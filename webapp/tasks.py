from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
import shutil
import time

from . import avatars
from .config import DEFAULT_STEPS, STORAGE, job_ttl_days
from .progress import enrich_jobs, format_seconds

CANCEL_MESSAGE = "已取消"
_CANCEL_FLAG = "cancel.flag"


def job_dir(job_id: str) -> Path:
    return STORAGE / "jobs" / job_id


def cancel_flag_path(job_id: str) -> Path:
    return job_dir(job_id) / _CANCEL_FLAG


def request_cancel(job_id: str) -> None:
    path = cancel_flag_path(job_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("1", encoding="utf-8")


def clear_cancel(job_id: str) -> None:
    cancel_flag_path(job_id).unlink(missing_ok=True)


def is_cancel_requested(job_id: Optional[str]) -> bool:
    return bool(job_id) and cancel_flag_path(job_id).exists()

STATUS_OUT = {
    "queued": "wait",
    "running": "run",
    "done": "done",
    "failed": "error",
    "cancelled": "cancelled",
}
STATUS_IN = {
    "wait": "queued",
    "queued": "queued",
    "run": "running",
    "running": "running",
    "done": "done",
    "error": "failed",
    "failed": "failed",
    "cancelled": "cancelled",
    "cancel": "cancelled",
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
    remain = None
    remaining = None
    if status == "run":
        remaining = job.get("remaining_seconds")
        remain = job.get("remaining_text") or format_seconds(remaining)
    elif status == "wait":
        remaining = None
        remain = None
    else:
        remaining = 0 if status in {"done", "error", "cancelled"} else job.get("remaining_seconds")
        remain = None
    queue_position = job.get("queue_position") if status == "wait" else None
    queue_ahead = None
    if status == "wait" and queue_position is not None:
        queue_ahead = max(0, int(queue_position) - 1)
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
        "progress_message": (
            "已取消" if status == "cancelled"
            else "合成失败" if status == "error"
            else (job.get("progress_text") or job.get("progress") or "")
        ),
        "error_message": "" if status == "cancelled" else (job.get("error") or ""),
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
        "remaining_seconds": remaining,
        "total_duration_text": remain,
        "queue_position": queue_position,
        "queue_ahead": queue_ahead,
    }


def canonicalize_job(job: dict) -> dict:
    job = dict(job)
    if job.get("status") == "failed" and (
        job.get("error") == CANCEL_MESSAGE
        or job.get("progress") == CANCEL_MESSAGE
        or job.get("stage") == "cancelled"
    ):
        job["status"] = "cancelled"
        job["error"] = None
        job["progress"] = CANCEL_MESSAGE
        job["stage"] = "cancelled"
    return job


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
    return enrich_jobs([canonicalize_job(dict(r)) for r in rows])


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
        try:
            key = avatars.normalize_user_id(username)
        except ValueError:
            key = username.strip() or None
        if key:
            matched = []
            for j in items:
                stored = j.get("username") or ""
                try:
                    got = avatars.normalize_user_id(stored)
                except ValueError:
                    got = stored.strip() or None
                if got == key:
                    matched.append(j)
            items = matched
        else:
            items = []
    elif not include_private:
        items = [j for j in items if (j.get("character_type") or "public") != "private"]
    if status and status != "all":
        key = status.strip().lower()
        if key in {"active", "live"}:
            items = [j for j in items if j.get("status") in {"queued", "running"}]
        else:
            inner = STATUS_IN.get(key, key)
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


def personal_summary(conn, owner: str) -> dict:
    """This user's avatars, works, and global queue wait ahead of their earliest queued job."""
    key = avatars.normalize_user_id(owner)
    avatars_total = avatars_ready = avatars_processing = avatars_error = 0
    if key:
        for row in conn.execute(
            """
            SELECT status FROM characters
            WHERE COALESCE(type, 'public') = 'private' AND user_id = ?
            """,
            (key,),
        ):
            avatars_total += 1
            st = row["status"] or ""
            if st == "ready":
                avatars_ready += 1
            elif st in {"queued", "preparing", "aligning"}:
                avatars_processing += 1
            elif st == "failed":
                avatars_error += 1
    jobs = filter_jobs(load_jobs(conn), username=key, include_private=True) if key else []
    done = run = wait = error = cancelled = 0
    first_pos = None
    for job in jobs:
        st = job.get("status")
        if st == "done":
            done += 1
        elif st == "running":
            run += 1
        elif st == "queued":
            wait += 1
            pos = job.get("queue_position")
            if pos is not None and (first_pos is None or int(pos) < first_pos):
                first_pos = int(pos)
        elif st == "failed":
            error += 1
        elif st == "cancelled":
            cancelled += 1
    gpu_busy = False
    try:
        from .gpu_runtime import inference_running

        gpu_busy = bool(inference_running())
    except Exception:
        pass
    return {
        "avatars": {
            "total": avatars_total,
            "ready": avatars_ready,
            "processing": avatars_processing,
            "error": avatars_error,
        },
        "tasks": {
            "done": done,
            "run": run,
            "wait": wait,
            "error": error,
            "cancelled": cancelled,
        },
        "queue": {
            "mine": wait,
            "ahead": None if first_pos is None else max(0, first_pos - 1),
            "position": first_pos,
            "gpu_busy": gpu_busy,
        },
    }


_PURGE_MIN_INTERVAL = 60
_last_job_purge_at = 0.0


def _job_keep_stamp(job: dict) -> str:
    stamps = [
        job.get("finished_at"),
        job.get("progress_updated_at"),
        job.get("started_at"),
        job.get("created_at"),
    ]
    kept = [s for s in stamps if s]
    return max(kept) if kept else ""


def remove_job_files(job_id: str) -> None:
    shutil.rmtree(job_dir(job_id), ignore_errors=True)


def purge_expired_jobs(*, force: bool = False) -> int:
    """Drop works older than job_ttl_days. Never touches a running synthesis."""
    global _last_job_purge_at
    now = time.time()
    if not force and now - _last_job_purge_at < _PURGE_MIN_INTERVAL:
        return 0
    _last_job_purge_at = now
    days = job_ttl_days()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    live: set[str] = set()
    try:
        from .gpu_runtime import inference_pids, job_id_from_pid

        for pid in inference_pids():
            jid = job_id_from_pid(pid)
            if jid:
                live.add(jid)
    except Exception:
        pass
    from . import db

    removed = 0
    with db.db() as conn:
        rows = conn.execute(
            """
            SELECT id, status, created_at, started_at, finished_at, progress_updated_at
            FROM jobs WHERE status != ?
            """,
            ("running",),
        ).fetchall()
        expired = []
        for row in rows:
            item = dict(row)
            if item["id"] in live:
                continue
            stamp = _job_keep_stamp(item)
            if stamp and stamp < cutoff:
                expired.append(item["id"])
        for jid in expired:
            cur = conn.execute("DELETE FROM jobs WHERE id = ? AND status != ?", (jid, "running"))
            if cur.rowcount:
                remove_job_files(jid)
                removed += 1
    root = STORAGE / "jobs"
    if root.is_dir():
        with db.db() as conn:
            known = {row["id"] for row in conn.execute("SELECT id FROM jobs")}
        ttl_sec = days * 86400
        for path in list(root.iterdir()):
            if not path.is_dir() or path.name in known or path.name in live:
                continue
            try:
                age = now - path.stat().st_mtime
            except OSError:
                continue
            if age < ttl_sec:
                continue
            shutil.rmtree(path, ignore_errors=True)
            removed += 1
    if removed:
        print(f"purged {removed} expired jobs (>{days}d)", flush=True)
    return removed
