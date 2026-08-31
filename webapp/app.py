import copy
import json
import math
import secrets
import shutil
import time
import uuid
from pathlib import Path
from typing import Optional

from contextlib import asynccontextmanager

from fastapi import Cookie, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.routing import APIRoute
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import avatars, db, tasks
from .config import (
    ADMIN_COOKIE,
    admin_key,
    SESSION_IDLE_SECONDS,
    ALLOWED_STEPS,
    AUDIO_EXTS,
    CHUNK_SIZE,
    DEFAULT_STEPS,
    MAX_AUDIO_SIZE,
    MAX_VIDEO_SIZE,
    STORAGE,
    UNET_CKPT,
    VIDEO_EXTS,
    WEBAPP_DIR,
    ensure_dirs,
)
from .progress import (
    chunks_from_duration,
    estimate_seconds,
    probe_duration,
)
from .media import ensure_preview as write_preview_mp4
from .prefix import cookie_path, public_prefix, split_public_path


class PublicPrefixMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in {"http", "websocket"}:
            path = scope.get("path") or "/"
            prefix, _inner = split_public_path(path)
            if prefix:
                existing = (scope.get("root_path") or "").rstrip("/")
                scope["root_path"] = existing + prefix
                if path.rstrip("/") == prefix and not path.endswith("/"):
                    scope["path"] = prefix + "/"
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # API process: no worker start/stop, never kill inference.
    ensure_dirs()
    db.init_db()
    avatars.purge_stale_uploads(force=True)
    yield


app = FastAPI(
    title="QeMix数字人平台",
    description="形象管理与口型合成接口。个人库素材和成片不可通过分享或裸访问 URL 打开。",
    version="1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
    openapi_tags=[
        {"name": "形象", "description": "公共形象与个人形象"},
        {"name": "作品", "description": "口型合成任务"},
        {"name": "系统", "description": "服务状态"},
        {"name": "上传", "description": "通用分片上传"},
    ],
    swagger_ui_parameters={"docExpansion": "list", "defaultModelsExpandDepth": -1},
    generate_unique_id_function=lambda route: route.summary or route.name,
)
app.add_middleware(PublicPrefixMiddleware)
ADMIN_DIR = WEBAPP_DIR / "admin"
_BASE_JS = (ADMIN_DIR / "assets" / "base.js").read_text(encoding="utf-8")
_SESSIONS: dict[str, float] = {}


def _purge_sessions() -> None:
    now = time.time()
    expired = [token for token, last in _SESSIONS.items() if now - last > SESSION_IDLE_SECONDS]
    for token in expired:
        _SESSIONS.pop(token, None)


def _session_ok(token: Optional[str], touch: bool = False) -> bool:
    _purge_sessions()
    if not token or token not in _SESSIONS:
        return False
    if touch:
        _SESSIONS[token] = time.time()
    return True


def _set_session_cookie(response: Response, token: str, path: str = "/") -> None:
    response.set_cookie(
        ADMIN_COOKIE,
        token,
        httponly=True,
        samesite="lax",
        path=path,
        max_age=SESSION_IDLE_SECONDS,
    )


def ok(data=None, msg: str = "ok"):
    return {"code": 0, "msg": msg, "success": True, "data": data}


def fail(msg: str, code: int = 1):
    return JSONResponse({"code": code, "msg": msg, "success": False, "data": None})


def _is_admin(request: Optional[Request]) -> bool:
    if request is None:
        return False
    return _session_ok(request.cookies.get(ADMIN_COOKIE), touch=False)


def _owner_of(user_id: Optional[str] = None, username: Optional[str] = None) -> Optional[str]:
    try:
        return avatars.normalize_user_id(user_id) or avatars.normalize_user_id(username)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _owner_from_headers(request: Request) -> Optional[str]:
    return _owner_of(
        request.headers.get("x-user-id"),
        request.headers.get("x-username"),
    )


def _file_owner_ok(request: Request, owner: Optional[str]) -> bool:
    """Private media bytes: admin session cookie, or X-User-Id / X-Username header.

    Query-string user_id is shareable with the URL and must never unlock files.
    """
    if _is_admin(request):
        return True
    viewer = _owner_from_headers(request)
    return bool(owner and viewer and viewer == owner)


def _load_character_row(character_id: str) -> dict:
    with db.db() as conn:
        row = conn.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "形象不存在")
    return dict(row)


def _visible_character(
    character_id: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
) -> dict:
    item = _load_character_row(character_id)
    owner = _owner_of(user_id, username)
    if not avatars.can_view(item, owner, admin=_is_admin(request)):
        raise HTTPException(404, "形象不存在")
    return item


def _visible_character_file(character_id: str, request: Request) -> dict:
    item = _load_character_row(character_id)
    if not avatars.is_private(item):
        return item
    if not _file_owner_ok(request, item.get("user_id")):
        raise HTTPException(404, "形象不存在")
    return item


def _send_media(
    path: Path,
    media_type: str,
    filename: Optional[str] = None,
    *,
    private: bool = False,
    extra_headers: Optional[dict] = None,
) -> FileResponse:
    headers = dict(extra_headers or {})
    if private:
        headers["Cache-Control"] = "private, no-store"
        headers["Pragma"] = "no-cache"
        headers["X-Content-Type-Options"] = "nosniff"
    return FileResponse(
        path,
        media_type=media_type,
        filename=filename,
        headers=headers or None,
    )


def _assert_character_usable(char: dict, owner: Optional[str]) -> None:
    if (char.get("type") or "public") != "private":
        return
    if not owner or owner != (char.get("user_id") or ""):
        raise HTTPException(404, "形象不存在")


class UploadInitBody(BaseModel):
    filename: str = Field(..., title="文件名")
    size: int = Field(..., title="文件大小")
    mime: Optional[str] = Field(None, title="MIME类型")
    kind: str = Field(..., title="文件种类", pattern="^(video|audio)$")


class CharacterCreateBody(BaseModel):
    name: str = Field(..., title="形象名称")
    video_upload_id: str = Field(..., title="视频上传ID")
    type: str = Field("public", title="形象类型")
    user_id: Optional[str] = Field(None, title="用户ID")
    username: Optional[str] = Field(None, title="用户ID（兼容字段）")


