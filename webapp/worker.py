import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Optional

from . import db
from .config import GUIDANCE_SCALE, PYTHON, ROOT, STORAGE, UNET_CKPT, UNET_CONFIG
from .progress import parse_job_line

_stop = threading.Event()
_thread: Optional[threading.Thread] = None
_char_thread: Optional[threading.Thread] = None


def start_worker() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _recover_stale()
    _thread = threading.Thread(target=_loop, name="latentsync-worker", daemon=True)
    _thread.start()


def _recover_stale() -> None:
    with db.db() as conn:
        conn.execute(
            "UPDATE characters SET status = ?, error = NULL WHERE status IN (?, ?)",
            ("queued", "preparing", "aligning"),  # aligning: 兼容旧状态
        )
        if inference_running():
            return
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, error = ?, finished_at = ?, stage = ?, remaining_seconds = 0
            WHERE status = ?
            """,
            ("failed", "服务重启，合成中断", db.utcnow(), "failed", "running"),
        )


def stop_worker() -> None:
    _stop.set()


def inference_running() -> bool:
    try:
        result = subprocess.run(
            ["pgrep", "-af", "scripts.inference"],
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            if "pgrep" in line:
                continue
            if "/bin/bash" in line or "extglob" in line:
                continue
            if "scripts.inference" in line and "python" in line.lower():
                return True
        return False
    except Exception:
        return False


TARGET_FPS = 25
_FPS_TOLERANCE = 0.05


def prepare_character(character_id: str) -> None:
    with db.db() as conn:
        row = conn.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
        if row is None:
            return
        char = dict(row)
        conn.execute(
            "UPDATE characters SET status = ?, error = NULL, progress = ? WHERE id = ?",
            ("preparing", "检测帧率", character_id),
        )

    char_dir = STORAGE / "characters" / character_id
    char_dir.mkdir(parents=True, exist_ok=True)
    video_out = char_dir / "video.mp4"
    preview_out = char_dir / "preview.mp4"
    poster_out = char_dir / "poster.jpg"
    source = Path(char["source_path"])

    try:
        if not video_out.exists():
            src_meta = _probe(source)
            if _is_25fps(src_meta.get("fps")):
                with db.db() as conn:
                    conn.execute(
                        "UPDATE characters SET progress = ? WHERE id = ?",
                        ("已是 25fps，跳过转码", character_id),
                    )
                shutil.copy2(source, video_out)
            else:
                with db.db() as conn:
                    conn.execute(
                        "UPDATE characters SET progress = ? WHERE id = ?",
                        ("转码中", character_id),
                    )
                _ffmpeg_prepare_video(source, video_out)
        if not poster_out.exists():
            _extract_poster(video_out, poster_out)
        if not preview_out.exists() or preview_out.stat().st_size < 1000:
            with db.db() as conn:
                conn.execute(
                    "UPDATE characters SET progress = ? WHERE id = ?",
                    ("生成预览", character_id),
                )
            _ffmpeg_preview_video(video_out, preview_out)
        meta = _probe(video_out)
        with db.db() as conn:
            conn.execute(
                """
                UPDATE characters
                SET video_path = ?, preview_path = ?, poster_path = ?, duration = ?, width = ?, height = ?,
                    status = ?, progress = ?, error = NULL
                WHERE id = ?
                """,
                (
                    str(video_out),
                    str(preview_out) if preview_out.exists() else None,
                    str(poster_out) if poster_out.exists() else None,
                    meta.get("duration"),
                    meta.get("width"),
                    meta.get("height"),
                    "ready",
                    None,
                    character_id,
                ),
            )
        print(f"Character {character_id} ready fps={meta.get('fps')}")
    except Exception as exc:
        with db.db() as conn:
            conn.execute(
                "UPDATE characters SET status = ?, error = ?, progress = NULL WHERE id = ?",
                ("failed", str(exc), character_id),
            )


def ensure_preview(character_id: str) -> None:
    """为已就绪、缺预览片的形象补生成低码率预览（不改变 ready 状态）。"""
    with db.db() as conn:
        row = conn.execute("SELECT * FROM characters WHERE id = ?", (character_id,)).fetchone()
        if row is None:
            return
        char = dict(row)
    video = Path(char["video_path"]) if char.get("video_path") else None
    if not video or not video.exists():
        return
    preview_out = Path(char["preview_path"]) if char.get("preview_path") else video.parent / "preview.mp4"
    if preview_out.exists() and preview_out.stat().st_size >= 1000:
        with db.db() as conn:
            conn.execute(
                "UPDATE characters SET preview_path = ? WHERE id = ? AND (preview_path IS NULL OR preview_path = '')",
                (str(preview_out), character_id),
            )
        return
    try:
        _ffmpeg_preview_video(video, preview_out)
        with db.db() as conn:
            conn.execute(
                "UPDATE characters SET preview_path = ? WHERE id = ?",
                (str(preview_out), character_id),
            )
        print(f"Character {character_id} preview ready")
    except Exception as exc:
        print(f"Character {character_id} preview failed: {exc}")


def _fps_from_ratio(raw) -> Optional[float]:
    text = str(raw or "").strip()
    if not text or text in {"0/0", "N/A"}:
        return None
    try:
        if "/" in text:
            num, den = text.split("/", 1)
            den_f = float(den)
            if den_f == 0:
                return None
            return float(num) / den_f
        return float(text)
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _is_25fps(fps: Optional[float]) -> bool:
    return fps is not None and abs(fps - TARGET_FPS) < _FPS_TOLERANCE


def _extract_poster(src: Path, dst: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            "0",
            "-i",
            str(src),
            "-frames:v",
            "1",
            str(dst),
        ],
        check=True,
    )


def _ffmpeg_prepare_video(src: Path, dst: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(src),
            "-r",
            str(TARGET_FPS),
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "18",
            "-an",
            str(dst),
        ],
        check=True,
    )


def _ffmpeg_preview_video(src: Path, dst: Path) -> None:
    """网页预览用低码率片：最高 720p、CRF28、faststart，合成仍用 video.mp4。"""
    tmp = dst.with_suffix(".tmp.mp4")
    try:
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(src),
                "-vf",
                r"scale=-2:min(720\,ih)",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "28",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                "-an",
                str(tmp),
            ],
            check=True,
        )
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)


def _probe(path: Path) -> dict:
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate,avg_frame_rate",
            "-show_entries",
            "format=duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    duration = fmt.get("duration")
    fps = _fps_from_ratio(stream.get("avg_frame_rate")) or _fps_from_ratio(stream.get("r_frame_rate"))
    return {
        "width": stream.get("width"),
        "height": stream.get("height"),
        "duration": float(duration) if duration else None,
        "fps": fps,
    }


def _loop() -> None:
    while not _stop.is_set():
        try:
            _kick_character_prepare()
            _run_next_job()
        except Exception:
            time.sleep(2)
        time.sleep(1)


def _kick_character_prepare() -> None:
    global _char_thread
    if _char_thread and _char_thread.is_alive():
        return
    character_id = _pick_character_to_prepare()
    if character_id:
        _char_thread = threading.Thread(
            target=prepare_character, args=(character_id,), name="latentsync-prepare", daemon=True
        )
        _char_thread.start()
        return
    preview_id = _pick_character_needing_preview()
    if not preview_id:
        return
    _char_thread = threading.Thread(
        target=ensure_preview, args=(preview_id,), name="latentsync-preview", daemon=True
    )
    _char_thread.start()


def _pick_character_to_prepare() -> Optional[str]:
    with db.db() as conn:
        row = conn.execute(
            "SELECT id FROM characters WHERE status = ? ORDER BY created_at LIMIT 1",
            ("queued",),
        ).fetchone()
        if row is not None:
            return row["id"]
    return None


def _pick_character_needing_preview() -> Optional[str]:
    with db.db() as conn:
        rows = conn.execute(
            """
            SELECT id, video_path, preview_path FROM characters
            WHERE status = 'ready' AND video_path IS NOT NULL
            ORDER BY created_at
            """
        ).fetchall()
    for row in rows:
        preview = Path(row["preview_path"]) if row["preview_path"] else None
        if preview and preview.exists() and preview.stat().st_size >= 1000:
            continue
        video = Path(row["video_path"]) if row["video_path"] else None
        if video and video.exists():
            return row["id"]
    return None


def _run_next_job() -> None:
    if inference_running():
        return
    with db.db() as conn:
        rows = conn.execute(
            """
            SELECT jobs.*, characters.video_path AS character_video, characters.status AS character_status
            FROM jobs
            JOIN characters ON characters.id = jobs.character_id
            WHERE jobs.status = ?
            ORDER BY jobs.created_at
            """,
            ("queued",),
        ).fetchall()
        job = None
        for row in rows:
            item = dict(row)
            if item["character_status"] == "failed":
                conn.execute(
                    "UPDATE jobs SET status = ?, error = ?, finished_at = ?, stage = ?, remaining_seconds = 0 WHERE id = ?",
                    ("failed", "形象处理失败，无法合成", db.utcnow(), "failed", item["id"]),
                )
                continue
            if item["character_status"] != "ready" or not item["character_video"]:
                continue
            if not Path(item["character_video"]).exists() or not Path(item["audio_path"]).exists():
                conn.execute(
                    "UPDATE jobs SET status = ?, error = ?, finished_at = ?, stage = ?, remaining_seconds = 0 WHERE id = ?",
                    ("failed", "形象视频或音频文件缺失", db.utcnow(), "failed", item["id"]),
                )
                continue
            job = item
            break
        if job is None:
            return
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, progress = ?, stage = ?, started_at = ?, error = NULL,
                progress_percent = 0, remaining_seconds = estimated_seconds, progress_updated_at = ?
            WHERE id = ?
            """,
            ("running", "加载模型", "loading", db.utcnow(), db.utcnow(), job["id"]),
        )

    _execute_job(job)


