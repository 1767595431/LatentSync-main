import math
import re
import subprocess
from datetime import datetime, timezone
from typing import Any, Optional

SEC_PER_CHUNK = {20: 10.2, 30: 13.2, 40: 17.5, 50: 21.0, 60: 25.2, 70: 29.4, 80: 33.6}
LOAD_OVERHEAD_SEC = 50
NUM_FRAMES = 16
VIDEO_FPS = 25


def parse_iso(ts: Optional[str]) -> Optional[datetime]:
    if not ts:
        return None
    try:
        return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def probe_duration(path: str) -> Optional[float]:
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=nw=1:nk=1",
                path,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        text = (result.stdout or "").strip()
        return float(text) if text else None
    except Exception:
        return None


def chunks_from_duration(duration_sec: Optional[float]) -> Optional[int]:
    if not duration_sec or duration_sec <= 0:
        return None
    frames = max(1, int(round(duration_sec * VIDEO_FPS)))
    return max(1, math.ceil(frames / NUM_FRAMES))


def estimate_from_chunks(steps: int, chunks: Optional[int], include_overhead: bool = True) -> Optional[int]:
    if not chunks:
        return None
    per = SEC_PER_CHUNK.get(int(steps), 10.2)
    overhead = LOAD_OVERHEAD_SEC if include_overhead else 0
    return int(overhead + chunks * per)


def estimate_seconds(steps: int, duration_sec: Optional[float]) -> Optional[int]:
    return estimate_from_chunks(steps, chunks_from_duration(duration_sec), include_overhead=True)


def parse_tqdm_time(text: str) -> Optional[float]:
    text = (text or "").strip()
    if not text or text in {"?", "??"}:
        return None
    parts = text.split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        return None
    return None


def format_seconds(sec: Optional[float]) -> Optional[str]:
    if sec is None:
        return None
    sec = max(0, int(round(sec)))
    if sec < 60:
        return f"约 {sec} 秒"
    minutes = sec // 60
    if minutes < 60:
        remain = sec % 60
        if remain >= 20:
            return f"约 {minutes} 分 {remain} 秒"
        return f"约 {minutes} 分钟"
    hours = minutes // 60
    minutes = minutes % 60
    return f"约 {hours} 小时 {minutes} 分钟"


def _decay_since(value: Optional[float], ts: Optional[str]) -> Optional[float]:
    if value is None:
        return None
    t0 = parse_iso(ts)
    if t0 is None:
        return value
    return max(0.0, float(value) - (now_utc() - t0).total_seconds())


def running_remaining(
    *,
    estimated_seconds: Optional[float],
    current_chunk: Optional[int],
    total_chunks: Optional[int],
    tqdm_remaining: Optional[float],
    started_at: Optional[str],
    infer_started_at: Optional[str],
    stage: Optional[str],
    progress_updated_at: Optional[str] = None,
) -> Optional[float]:
    if stage in {"restoring", "muxing"}:
        return _decay_since(20.0, progress_updated_at) if progress_updated_at else 20.0
    if tqdm_remaining is not None and tqdm_remaining >= 0:
        extra = 25 if stage == "inferring" else 0
        return _decay_since(float(tqdm_remaining) + extra, progress_updated_at)
    total = int(total_chunks or 0)
    done = int(current_chunk or 0)
    infer_t0 = parse_iso(infer_started_at)
    if infer_t0 and done >= 1 and total > done:
        elapsed = (now_utc() - infer_t0).total_seconds()
        avg = elapsed / max(done, 1)
        return max(0.0, (total - done) * avg + 25)
    started = parse_iso(started_at)
    if estimated_seconds and started:
        elapsed = (now_utc() - started).total_seconds()
        return max(8.0, float(estimated_seconds) - elapsed)
    return None if estimated_seconds is None else float(estimated_seconds)


