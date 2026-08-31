from typing import Optional

from . import avatars
from .config import DEFAULT_STEPS
from .progress import enrich_jobs, format_seconds

STATUS_OUT = {
    "queued": "wait",
    "running": "run",
    "done": "done",
    "failed": "error",
}
STATUS_IN = {
    "wait": "queued",
    "queued": "queued",
    "run": "running",
    "running": "running",
    "done": "done",
    "error": "failed",
    "failed": "failed",
}
QUALITY = {
    30: "标准",
    50: "高质量",
    80: "超高质量",
}


def quality_label(steps: Optional[int]) -> str:
    return QUALITY.get(int(steps or DEFAULT_STEPS), "自定义")


def to_admin_task(job: dict, char: Optional[dict] = None) -> dict:
    job = dict(job)
    cid = job.get("character_id")
    avatar = avatars.to_admin_avatar(char) if char else None
    status = STATUS_OUT.get(job.get("status") or "", "wait")
    tid = job["id"]
    done = status == "done" and bool(job.get("output_path"))
    preview = f"/api/tasks/{tid}/preview" if done else ""
    download = f"/api/tasks/{tid}/download" if done else ""
    steps = int(job.get("steps") or DEFAULT_STEPS)
    remain = job.get("remaining_text") or format_seconds(job.get("remaining_seconds"))
    return {
        "task_id": tid,
        "task_name": job.get("task_name") or job.get("audio_name") or tid,
        "username": job.get("username") or (char or {}).get("user_id"),
        "user_id": job.get("username") or (char or {}).get("user_id"),
        "avatar_identifier": cid,
        "avatar_name": (char or {}).get("name") or job.get("character_name"),
        "avatar_thumbnail": avatar["thumbnail"] if avatar else "",
        "avatar_preview_video": avatar["preview_video_path"] if avatar else "",
        "avatar_video_path": avatar["video_path"] if avatar else "",
        "avatar_bake_status": avatar["bake_status"] if avatar else "missing",
        "status": status,
        "progress": float(job.get("progress_percent") or 0),
        "progress_message": job.get("progress_text") or job.get("progress") or "",
        "error_message": job.get("error") or "",
        "result_path": preview,
        "result_thumbnail": (avatar or {}).get("thumbnail") if done else "",
        "result_path_lbr": preview,
        "created_at": job.get("created_at"),
        "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"),
        "steps": steps,
        "quality_label": quality_label(steps),
        "audio_name": job.get("audio_name"),
        "audio_duration": job.get("audio_duration"),
        "remaining_seconds": job.get("remaining_seconds"),
        "total_duration_text": remain,
    }


def load_jobs(conn) -> list[dict]:
    rows = conn.execute(
        """
        SELECT jobs.*, characters.name AS character_name,
               characters.type AS character_type, characters.user_id AS character_user_id
        FROM jobs
        LEFT JOIN characters ON characters.id = jobs.character_id
        ORDER BY jobs.created_at DESC
        """
    ).fetchall()
    return enrich_jobs([dict(r) for r in rows])


def character_map(conn) -> dict[str, dict]:
    rows = conn.execute("SELECT * FROM characters").fetchall()
    return {row["id"]: avatars.public_character(dict(row)) for row in rows}


def filter_jobs(
    jobs: list[dict],
    *,
    username: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    include_private: bool = False,
) -> list[dict]:
    items = jobs
    if username:
        key = username.strip()
        items = [j for j in items if (j.get("username") or "") == key]
    elif not include_private:
        items = [j for j in items if (j.get("character_type") or "public") != "private"]
    if status and status != "all":
        inner = STATUS_IN.get(status, status)
        items = [j for j in items if j.get("status") == inner]
    if keyword:
        q = keyword.strip().lower()
        items = [
            j
            for j in items
            if q in (j.get("task_name") or "").lower()
            or q in (j.get("audio_name") or "").lower()
            or q in (j.get("character_name") or "").lower()
        ]
    return items


def job_is_private(job: dict) -> bool:
    return (job.get("character_type") or "public") == "private"


def can_view_job(job: dict, owner: Optional[str], *, admin: bool = False) -> bool:
    if admin:
        return True
    if not job_is_private(job):
        return True
    if owner and (job.get("username") or "") == owner:
        return True
    if owner and (job.get("character_user_id") or "") == owner:
        return True
    return False