def _execute_job(job: dict) -> None:
    job_id = job["id"]
    job_dir = STORAGE / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    output_path = job_dir / "output.mp4"
    temp_dir = job_dir / "temp"
    if temp_dir.exists():
        shutil.rmtree(temp_dir, ignore_errors=True)

    cmd = [
        PYTHON,
        "-u",
        "-m",
        "scripts.inference",
        "--unet_config_path",
        str(UNET_CONFIG),
        "--inference_ckpt_path",
        str(UNET_CKPT),
        "--inference_steps",
        str(job["steps"]),
        "--guidance_scale",
        str(GUIDANCE_SCALE),
        "--enable_deepcache",
        "--video_path",
        job["character_video"],
        "--audio_path",
        job["audio_path"],
        "--video_out_path",
        str(output_path),
        "--temp_dir",
        str(temp_dir),
    ]
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        assert proc.stdout is not None
        buf = ""
        while True:
            chunk = proc.stdout.read(256)
            if not chunk:
                break
            buf += chunk.decode("utf-8", errors="replace")
            while True:
                idx_r, idx_n = buf.find("\r"), buf.find("\n")
                cuts = [i for i in (idx_r, idx_n) if i >= 0]
                if not cuts:
                    if len(buf) > 800:
                        buf = buf[-200:]
                    break
                i = min(cuts)
                line, buf = buf[:i], buf[i + 1 :]
                fields = parse_job_line(line, steps=int(job.get("steps") or 20))
                if fields:
                    _update_job_progress(job_id, fields)
        code = proc.wait()
        if code != 0 or not output_path.exists() or output_path.stat().st_size < 1000:
            raise RuntimeError(f"合成失败，退出码 {code}")
        with db.db() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, progress = ?, output_path = ?, finished_at = ?, error = NULL,
                    progress_percent = 100, remaining_seconds = 0, stage = ?, tqdm_remaining = 0,
                    progress_updated_at = ?
                WHERE id = ?
                """,
                ("done", "已完成", str(output_path), db.utcnow(), "done", db.utcnow(), job_id),
            )
    except Exception as exc:
        with db.db() as conn:
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?, finished_at = ?, stage = ?, remaining_seconds = 0
                WHERE id = ?
                """,
                ("failed", str(exc), db.utcnow(), "failed", job_id),
            )
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def _update_job_progress(job_id: str, fields: dict) -> None:
    now = db.utcnow()
    assignments = ["progress_updated_at = ?"]
    values: list = [now]
    for key in (
        "progress",
        "progress_percent",
        "current_chunk",
        "total_chunks",
        "remaining_seconds",
        "estimated_seconds",
        "stage",
        "tqdm_remaining",
    ):
        if key in fields and fields[key] is not None:
            assignments.append(f"{key} = ?")
            values.append(fields[key])
    if fields.get("set_infer_started"):
        assignments.append("infer_started_at = COALESCE(infer_started_at, ?)")
        values.append(now)
    values.append(job_id)
    sql = f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ?"
    with db.db() as conn:
        conn.execute(sql, values)