class LoginBody(BaseModel):
    key: str = Field(..., title="访问密钥")


class JobCreateBody(BaseModel):
    character_id: str = Field(..., title="形象ID")
    audio_upload_id: str = Field(..., title="音频上传ID")
    steps: Optional[int] = Field(None, title="合成步数")
    username: Optional[str] = Field(None, title="用户ID")
    task_name: Optional[str] = Field(None, title="作品名称")


def _safe_name(name: str) -> str:
    cleaned = "".join(ch for ch in Path(name).name if ch not in '\\/:*?"<>|')
    return cleaned or "file"


def _ext(name: str) -> str:
    return Path(name).suffix.lower()


def _require_upload_id(upload_id: str) -> str:
    try:
        return avatars.parse_upload_id(upload_id)
    except FileNotFoundError:
        raise HTTPException(404, "上传任务不存在") from None


@app.post("/api/uploads")
def create_upload(body: UploadInitBody):
    ext = _ext(body.filename)
    if body.kind == "video":
        if ext not in VIDEO_EXTS:
            raise HTTPException(400, f"不支持的视频格式：{ext or '未知'}")
        if body.size <= 0 or body.size > MAX_VIDEO_SIZE:
            raise HTTPException(400, "视频大小需在 1B–2GB 之间")
    else:
        if ext not in AUDIO_EXTS:
            raise HTTPException(400, f"不支持的音频格式：{ext or '未知'}")
        if body.size <= 0 or body.size > MAX_AUDIO_SIZE:
            raise HTTPException(400, "音频大小需在 1B–300MB 之间")

    upload_id = uuid.uuid4().hex
    total_chunks = max(1, math.ceil(body.size / CHUNK_SIZE))
    avatars.purge_stale_uploads()
    chunk_dir = STORAGE / "chunks" / upload_id
    chunk_dir.mkdir(parents=True, exist_ok=True)
    with db.db() as conn:
        conn.execute(
            """
            INSERT INTO uploads (id, kind, filename, mime, size, chunk_size, total_chunks, received, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, '[]', 'uploading', ?)
            """,
            (
                upload_id,
                body.kind,
                _safe_name(body.filename),
                body.mime,
                body.size,
                CHUNK_SIZE,
                total_chunks,
                db.utcnow(),
            ),
        )
    return {
        "upload_id": upload_id,
        "chunk_size": CHUNK_SIZE,
        "total_chunks": total_chunks,
        "received": [],
    }


@app.get("/api/uploads/{upload_id}")
def get_upload(upload_id: str):
    upload_id = _require_upload_id(upload_id)
    with db.db() as conn:
        row = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "上传任务不存在")
    data = dict(row)
    data["received"] = json.loads(data["received"] or "[]")
    return data


