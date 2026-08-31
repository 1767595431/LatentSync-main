import json
import math
import shutil
from pathlib import Path
from typing import Any, Optional

from .config import STORAGE

AVATAR_TYPES = ("public", "private")
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


def upload_meta_path(upload_id: str) -> Path:
    return STORAGE / "chunks" / upload_id / "meta.json"


def write_upload_meta(upload_id: str, data: dict) -> None:
    path = upload_meta_path(upload_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def read_upload_meta(upload_id: str) -> dict:
    path = upload_meta_path(upload_id)
    if not path.is_file():
        raise FileNotFoundError("上传任务不存在")
    return json.loads(path.read_text(encoding="utf-8"))


def merge_upload_chunks(upload_id: str, dest: Path, total_chunks: int, expected_size: int) -> int:
    dest.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    chunk_dir = STORAGE / "chunks" / upload_id
    with dest.open("wb") as out:
        for i in range(total_chunks):
            part = chunk_dir / f"{i:06d}.part"
            if not part.exists():
                raise FileNotFoundError(f"分片 {i} 文件丢失")
            written += out.write(part.read_bytes())
    if written != expected_size:
        dest.unlink(missing_ok=True)
        raise ValueError("合并后文件大小与声明不一致")
    shutil.rmtree(chunk_dir, ignore_errors=True)
    return written