def enrich_jobs(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    items = [dict(j) for j in jobs]
    running = [j for j in items if j.get("status") == "running"]
    queued = sorted(
        [j for j in items if j.get("status") == "queued"],
        key=lambda j: j.get("created_at") or "",
    )

    if running:
        r = running[0]
        remain = running_remaining(
            estimated_seconds=r.get("estimated_seconds"),
            current_chunk=r.get("current_chunk"),
            total_chunks=r.get("total_chunks"),
            tqdm_remaining=r.get("tqdm_remaining"),
            started_at=r.get("started_at"),
            infer_started_at=r.get("infer_started_at"),
            stage=r.get("stage"),
            progress_updated_at=r.get("progress_updated_at"),
        )
        r["remaining_seconds"] = None if remain is None else int(round(remain))
        if (r.get("stage") in {None, "", "loading"}) and not r.get("current_chunk"):
            started = parse_iso(r.get("started_at"))
            if started:
                elapsed = (now_utc() - started).total_seconds()
                r["progress_percent"] = round(min(8.0, 8.0 * elapsed / LOAD_OVERHEAD_SEC), 1)

    for idx, job in enumerate(queued, start=1):
        job["queue_position"] = idx
        job["remaining_seconds"] = None

    for job in items:
        if job.get("status") == "done":
            job["progress_percent"] = 100
            job["remaining_seconds"] = 0
            job["stage"] = job.get("stage") or "done"
        elif job.get("status") == "failed":
            job["remaining_seconds"] = 0
        elif job.get("status") == "queued":
            job["progress_percent"] = 0
            job["stage"] = "queued"
            job["remaining_seconds"] = None
        job["remaining_text"] = (
            None
            if job.get("status") != "running"
            else format_seconds(job.get("remaining_seconds"))
        )
        if job.get("status") in {"done", "failed"}:
            job["remaining_text"] = None
        job["progress_text"] = _progress_text(job)
    return items


def _progress_text(job: dict[str, Any]) -> str:
    status = job.get("status")
    stage = job.get("stage")
    if status == "done":
        return "已完成"
    if status == "failed":
        return job.get("error") or "失败"
    if status == "queued":
        return "排队中"
    cur, total = job.get("current_chunk"), job.get("total_chunks")
    pct = job.get("progress_percent")
    if stage == "inferring" and cur is not None and total:
        text = f"合成中 {int(cur)}/{int(total)}"
        if pct is not None:
            text += f" · {int(pct)}%"
        return text
    labels = {
        "loading": "加载模型",
        "aligning": "人脸对齐",
        "inferring": "扩散合成",
        "restoring": "贴回原视频",
        "muxing": "封装成片",
    }
    return labels.get(stage or "", job.get("progress") or "合成中")


INFER_RE = re.compile(
    r"Doing inference\.\.\.:\s+(\d+)%\|.*?\|\s*(\d+)/(\d+)\s*\[([^<]*)<([^,\]]+)"
)
STREAM_RE = re.compile(
    r"Streaming video on demand \((\d+) source frames, (\d+) audio-aligned frames\)"
)
PLAN_RE = re.compile(r"Inference plan:\s+(\d+) chunks")


def parse_job_line(line: str, steps: int = 30) -> Optional[dict[str, Any]]:
    text = (line or "").replace("\r", "").strip()
    if not text:
        return None

    match = INFER_RE.search(text)
    if match:
        percent = float(match.group(1))
        current = int(match.group(2))
        total = int(match.group(3))
        remain = parse_tqdm_time(match.group(5))
        if total > 0:
            percent = round(100.0 * current / total, 1)
        return {
            "progress": f"合成中 {current}/{total} · {int(percent)}%",
            "progress_percent": percent,
            "current_chunk": current,
            "total_chunks": total,
            "tqdm_remaining": remain,
            "remaining_seconds": None if remain is None else int(round(remain + 25)),
            "stage": "inferring",
            "set_infer_started": True,
        }

    match = PLAN_RE.search(text)
    if match:
        total = int(match.group(1))
        eta = estimate_from_chunks(steps, total, include_overhead=False)
        return {
            "progress": f"准备合成 {total} 块",
            "total_chunks": total,
            "estimated_seconds": estimate_from_chunks(steps, total, include_overhead=True),
            "remaining_seconds": eta,
            "stage": "inferring",
        }

    match = STREAM_RE.search(text)
    if match:
        aligned = int(match.group(2))
        total = max(1, math.ceil(aligned / NUM_FRAMES))
        eta = estimate_from_chunks(steps, total, include_overhead=False)
        return {
            "progress": f"按需加载视频 · {total} 块",
            "total_chunks": total,
            "estimated_seconds": estimate_from_chunks(steps, total, include_overhead=True),
            "remaining_seconds": eta,
            "stage": "inferring",
        }

    lowered = text.lower()
    if "muxing audio and video" in lowered or text.startswith("Muxing"):
        return {"progress": "封装成片", "progress_percent": 97, "stage": "muxing", "remaining_seconds": 20}
    if "loading models" in lowered or "loaded checkpoint" in lowered:
        return {"progress": "加载模型", "stage": "loading"}
    if "running lipsync" in lowered:
        return {"progress": "启动合成", "stage": "loading"}
    if "Affine transforming" in text:
        return {"progress": "人脸对齐", "stage": "aligning"}
    if text.startswith("Restoring"):
        return {"progress": "贴回原视频", "stage": "restoring", "remaining_seconds": 25}
    return None