@app.put("/api/uploads/{upload_id}/chunks/{index}")
async def put_chunk(upload_id: str, index: int, request: Request):
    upload_id = _require_upload_id(upload_id)
    with db.db() as conn:
        row = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "上传任务不存在")
        upload = dict(row)
        if upload["status"] not in ("uploading",):
            raise HTTPException(400, "该上传已结束")
        if index < 0 or index >= upload["total_chunks"]:
            raise HTTPException(400, "分片序号无效")

    data = await request.body()
    if not data:
        raise HTTPException(400, "空分片")
    if len(data) > upload["chunk_size"] + 4096:
        raise HTTPException(400, "分片过大")
    is_last = index == upload["total_chunks"] - 1
    expected = upload["size"] - upload["chunk_size"] * (upload["total_chunks"] - 1) if is_last else upload["chunk_size"]
    if is_last and len(data) != expected:
        raise HTTPException(400, f"最后分片大小应为 {expected} 字节")
    if not is_last and len(data) != upload["chunk_size"]:
        raise HTTPException(400, f"分片大小应为 {upload['chunk_size']} 字节")

    chunk_path = STORAGE / "chunks" / upload_id / f"{index:06d}.part"
    try:
        chunk_path.write_bytes(data)
    except FileNotFoundError:
        avatars.discard_upload_chunks(upload_id)
        raise HTTPException(404, "上传任务不存在") from None

    with db.db() as conn:
        row = conn.execute("SELECT received FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        if row is None:
            avatars.discard_upload_chunks(upload_id)
            raise HTTPException(404, "上传任务不存在")
        received = set(json.loads(row["received"] or "[]"))
        received.add(index)
        conn.execute(
            "UPDATE uploads SET received = ? WHERE id = ?",
            (json.dumps(sorted(received)), upload_id),
        )
    return {"ok": True, "index": index, "received": len(received), "total_chunks": upload["total_chunks"]}


@app.post("/api/uploads/{upload_id}/complete")
def complete_upload(upload_id: str):
    upload_id = _require_upload_id(upload_id)
    with db.db() as conn:
        row = conn.execute("SELECT * FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "上传任务不存在")
        upload = dict(row)
        received = json.loads(upload["received"] or "[]")
        if len(received) != upload["total_chunks"] or sorted(received) != list(range(upload["total_chunks"])):
            missing = [i for i in range(upload["total_chunks"]) if i not in received]
            raise HTTPException(400, f"分片不完整，缺少 {len(missing)} 片")

        dest_dir = STORAGE / "uploads" / upload_id
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / upload["filename"]
        chunk_dir = STORAGE / "chunks" / upload_id
        written = 0
        try:
            with dest.open("wb") as out:
                for i in range(upload["total_chunks"]):
                    part = chunk_dir / f"{i:06d}.part"
                    if not part.exists():
                        raise HTTPException(400, f"分片 {i} 文件丢失")
                    written += out.write(part.read_bytes())
            if written != upload["size"]:
                raise HTTPException(400, "合并后文件大小与声明不一致")
        except HTTPException:
            dest.unlink(missing_ok=True)
            shutil.rmtree(dest_dir, ignore_errors=True)
            try:
                avatars.discard_upload_chunks(upload_id)
            except FileNotFoundError:
                shutil.rmtree(chunk_dir, ignore_errors=True)
            raise
        shutil.rmtree(chunk_dir, ignore_errors=True)
        conn.execute(
            "UPDATE uploads SET status = ?, path = ? WHERE id = ?",
            ("ready", str(dest), upload_id),
        )
    return {"ok": True, "upload_id": upload_id, "path": str(dest), "size": written}


@app.delete("/api/uploads/{upload_id}")
def abort_generic_upload(upload_id: str):
    upload_id = _require_upload_id(upload_id)
    with db.db() as conn:
        row = conn.execute("SELECT status FROM uploads WHERE id = ?", (upload_id,)).fetchone()
        if row is None:
            avatars.discard_upload_chunks(upload_id)
            return {"ok": True, "upload_id": upload_id}
        if dict(row).get("status") == "ready":
            raise HTTPException(400, "该上传已结束")
    avatars.abort_upload(upload_id)
    return {"ok": True, "upload_id": upload_id}


@app.get("/api/characters")
def list_characters(
    type: Optional[str] = None,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
):
    avatar_type = None
    if type:
        try:
            avatar_type = avatars.normalize_type(type)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    try:
        with db.db() as conn:
            rows = avatars.list_characters(conn, avatar_type, user_id, username)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return rows


@app.get("/api/characters/{character_id}")
def get_character(
    character_id: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
):
    return avatars.public_character(_visible_character(character_id, request, user_id, username))


@app.post("/api/characters")
def create_character(body: CharacterCreateBody):
    name = body.name.strip()
    if not name:
        raise HTTPException(400, "请填写形象名称")
    try:
        avatar_type = avatars.normalize_type(body.type)
        owner = avatars.resolve_owner(avatar_type, body.user_id, body.username)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    with db.db() as conn:
        upload = conn.execute("SELECT * FROM uploads WHERE id = ?", (body.video_upload_id,)).fetchone()
        if upload is None:
            raise HTTPException(400, "视频上传不存在")
        upload = dict(upload)
        if upload["kind"] != "video" or upload["status"] != "ready" or not upload["path"]:
            raise HTTPException(400, "请先完成视频分片上传")
        source = Path(upload["path"])
        if not source.exists():
            raise HTTPException(400, "视频文件不存在")
        char_id = avatars.new_character_id(conn)
        conn.execute(
            """
            INSERT INTO characters (id, name, source_path, status, created_at, type, user_id)
            VALUES (?, ?, ?, 'queued', ?, ?, ?)
            """,
            (char_id, name, str(source), db.utcnow(), avatar_type, owner),
        )
    return {"id": char_id, "name": name, "status": "queued", "type": avatar_type, "user_id": owner}


@app.get("/api/characters/{character_id}/poster")
def character_poster(character_id: str, request: Request):
    row = _visible_character_file(character_id, request)
    if not row.get("poster_path") or not Path(row["poster_path"]).exists():
        raise HTTPException(404, "暂无封面")
    return _send_media(
        Path(row["poster_path"]),
        "image/jpeg",
        private=avatars.is_private(row),
    )


@app.get("/api/characters/{character_id}/video")
def character_video(character_id: str, request: Request):
    row = _visible_character_file(character_id, request)
    preview = Path(row["preview_path"]) if row.get("preview_path") else None
    full = Path(row["video_path"]) if row.get("video_path") else None
    path = preview if preview and preview.exists() else None
    if path is None or not path.exists():
        raise HTTPException(404, "形象视频不存在")
    return _send_media(
        path,
        "video/mp4",
        filename=f"{character_id}.mp4",
        private=avatars.is_private(row),
    )


@app.delete("/api/characters/{character_id}")
def delete_character(
    character_id: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
):
    _visible_character(character_id, request, user_id, username)
    with db.db() as conn:
        busy = conn.execute(
            "SELECT id FROM jobs WHERE character_id = ? AND status IN ('queued', 'running')",
            (character_id,),
        ).fetchone()
        if busy:
            raise HTTPException(400, "该形象还有排队或正在合成的任务")
        row = conn.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "形象不存在")
        source_path = dict(row).get("source_path")
        conn.execute("DELETE FROM characters WHERE id = ?", (character_id,))
    avatars.purge_character_files(character_id, source_path)
    return {"ok": True}


@app.get("/api/avatars")
def list_avatars(
    request: Request,
    type: Optional[str] = None,
    username: Optional[str] = None,
    user_id: Optional[str] = None,
    bake_status: Optional[str] = None,
    page: int = 1,
    page_size: int = 12,
):
    avatar_type = None
    if type and type != "all":
        try:
            avatar_type = avatars.normalize_type(type)
        except ValueError as exc:
            return fail(str(exc))
    try:
        owner = _owner_of(user_id, username)
        with db.db() as conn:
            rows = avatars.list_characters(conn, avatar_type, user_id, username, bake_status)
            total_counts, ready_counts = avatars.counts(
                conn, owner, include_all_private=_is_admin(request)
            )
    except ValueError as exc:
        return fail(str(exc))
    page_data = avatars.paginate(rows, page, page_size)
    page_data["items"] = [avatars.to_admin_avatar(r) for r in page_data["items"]]
    page_data["counts"] = total_counts
    page_data["ready_counts"] = ready_counts
    return ok(page_data)


@app.post("/api/avatars/upload")
async def upload_avatar(
    request: Request,
    stage: Optional[str] = Form(None),
    name: Optional[str] = Form(None),
    type: Optional[str] = Form("public"),
    username: Optional[str] = Form(None),
    user_id: Optional[str] = Form(None),
    filename: Optional[str] = Form(None),
    filesize: Optional[int] = Form(None),
    chunk_size: Optional[int] = Form(None),
    upload_id: Optional[str] = Form(None),
    chunk_index: Optional[int] = Form(None),
    total_chunks: Optional[int] = Form(None),
    chunk: Optional[UploadFile] = File(None),
):
    stage = (stage or request.query_params.get("stage") or "").strip().lower()
    upload_id = upload_id or request.query_params.get("upload_id")
    avatars.purge_stale_uploads()
    if stage == "init":
        display = (name or "").strip()
        if not display:
            return fail("请填写形象名称")
        if len(display) > 80:
            return fail("形象名称不能超过 80 个字符")
        try:
            avatar_type = avatars.normalize_type(type)
            owner = avatars.resolve_owner(avatar_type, user_id, username)
        except ValueError as exc:
            return fail(str(exc))
        ext = _ext(filename or "")
        if ext not in VIDEO_EXTS:
            return fail(f"不支持的视频格式：{ext or '未知'}")
        size = int(filesize or 0)
        if size <= 0 or size > MAX_VIDEO_SIZE:
            return fail("视频大小需在 1B–2GB 之间")
        uid = uuid.uuid4().hex
        piece = int(chunk_size or CHUNK_SIZE)
        if piece < 64 * 1024 or piece > 16 * 1024 * 1024:
            return fail("分片大小无效")
        total = max(1, math.ceil(size / piece))
        avatars.write_upload_meta(
            uid,
            {
                "name": display,
                "type": avatar_type,
                "user_id": owner,
                "filename": _safe_name(filename or f"video{ext}"),
                "size": size,
                "chunk_size": piece,
                "total_chunks": total,
                "received": [],
            },
            create=True,
        )
        return ok({"upload_id": uid, "total_chunks": total, "chunk_size": piece})

    if stage == "chunk":
        try:
            upload_id = avatars.parse_upload_id(upload_id)
        except FileNotFoundError:
            return fail("上传任务不存在")
        if chunk is None or chunk_index is None:
            return fail("分片参数不完整")
        try:
            meta = avatars.read_upload_meta(upload_id)
        except FileNotFoundError:
            return fail("上传任务不存在")
        index = int(chunk_index)
        total = int(meta["total_chunks"])
        if total_chunks is not None and int(total_chunks) != total:
            return fail("分片总数与初始化不一致")
        if index < 0 or index >= total:
            return fail("分片序号无效")
        data = await chunk.read()
        if not data:
            return fail("空分片")
        is_last = index == total - 1
        expected = meta["size"] - meta["chunk_size"] * (total - 1) if is_last else meta["chunk_size"]
        if is_last and len(data) != expected:
            return fail(f"最后分片大小应为 {expected} 字节")
        if not is_last and len(data) != meta["chunk_size"]:
            return fail(f"分片大小应为 {meta['chunk_size']} 字节")
        part = STORAGE / "chunks" / upload_id / f"{index:06d}.part"
        try:
            part.write_bytes(data)
            received = set(meta.get("received") or [])
            received.add(index)
            meta["received"] = sorted(received)
            avatars.write_upload_meta(upload_id, meta)
        except FileNotFoundError:
            avatars.discard_upload_chunks(upload_id)
            return fail("上传任务不存在")
        return ok({"index": index, "received": len(received), "total_chunks": total})

    if stage == "complete":
        try:
            upload_id = avatars.parse_upload_id(upload_id)
        except FileNotFoundError:
            return fail("缺少 upload_id" if not upload_id else "上传任务不存在")
        try:
            meta = avatars.read_upload_meta(upload_id)
        except FileNotFoundError:
            return fail("上传任务不存在")
        received = meta.get("received") or []
        total = int(meta["total_chunks"])
        if len(received) != total or sorted(received) != list(range(total)):
            return fail("分片不完整")
        dest_dir = STORAGE / "uploads" / upload_id
        dest = dest_dir / meta["filename"]
        try:
            avatars.merge_upload_chunks(upload_id, dest, total, int(meta["size"]))
        except (FileNotFoundError, ValueError) as exc:
            dest.unlink(missing_ok=True)
            shutil.rmtree(dest_dir, ignore_errors=True)
            avatars.discard_upload_chunks(upload_id)
            return fail(str(exc))
        try:
            with db.db() as conn:
                char_id = avatars.new_character_id(conn)
                conn.execute(
                    """
                    INSERT INTO characters (id, name, source_path, status, created_at, type, user_id)
                    VALUES (?, ?, ?, 'queued', ?, ?, ?)
                    """,
                    (char_id, meta["name"], str(dest), db.utcnow(), meta["type"], meta.get("user_id")),
                )
                row = conn.execute("SELECT * FROM characters WHERE id = ?", (char_id,)).fetchone()
        except Exception:
            dest.unlink(missing_ok=True)
            shutil.rmtree(dest_dir, ignore_errors=True)
            avatars.discard_upload_chunks(upload_id)
            return fail("创建形象失败")
        return ok(avatars.to_admin_avatar(dict(row)), "上传成功，正在处理")

    if stage == "abort":
        if not upload_id:
            return fail("缺少 upload_id")
        try:
            uid = avatars.parse_upload_id(upload_id)
            avatars.abort_upload(uid)
        except FileNotFoundError:
            return fail("上传任务不存在")
        return ok({"upload_id": uid}, "已取消并清理分片")

    return fail("未知的上传阶段")


@app.post("/api/avatars/{identifier}/rebake")
def rebake_avatar(
    identifier: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
):
    try:
        char = _visible_character(identifier, request, user_id, username)
    except HTTPException as exc:
        return fail(exc.detail if isinstance(exc.detail, str) else "形象不存在")
    for key in ("video_path", "preview_path"):
        path = Path(char[key]) if char.get(key) else None
        if path and path.exists():
            path.unlink(missing_ok=True)
    with db.db() as conn:
        conn.execute(
            """
            UPDATE characters
            SET status = ?, error = NULL, progress = ?, video_path = NULL, preview_path = NULL
            WHERE id = ?
            """,
            ("queued", "转码中", identifier),
        )
    return ok({"identifier": identifier}, "已重新排队转码")


@app.delete("/api/avatars/{identifier}")
def delete_avatar(
    identifier: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
):
    try:
        _visible_character(identifier, request, user_id, username)
    except HTTPException as exc:
        return fail(exc.detail if isinstance(exc.detail, str) else "形象不存在")
    with db.db() as conn:
        busy = conn.execute(
            "SELECT id FROM jobs WHERE character_id = ? AND status IN ('queued', 'running')",
            (identifier,),
        ).fetchone()
        if busy:
            return fail("该形象还有排队或正在合成的任务")
        row = conn.execute("SELECT source_path FROM characters WHERE id = ?", (identifier,)).fetchone()
        source_path = dict(row).get("source_path") if row else None
        conn.execute("DELETE FROM characters WHERE id = ?", (identifier,))
    avatars.purge_character_files(identifier, source_path)
    return ok({"identifier": identifier}, "已删除")


@app.get("/api/jobs")
def list_jobs(
    request: Request,
    username: Optional[str] = None,
    user_id: Optional[str] = None,
):
    owner = _owner_of(user_id, username)
    with db.db() as conn:
        items = tasks.filter_jobs(
            tasks.load_jobs(conn),
            username=owner,
            include_private=_is_admin(request) and not owner,
        )
    return JSONResponse(items, headers={"Cache-Control": "no-store"})


@app.post("/api/jobs")
def create_job(body: JobCreateBody):
    steps = DEFAULT_STEPS if body.steps is None else body.steps
    if steps not in ALLOWED_STEPS:
        raise HTTPException(400, f"步数只能是 {', '.join(map(str, ALLOWED_STEPS))}")
    with db.db() as conn:
        char = conn.execute("SELECT * FROM characters WHERE id = ?", (body.character_id,)).fetchone()
        if char is None:
            raise HTTPException(400, "形象不存在")
        char = dict(char)
        if char["status"] != "ready":
            raise HTTPException(400, "形象还在转码，请稍后再提交")
        owner = _owner_of(None, body.username)
        _assert_character_usable(char, owner)
        upload = conn.execute("SELECT * FROM uploads WHERE id = ?", (body.audio_upload_id,)).fetchone()
        if upload is None:
            raise HTTPException(400, "音频上传不存在")
        upload = dict(upload)
        if upload["kind"] != "audio" or upload["status"] != "ready" or not upload["path"]:
            raise HTTPException(400, "请先完成音频分片上传")
        src = Path(upload["path"])
        if not src.exists():
            raise HTTPException(400, "音频文件不存在")
        job_id = uuid.uuid4().hex
        job_dir = STORAGE / "jobs" / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        audio_path = job_dir / f"audio{Path(upload['filename']).suffix.lower()}"
        shutil.copy2(src, audio_path)
        duration = probe_duration(str(audio_path))
        total_chunks = chunks_from_duration(duration)
        estimated = estimate_seconds(steps, duration)
        conn.execute(
            """
            INSERT INTO jobs (
                id, character_id, audio_path, audio_name, steps, status, progress, created_at,
                audio_duration, total_chunks, estimated_seconds, remaining_seconds, stage, progress_percent,
                username, task_name
            )
            VALUES (?, ?, ?, ?, ?, 'queued', '排队中', ?, ?, ?, ?, ?, 'queued', 0, ?, ?)
            """,
            (
                job_id,
                body.character_id,
                str(audio_path),
                upload["filename"],
                steps,
                db.utcnow(),
                duration,
                total_chunks,
                estimated,
                estimated,
                (body.username or "").strip() or None,
                (body.task_name or "").strip() or None,
            ),
        )
    jobs = _load_jobs()
    created = next((j for j in jobs if j["id"] == job_id), None)
    return JSONResponse(created or {"id": job_id, "status": "queued", "steps": steps}, headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}")
def get_job(
    job_id: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
):
    job = _visible_job(job_id, request, user_id, username)
    return JSONResponse(job, headers={"Cache-Control": "no-store"})


def _load_jobs() -> list[dict]:
    with db.db() as conn:
        return tasks.load_jobs(conn)


def _find_job(job_id: str) -> dict:
    with db.db() as conn:
        jobs = [j for j in tasks.load_jobs(conn) if j["id"] == job_id]
    if not jobs:
        raise HTTPException(404, "任务不存在")
    return jobs[0]


def _visible_job(
    job_id: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
) -> dict:
    job = _find_job(job_id)
    owner = _owner_of(user_id, username)
    if not tasks.can_view_job(job, owner, admin=_is_admin(request)):
        raise HTTPException(404, "任务不存在")
    return job


def _visible_job_file(job_id: str, request: Request) -> dict:
    job = _find_job(job_id)
    if not tasks.job_is_private(job):
        return job
    owner = (job.get("username") or job.get("character_user_id") or "").strip() or None
    if not _file_owner_ok(request, owner):
        raise HTTPException(404, "任务不存在")
    return job


def _admin_tasks(
    request: Optional[Request] = None,
    username: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
) -> list[dict]:
    owner = username.strip() if username else None
    with db.db() as conn:
        jobs = tasks.filter_jobs(
            tasks.load_jobs(conn),
            username=owner,
            status=status,
            keyword=keyword,
            include_private=_is_admin(request) and not owner,
        )
        chars = tasks.character_map(conn)
    visible = []
    for job in jobs:
        char = chars.get(job.get("character_id"))
        if char and not avatars.can_view(char, owner, admin=_is_admin(request)):
            continue
        visible.append(tasks.to_admin_task(job, char))
    return visible


def _job_output(job: dict) -> Path:
    if job.get("status") != "done" or not job.get("output_path"):
        raise HTTPException(400, "任务尚未合成完成")
    path = Path(job["output_path"])
    if not path.exists():
        raise HTTPException(404, "成片文件不存在")
    return path


def _job_preview(job: dict) -> Path:
    output = _job_output(job)
    preview = Path(job["preview_path"]) if job.get("preview_path") else output.parent / "preview.mp4"
    try:
        path = write_preview_mp4(output, preview)
        if job.get("preview_path") != str(path):
            with db.db() as conn:
                conn.execute("UPDATE jobs SET preview_path = ? WHERE id = ?", (str(path), job["id"]))
        return path
    except Exception:
        return output


def _requeue_job(job: dict) -> None:
    if job["status"] == "running":
        raise HTTPException(400, "正在合成的任务不能重试")
    if not Path(job["audio_path"]).exists():
        raise HTTPException(400, "音频文件已丢失，无法重试")
    job_dir = STORAGE / "jobs" / job["id"]
    for name in ("output.mp4", "preview.mp4"):
        (job_dir / name).unlink(missing_ok=True)
    duration = job.get("audio_duration") or probe_duration(job["audio_path"])
    estimated = estimate_seconds(int(job.get("steps") or DEFAULT_STEPS), duration)
    with db.db() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, error = NULL, progress = ?, output_path = NULL, preview_path = NULL,
                started_at = NULL, finished_at = NULL, stage = ?, progress_percent = 0,
                remaining_seconds = ?, estimated_seconds = ?, current_chunk = NULL,
                infer_started_at = NULL, tqdm_remaining = NULL
            WHERE id = ?
            """,
            ("queued", "排队中", "queued", estimated, estimated, job["id"]),
        )


def _job_video(job_id: str) -> Path:
    return _job_output(_find_job(job_id))


@app.get("/api/jobs/{job_id}/preview")
def preview_job(job_id: str, request: Request):
    job = _visible_job_file(job_id, request)
    path = _job_preview(job)
    return _send_media(
        path,
        "video/mp4",
        filename=path.name,
        private=tasks.job_is_private(job),
    )


@app.get("/api/jobs/{job_id}/download")
def download_job(job_id: str, request: Request):
    job = _visible_job_file(job_id, request)
    path = _job_output(job)
    return _send_media(
        path,
        "video/mp4",
        filename=f"{job_id}.mp4",
        private=tasks.job_is_private(job),
        extra_headers={"Content-Disposition": f'attachment; filename="{job_id}.mp4"'},
    )


@app.post("/api/jobs/{job_id}/retry")
def retry_job(
    job_id: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
):
    job = _visible_job(job_id, request, user_id, username)
    try:
        _requeue_job(job)
    except HTTPException as exc:
        raise exc
    return {"ok": True, "id": job_id, "status": "queued"}


@app.delete("/api/jobs/{job_id}")
def delete_job(
    job_id: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
):
    _visible_job(job_id, request, user_id, username)
    with db.db() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "任务不存在")
        if row["status"] == "running":
            raise HTTPException(400, "正在合成的任务不能删除")
        conn.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
    shutil.rmtree(STORAGE / "jobs" / job_id, ignore_errors=True)
    return {"ok": True}


@app.get("/api/health")
def health(request: Request):
    from .gpu_runtime import gpu_snapshot, inference_running, worker_alive

    with db.db() as conn:
        queued = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status = 'queued'").fetchone()["n"]
        running = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status = 'running'").fetchone()["n"]
    jobs = _load_jobs()
    current = next((j for j in jobs if j.get("status") == "running"), None)
    if current and tasks.job_is_private(current) and not _is_admin(request):
        current = {"id": current["id"], "status": "running"}
    return JSONResponse(
        {
            "ok": True,
            "queued": queued,
            "running": running,
            "gpu_busy": inference_running(),
            "gpus": gpu_snapshot(),
            "worker_alive": worker_alive(),
            "current_job": current,
        },
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/system/ready")
def system_ready():
    from .gpu_runtime import gpu_snapshot, inference_running, worker_alive

    ffmpeg_ok = shutil.which("ffmpeg") is not None
    model_ok = UNET_CKPT.exists()
    return ok(
        {
            "ready": ffmpeg_ok and model_ok,
            "checks": {
                "ffmpeg": ffmpeg_ok,
                "gpu": True,
                "model": model_ok,
            },
            "gpu_busy": inference_running(),
            "gpus": gpu_snapshot(),
            "worker_alive": worker_alive(),
        }
    )


@app.get("/api/system/status")
def system_status(request: Request):
    from .gpu_runtime import gpu_snapshot, inference_running, worker_alive

    with db.db() as conn:
        baking = conn.execute(
            "SELECT COUNT(*) AS n FROM characters WHERE status IN ('queued', 'preparing', 'aligning')"
        ).fetchone()["n"]
        queued = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status = 'queued'").fetchone()["n"]
        running = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status = 'running'").fetchone()["n"]
        done = conn.execute("SELECT COUNT(*) AS n FROM jobs WHERE status = 'done'").fetchone()["n"]
        total_counts, _ready = avatars.counts(conn, include_all_private=_is_admin(request))
    return ok(
        {
            "baking": baking,
            "queued": queued,
            "running": running,
            "gpu_busy": inference_running(),
            "gpus": gpu_snapshot(),
            "worker_alive": worker_alive(),
            "avatars": {
                "public": total_counts["public"],
                "private": total_counts["private"],
                "total": total_counts["public"] + total_counts["private"],
            },
            "tasks": {"done": done, "run": running, "wait": queued},
            "features": {"max_synthesis_duration_seconds": 0},
        }
    )


@app.get("/api/tasks")
def list_tasks(
    request: Request,
    page: int = 1,
    page_size: int = 12,
    username: Optional[str] = None,
    user_id: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
):
    items = _admin_tasks(request, username or user_id, status, keyword)
    page_data = avatars.paginate(items, page, page_size)
    return ok(
        {
            "tasks": page_data["items"],
            "total": page_data["total"],
            "page": page_data["page"],
            "pages": page_data["pages"],
            "page_size": page_data["page_size"],
        }
    )


@app.post("/api/tasks/create")
async def create_task(
    avatar_identifier: str = Form(..., title="形象ID"),
    username: str = Form(..., title="用户ID"),
    task_name: str = Form(..., title="作品名称"),
    audio: UploadFile = File(..., title="驱动音频"),
    steps: int = Form(DEFAULT_STEPS, title="合成质量步数"),
):
    name = (task_name or "").strip()
    owner = (username or "").strip()
    if not name:
        return fail("请填写作品名称")
    if not owner:
        return fail("请填写用户ID")
    if steps not in ALLOWED_STEPS:
        return fail(f"步数只能是 {', '.join(map(str, ALLOWED_STEPS))}")
    ext = _ext(audio.filename or "")
    if ext not in AUDIO_EXTS:
        return fail(f"不支持的音频格式：{ext or '未知'}")
    with db.db() as conn:
        char = conn.execute("SELECT * FROM characters WHERE id = ?", (avatar_identifier,)).fetchone()
        if char is None:
            return fail("形象不存在")
        char = dict(char)
        if char["status"] != "ready" or not char.get("video_path"):
            return fail("形象还在转码，请稍后再提交")
        try:
            _assert_character_usable(char, avatars.normalize_user_id(owner))
        except HTTPException:
            return fail("形象不存在")
        except ValueError as exc:
            return fail(str(exc))
    data = await audio.read()
    if not data:
        return fail("音频文件为空")
    if len(data) > MAX_AUDIO_SIZE:
        return fail("音频不能超过 300MB")
    job_id = uuid.uuid4().hex
    job_dir = STORAGE / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    audio_path = job_dir / f"audio{ext or '.wav'}"
    audio_path.write_bytes(data)
    duration = probe_duration(str(audio_path))
    total_chunks = chunks_from_duration(duration)
    estimated = estimate_seconds(steps, duration)
    with db.db() as conn:
        conn.execute(
            """
            INSERT INTO jobs (
                id, character_id, audio_path, audio_name, steps, status, progress, created_at,
                audio_duration, total_chunks, estimated_seconds, remaining_seconds, stage, progress_percent,
                username, task_name
            )
            VALUES (?, ?, ?, ?, ?, 'queued', '排队中', ?, ?, ?, ?, ?, 'queued', 0, ?, ?)
            """,
            (
                job_id,
                avatar_identifier,
                str(audio_path),
                _safe_name(audio.filename or audio_path.name),
                steps,
                db.utcnow(),
                duration,
                total_chunks,
                estimated,
                estimated,
                owner,
                name,
            ),
        )
    created = next((t for t in _admin_tasks(username=owner) if t["task_id"] == job_id), None)
    return ok(created, "已加入合成队列")


@app.get("/api/tasks/{task_id}")
def get_task(
    task_id: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
):
    try:
        job = _visible_job(task_id, request, user_id, username)
    except HTTPException:
        return fail("任务不存在")
    with db.db() as conn:
        chars = tasks.character_map(conn)
    return ok(tasks.to_admin_task(job, chars.get(job.get("character_id"))))


@app.post("/api/tasks/{task_id}/retry")
def retry_task(
    task_id: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
):
    try:
        job = _visible_job(task_id, request, user_id, username)
        _requeue_job(job)
    except HTTPException as exc:
        return fail(exc.detail if isinstance(exc.detail, str) else "无法重试")
    return ok({"task_id": task_id}, "已重新排队")


@app.get("/api/tasks/{task_id}/preview")
def preview_task(task_id: str, request: Request):
    job = _visible_job_file(task_id, request)
    path = _job_preview(job)
    return _send_media(
        path,
        "video/mp4",
        filename=path.name,
        private=tasks.job_is_private(job),
    )


@app.get("/api/tasks/{task_id}/download")
def download_task(task_id: str, request: Request):
    job = _visible_job_file(task_id, request)
    path = _job_output(job)
    return _send_media(
        path,
        "video/mp4",
        filename=f"{task_id}.mp4",
        private=tasks.job_is_private(job),
        extra_headers={"Content-Disposition": f'attachment; filename="{task_id}.mp4"'},
    )


@app.delete("/api/tasks/{task_id}")
def delete_task(
    task_id: str,
    request: Request,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
):
    try:
        _visible_job(task_id, request, user_id, username)
    except HTTPException:
        return fail("任务不存在")
    with db.db() as conn:
        row = conn.execute("SELECT status FROM jobs WHERE id = ?", (task_id,)).fetchone()
        if row is None:
            return fail("任务不存在")
        if row["status"] == "running":
            return fail("正在合成的任务不能删除")
        conn.execute("DELETE FROM jobs WHERE id = ?", (task_id,))
    shutil.rmtree(STORAGE / "jobs" / task_id, ignore_errors=True)
    return ok({"task_id": task_id}, "已删除")


@app.get("/api/admin/session")
def admin_session(qemix_gate: Optional[str] = Cookie(None)):
    valid = _session_ok(qemix_gate, touch=False)
    return ok(
        {
            "enabled": True,
            "ok": valid,
            "idle_seconds": SESSION_IDLE_SECONDS,
        }
    )


@app.post("/api/admin/heartbeat")
def admin_heartbeat(request: Request, response: Response, qemix_gate: Optional[str] = Cookie(None)):
    path = cookie_path(request)
    if not _session_ok(qemix_gate, touch=True):
        response.delete_cookie(ADMIN_COOKIE, path=path)
        return fail("会话已过期，请重新登录")
    _set_session_cookie(response, qemix_gate, path=path)
    return ok({"ok": True, "idle_seconds": SESSION_IDLE_SECONDS})


@app.post("/api/admin/login")
def admin_login(body: LoginBody, request: Request, response: Response):
    if (body.key or "").strip() != admin_key():
        return fail("密钥错误")
    token = secrets.token_hex(16)
    _SESSIONS[token] = time.time()
    _set_session_cookie(response, token, path=cookie_path(request))
    return ok({"ok": True, "idle_seconds": SESSION_IDLE_SECONDS}, "已登录")


@app.post("/api/admin/logout")
def admin_logout(request: Request, response: Response, qemix_gate: Optional[str] = Cookie(None)):
    if qemix_gate:
        _SESSIONS.pop(qemix_gate, None)
    response.delete_cookie(ADMIN_COOKIE, path=cookie_path(request))
    return ok({"ok": True}, "已退出")


_HTML_NO_STORE = {
    "Cache-Control": "no-store, no-cache, must-revalidate",
    "Pragma": "no-cache",
}


def _asset_query(name: str) -> str:
    path = ADMIN_DIR / "assets" / name
    try:
        return str(int(path.stat().st_mtime))
    except OSError:
        return "1"


def _inject_base(html: str) -> str:
    script = f"<script>{_BASE_JS}</script>"
    for name in ("vendor/animate.min.css", "style.css", "app.js", "fx.js"):
        token = f"admin/assets/{name}"
        html = html.replace(token, f"{token}?v={_asset_query(name)}", 1)
    if "<!--APP_BASE-->" in html:
        return html.replace("<!--APP_BASE-->", script, 1)
    return script + html


def _html_page(content: str) -> HTMLResponse:
    return HTMLResponse(content, headers=_HTML_NO_STORE)


def _page(name: str) -> HTMLResponse:
    html = ADMIN_DIR / name
    if not html.exists():
        raise HTTPException(404, "页面缺失")
    return _html_page(_inject_base(html.read_text(encoding="utf-8")))


@app.get("/login.html", response_class=HTMLResponse, include_in_schema=False)
def login_page():
    return _page("login.html")


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def index():
    html = ADMIN_DIR / "index.html"
    if not html.exists():
        raise HTTPException(500, "前端页面缺失")
    return _html_page(_inject_base(html.read_text(encoding="utf-8")))


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
@app.get("/admin/", response_class=HTMLResponse, include_in_schema=False)
def admin_home():
    return index()


@app.get("/favicon.svg", include_in_schema=False)
def favicon_svg():
    path = ADMIN_DIR / "assets" / "favicon.svg"
    if not path.exists():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/svg+xml")


@app.get("/openapi.json", include_in_schema=False)
def openapi_json(request: Request):
    schema = copy.deepcopy(app.openapi())
    prefix = public_prefix(request) or "/"
    schema["servers"] = [{"url": prefix, "description": "QeMixAvatar"}]
    return JSONResponse(schema)


_SWAGGER_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>QeMixAvatar API</title>
  <!--APP_BASE-->
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css">
</head>
<body>
  <div id="swagger-ui"></div>
  <script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"></script>
  <script>
    const prefix = window.APP_BASE || '';
    fetch((window.apiUrl ? window.apiUrl('/openapi.json') : (prefix + '/openapi.json')))
      .then((r) => r.json())
      .then((spec) => {
        spec.servers = [{ url: prefix || '/', description: 'QeMixAvatar' }];
        SwaggerUIBundle({
          spec: spec,
          dom_id: '#swagger-ui',
          docExpansion: 'list',
          defaultModelsExpandDepth: -1,
          persistAuthorization: true,
        });
      });
  </script>
</body>
</html>
"""

_REDOC_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8"/>
  <title>QeMixAvatar API</title>
  <!--APP_BASE-->
  <style>body { margin: 0; padding: 0; }</style>
</head>
<body>
  <div id="redoc"></div>
  <script src="https://cdn.jsdelivr.net/npm/redoc@2/bundles/redoc.standalone.js"></script>
  <script>
    const prefix = window.APP_BASE || '';
    fetch((window.apiUrl ? window.apiUrl('/openapi.json') : (prefix + '/openapi.json')))
      .then((r) => r.json())
      .then((spec) => {
        spec.servers = [{ url: prefix || '/', description: 'QeMixAvatar' }];
        Redoc.init(spec, {}, document.getElementById('redoc'));
      });
  </script>
</body>
</html>
"""


@app.get("/docs", include_in_schema=False)
@app.get("/docs/", include_in_schema=False)
def swagger_docs():
    return HTMLResponse(_inject_base(_SWAGGER_HTML))


@app.get("/redoc", include_in_schema=False)
@app.get("/redoc/", include_in_schema=False)
def redoc_docs():
    return HTMLResponse(_inject_base(_REDOC_HTML))


_CN_DOCS = {
    ("POST", "/api/uploads"): ("创建分片上传", "上传"),
    ("GET", "/api/uploads/{upload_id}"): ("查询分片上传", "上传"),
    ("PUT", "/api/uploads/{upload_id}/chunks/{index}"): ("上传一个分片", "上传"),
    ("POST", "/api/uploads/{upload_id}/complete"): ("合并分片文件", "上传"),
    ("DELETE", "/api/uploads/{upload_id}"): ("取消分片上传并清理", "上传"),
    ("GET", "/api/characters"): ("列出形象", "形象"),
    ("POST", "/api/characters"): ("创建形象", "形象"),
    ("GET", "/api/characters/{character_id}"): ("查询形象", "形象"),
    ("DELETE", "/api/characters/{character_id}"): ("删除形象", "形象"),
    ("GET", "/api/characters/{character_id}/poster"): ("获取形象封面", "形象"),
    ("GET", "/api/characters/{character_id}/video"): ("获取形象视频", "形象"),
    ("GET", "/api/avatars"): ("分页列出形象", "形象"),
    ("POST", "/api/avatars/upload"): ("分片/批量上传形象", "形象"),
    ("POST", "/api/avatars/{identifier}/rebake"): ("重新转码形象", "形象"),
    ("DELETE", "/api/avatars/{identifier}"): ("按标识删除形象", "形象"),
    ("GET", "/api/jobs"): ("列出合成任务", "作品"),
    ("POST", "/api/jobs"): ("提交合成任务", "作品"),
    ("GET", "/api/jobs/{job_id}"): ("查询合成任务", "作品"),
    ("DELETE", "/api/jobs/{job_id}"): ("删除合成任务", "作品"),
    ("GET", "/api/jobs/{job_id}/preview"): ("预览成片", "作品"),
    ("GET", "/api/jobs/{job_id}/download"): ("下载成片", "作品"),
    ("POST", "/api/jobs/{job_id}/retry"): ("重试合成任务", "作品"),
    ("GET", "/api/tasks"): ("分页列出作品", "作品"),
    ("POST", "/api/tasks/create"): ("创建合成作品", "作品"),
    ("GET", "/api/tasks/{task_id}"): ("查询作品", "作品"),
    ("DELETE", "/api/tasks/{task_id}"): ("删除作品", "作品"),
    ("POST", "/api/tasks/{task_id}/retry"): ("重试合成", "作品"),
    ("GET", "/api/tasks/{task_id}/preview"): ("预览作品成片", "作品"),
    ("GET", "/api/tasks/{task_id}/download"): ("下载作品成片", "作品"),
    ("GET", "/api/health"): ("简易健康检查", "系统"),
    ("GET", "/api/system/ready"): ("检查服务是否就绪", "系统"),
    ("GET", "/api/system/status"): ("查询系统状态", "系统"),
    ("GET", "/api/admin/session"): ("查询访问会话", "系统"),
    ("POST", "/api/admin/heartbeat"): ("刷新访问会话", "系统"),
    ("POST", "/api/admin/login"): ("访问密钥登录", "系统"),
    ("POST", "/api/admin/logout"): ("退出登录", "系统"),
}

for _route in app.routes:
    if not isinstance(_route, APIRoute):
        continue
    if not _route.path.startswith("/api/"):
        _route.include_in_schema = False
        continue
    _methods = [m for m in (_route.methods or set()) if m not in {"HEAD", "OPTIONS"}]
    for _method in _methods:
        _spec = _CN_DOCS.get((_method, _route.path))
        if not _spec:
            continue
        _route.summary = _spec[0]
        _route.tags = [_spec[1]]
        _route.name = _spec[0]
        _route.operation_id = _spec[0]


app.mount("/admin/assets", StaticFiles(directory=str(ADMIN_DIR / "assets")), name="admin-assets")
