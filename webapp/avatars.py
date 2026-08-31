import json
import math
import re
import secrets
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .config import STORAGE

UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")
CHUNK_TTL_SECONDS = 24 * 3600
_PURGE_MIN_INTERVAL = 60
_last_purge_at = 0.0

AVATAR_TYPES = ("public", "private")
CHAR_ID_LEN = 8


def new_character_id(conn) -> str:
    for _ in range(64):
        cid = secrets.token_hex(CHAR_ID_LEN // 2)
        if conn.execute("SELECT 1 FROM characters WHERE id = ?", (cid,)).fetchone() is None:
            return cid
    raise RuntimeError("无法生成形象ID")


BAKE_MAP = {
    "ready": "ready",
    "queued": "processing",
    "preparing": "processing",
    "aligning": "processing",
    "failed": "error",
}


def normalize_type(raw: Optional[str], default: str = "public") -> str:
    value = (raw or default).strip().lower()
    if value in {"personal", "user", "private"}:
        return "private"
    if value in {"public", "shared", "common"}:
        return "public"
    raise ValueError("形象类型只能是 public（公共）或 private（个人）")


def normalize_user_id(raw: Optional[str]) -> Optional[str]:
    value = (raw or "").strip()
    if not value:
        return None
    cleaned = "".join(ch for ch in value if ch not in '\\/:*?"<>|')
    cleaned = cleaned.strip()
    if not cleaned:
        raise ValueError("用户ID无效")
    if len(cleaned) > 64:
        raise ValueError("用户ID不能超过 64 个字符")
    return cleaned


def resolve_owner(avatar_type: str, user_id: Optional[str] = None, username: Optional[str] = None) -> Optional[str]:
    owner = normalize_user_id(user_id) or normalize_user_id(username)
    if avatar_type == "private":
        if not owner:
            raise ValueError("个人形象必须填写用户ID")
        return owner
    return None


def bake_status(row: dict) -> str:
    return BAKE_MAP.get(row.get("status") or "", "missing")


def public_character(row: dict) -> dict:
    item = dict(row)
    avatar_type = item.get("type") or "public"
    if avatar_type not in AVATAR_TYPES:
        avatar_type = "public"
    item["type"] = avatar_type
    item["user_id"] = item.get("user_id") if avatar_type == "private" else None
    return item


def is_private(row: dict) -> bool:
    return (row.get("type") or "public") == "private"


def can_view(row: dict, owner: Optional[str], *, admin: bool = False) -> bool:
    if admin:
        return True
    if not is_private(row):
        return True
    return bool(owner) and owner == (row.get("user_id") or "")


def to_admin_avatar(row: dict) -> dict:
    item = public_character(row)
    cid = item["id"]
    ready = item.get("status") == "ready" and bool(item.get("video_path"))
    # Never put user_id on media URLs: a shareable query string must not unlock private bytes.
    poster = f"/api/characters/{cid}/poster" if item.get("poster_path") else ""
    video = f"/api/characters/{cid}/video" if ready else ""
    owner = item.get("user_id")
    return {
        "identifier": cid,
        "id": cid,
        "name": item.get("name"),
        "type": item["type"],
        "user_id": owner,
        "username": owner,
        "status": item.get("status"),
        "error": item.get("error"),
        "progress": item.get("progress"),
        "duration": item.get("duration"),
        "width": item.get("width"),
        "height": item.get("height"),
        "created_at": item.get("created_at"),
        "thumbnail": poster,
        "preview_thumbnail": poster,
        "video_path": video,
        "preview_video_path": video,
        "bake_status": bake_status(item),
        "bake_progress": 100 if item.get("status") == "ready" else 0,
        "bake_message": item.get("error") or item.get("progress") or "",
    }


def list_characters(
    conn,
    avatar_type: Optional[str] = None,
    user_id: Optional[str] = None,
    username: Optional[str] = None,
    bake_status_filter: Optional[str] = None,
) -> list[dict]:
    owner = normalize_user_id(user_id) or normalize_user_id(username)
    if avatar_type == "private":
        if not owner:
            raise ValueError("查询个人形象必须填写用户ID")
        sql = "SELECT * FROM characters WHERE COALESCE(type, 'public') = 'private' AND user_id = ?"
        args: list[Any] = [owner]
    elif avatar_type == "public":
        sql = "SELECT * FROM characters WHERE COALESCE(type, 'public') = 'public'"
        args = []
    elif owner:
        sql = (
            "SELECT * FROM characters WHERE COALESCE(type, 'public') = 'public' "
            "OR (COALESCE(type, 'public') = 'private' AND user_id = ?)"
        )
        args = [owner]
    else:
        sql = "SELECT * FROM characters WHERE COALESCE(type, 'public') = 'public'"
        args = []
    sql += " ORDER BY created_at DESC"
    rows = [public_character(dict(r)) for r in conn.execute(sql, args).fetchall()]
    if bake_status_filter:
        rows = [r for r in rows if bake_status(r) == bake_status_filter]
    return rows


def counts(conn, owner: Optional[str] = None, *, include_all_private: bool = False) -> tuple[dict[str, int], dict[str, int]]:
    total = {"public": 0, "private": 0}
    ready = {"public": 0, "private": 0}
    owner = normalize_user_id(owner) if owner else None
    for row in conn.execute("SELECT type, status, user_id FROM characters").fetchall():
        kind = row["type"] if row["type"] in AVATAR_TYPES else "public"
        if kind == "private":
            if include_all_private:
                pass
            elif not owner or row["user_id"] != owner:
                continue
        total[kind] += 1
        if row["status"] == "ready":
            ready[kind] += 1
    return total, ready


def paginate(items: list, page: int, page_size: int) -> dict:
    page = max(1, int(page or 1))
    page_size = max(1, min(100, int(page_size or 12)))
    total = len(items)
    pages = max(1, math.ceil(total / page_size)) if total else 1
    page = min(page, pages)
    start = (page - 1) * page_size
    return {
        "items": items[start : start + page_size],
        "total": total,
        "page": page,
        "pages": pages,
        "page_size": page_size,
    }


def parse_upload_id(raw: Optional[str]) -> str:
    uid = (raw or "").strip().lower()
    if not UPLOAD_ID_RE.fullmatch(uid):
        raise FileNotFoundError("上传任务不存在")
    return uid


def chunk_dir(upload_id: str) -> Path:
    return STORAGE / "chunks" / parse_upload_id(upload_id)


def upload_meta_path(upload_id: str) -> Path:
    return chunk_dir(upload_id) / "meta.json"


def write_upload_meta(upload_id: str, data: dict, *, create: bool = False) -> None:
    path = upload_meta_path(upload_id)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    elif not path.is_file():
        raise FileNotFoundError("上传任务不存在")
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read_upload_meta(upload_id: str) -> dict:
    path = upload_meta_path(upload_id)
    if not path.is_file():
        raise FileNotFoundError("上传任务不存在")
    return json.loads(path.read_text(encoding="utf-8"))


def discard_upload_chunks(upload_id: str) -> bool:
    """Remove leftover parts for one session. Idempotent."""
    try:
        path = chunk_dir(upload_id)
    except FileNotFoundError:
        return False
    if not path.exists():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return True


def abort_upload(upload_id: str) -> None:
    """Cancel an unfinished upload and drop its chunks + unfinished DB row."""
    uid = parse_upload_id(upload_id)
    discard_upload_chunks(uid)
    from . import db

    with db.db() as conn:
        conn.execute("DELETE FROM uploads WHERE id = ? AND status != ?", (uid, "ready"))


def purge_character_files(character_id: str, source_path: Optional[str] = None) -> None:
    """Remove baked files and unused original under storage/uploads/."""
    shutil.rmtree(STORAGE / "characters" / character_id, ignore_errors=True)
    if not source_path:
        return
    src = Path(source_path)
    uploads_root = (STORAGE / "uploads").resolve()
    candidate = src.resolve() if src.exists() else src
    try:
        candidate.relative_to(uploads_root)
    except ValueError:
        return
    from . import db

    with db.db() as conn:
        others = conn.execute(
            "SELECT 1 FROM characters WHERE source_path = ? AND id != ? LIMIT 1",
            (str(src), character_id),
        ).fetchone()
        if others:
            return
    parent = candidate.parent
    try:
        parent_resolved = parent.resolve()
        if parent_resolved == uploads_root:
            candidate.unlink(missing_ok=True)
            return
        parent_resolved.relative_to(uploads_root)
    except (ValueError, OSError):
        candidate.unlink(missing_ok=True)
        return
    shutil.rmtree(parent, ignore_errors=True)


def merge_upload_chunks(upload_id: str, dest: Path, total_chunks: int, expected_size: int) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    parts_dir = chunk_dir(upload_id)
    with dest.open("wb") as out:
        for i in range(total_chunks):
            part = parts_dir / f"{i:06d}.part"
            if not part.exists():
                dest.unlink(missing_ok=True)
                raise FileNotFoundError(f"分片 {i} 文件丢失")
            written += out.write(part.read_bytes())
    if written != expected_size:
        dest.unlink(missing_ok=True)
        raise ValueError("合并后文件大小与声明不一致")
    shutil.rmtree(parts_dir, ignore_errors=True)
    return written


def _dir_activity_ts(path: Path) -> float:
    latest = path.stat().st_mtime
    try:
        for child in path.iterdir():
            try:
                latest = max(latest, child.stat().st_mtime)
            except OSError:
                continue
    except OSError:
        pass
    return latest


def _created_at_ts(text: Optional[str]) -> float:
    raw = (text or "").strip()
    if not raw:
        return 0.0
    if raw.endswith("Z"):
        raw = raw[:-1] + "+0000"
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S"):
        try:
            dt = datetime.strptime(raw, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except ValueError:
            continue
    return 0.0


def purge_stale_uploads(*, max_age_sec: int = CHUNK_TTL_SECONDS, force: bool = False) -> int:
    """Drop incomplete chunk dirs and abandoned upload rows after max_age_sec of inactivity."""
    global _last_purge_at
    now = time.time()
    if not force and now - _last_purge_at < _PURGE_MIN_INTERVAL:
        return 0
    _last_purge_at = now
    removed = 0
    root = STORAGE / "chunks"
    if root.is_dir():
        for path in list(root.iterdir()):
            if not path.is_dir():
                continue
            try:
                age = now - _dir_activity_ts(path)
            except OSError:
                continue
            if age < max_age_sec:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    try:
        from . import db

        with db.db() as conn:
            rows = conn.execute("SELECT id, status, created_at FROM uploads").fetchall()
            for row in rows:
                if (row["status"] or "") == "ready":
                    continue
                uid = row["id"]
                path = STORAGE / "chunks" / uid if isinstance(uid, str) else None
                fresh = False
                if path is not None and path.is_dir():
                    try:
                        fresh = (now - _dir_activity_ts(path)) < max_age_sec
                    except OSError:
                        fresh = False
                created = _created_at_ts(row["created_at"])
                if fresh or (created and now - created < max_age_sec):
                    continue
                if path is not None:
                    shutil.rmtree(path, ignore_errors=True)
                conn.execute("DELETE FROM uploads WHERE id = ? AND status != ?", (uid, "ready"))
                removed += 1
    except Exception:
        pass
    return removed
