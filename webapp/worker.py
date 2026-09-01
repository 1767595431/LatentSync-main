import fcntl
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

_env_bin = str(Path(sys.executable).resolve().parent)
_path = os.environ.get("PATH", "")
if _env_bin not in _path.split(os.pathsep):
    os.environ["PATH"] = _env_bin + (os.pathsep + _path if _path else "")

from . import db, tasks
from .config import (
    GUIDANCE_SCALE,
    PYTHON,
    ROOT,
    STORAGE,
    UNET_CKPT,
    UNET_CONFIG,
    WORKER_LOCK_PATH,
    WORKER_PID_PATH,
    ensure_dirs,
    load_gpus,
)
from .gpu_runtime import (
    cuda_visible_from_pid,
    inference_pids,
    job_id_from_pid,
    write_status,
)
from .media import (
    ensure_preview as write_preview_mp4,
    ffmpeg_preview_video,
    has_av_streams,
    mux_video_audio,
    preview_is_current,
)
from .progress import parse_job_line

_stop = threading.Event()
_thread: Optional[threading.Thread] = None


class JobCancelled(Exception):
    pass


_char_thread: Optional[threading.Thread] = None
_gpu_lock = threading.Lock()
_busy_gpus: dict[int, str] = {}
_gpu_threads: dict[int, threading.Thread] = {}
_lock_fd: Optional[int] = None


def start_worker() -> None:
    """In-process loop (tests only). Production uses `python -m webapp.worker`."""
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _recover_stale()
    _thread = threading.Thread(target=_loop, name="qemix-worker", daemon=True)
    _thread.start()


def _recover_stale() -> None:
    adopted = _adopt_running_inference()
    with db.db() as conn:
        conn.execute(
            "UPDATE characters SET status = ?, error = NULL WHERE status IN (?, ?)",
            ("queued", "preparing", "aligning"),
        )
        rows = conn.execute("SELECT id FROM jobs WHERE status = ?", ("running",)).fetchall()
        for row in rows:
            if row["id"] in adopted:
                continue
            if tasks.is_cancel_requested(row["id"]):
                _mark_job_cancelled(row["id"])
                continue
            conn.execute(
                """
                UPDATE jobs
                SET status = ?, error = ?, finished_at = ?, stage = ?, remaining_seconds = 0
                WHERE id = ?
                """,
                ("failed", "合成进程已退出", db.utcnow(), "failed", row["id"]),
            )


def _adopt_running_inference() -> set[str]:
    adopted: set[str] = set()
    for pid in inference_pids():
        job_id = job_id_from_pid(pid)
        gpu_id = cuda_visible_from_pid(pid)
        if job_id:
            adopted.add(job_id)
        if gpu_id is None:
            continue
        with _gpu_lock:
            if gpu_id in _busy_gpus:
                continue
            _busy_gpus[gpu_id] = job_id or f"pid:{pid}"
        th = threading.Thread(
            target=_watch_adopted,
            args=(pid, gpu_id, job_id),
            name=f"adopt-gpu{gpu_id}",
            daemon=True,
        )
        with _gpu_lock:
            _gpu_threads[gpu_id] = th
        th.start()
    return adopted


def _load_job(job_id: str) -> Optional[dict]:
    with db.db() as conn:
        row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    return dict(row) if row else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_process_group(pid: int) -> None:
    if pid <= 0:
        return
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(pid, sig)
        except OSError:
            try:
                os.kill(pid, sig)
            except OSError:
                return
        deadline = time.time() + (5 if sig == signal.SIGTERM else 2)
        while time.time() < deadline:
            if not _pid_alive(pid):
                return
            time.sleep(0.15)


def _mark_job_cancelled(job_id: str) -> None:
    with db.db() as conn:
        conn.execute(
            """
            UPDATE jobs
            SET status = ?, error = NULL, finished_at = ?, stage = ?, remaining_seconds = 0, progress = ?
            WHERE id = ? AND status = ?
            """,
            ("cancelled", db.utcnow(), "cancelled", tasks.CANCEL_MESSAGE, job_id, "running"),
        )


def _catch_up_job_log(log_path: Path, job: dict) -> int:
    """Parse recent tqdm lines into DB. Returns size to keep following from."""
    try:
        size = log_path.stat().st_size
    except OSError:
        return 0
    start = max(0, size - 65536)
    try:
        with open(log_path, "rb") as rf:
            rf.seek(start)
            if start:
                rf.readline()
            data = rf.read().decode("utf-8", errors="replace")
    except OSError:
        return size
    _consume_progress_buf(data.replace("\r", "\n") + "\n", job)
    return size


def _follow_job_log(pid: int, log_path: Path, start_off: int, job: dict) -> None:
    buf = ""
    offset = start_off
    while True:
        try:
            with open(log_path, "rb") as rf:
                rf.seek(offset)
                while True:
                    chunk = rf.read(4096)
                    if chunk:
                        offset += len(chunk)
                        buf += chunk.decode("utf-8", errors="replace")
                        buf = _consume_progress_buf(buf, job)
                        continue
                    if not _pid_alive(pid):
                        extra = rf.read()
                        if extra:
                            buf += extra.decode("utf-8", errors="replace")
                            _consume_progress_buf(buf, job)
                        return
                    if tasks.is_cancel_requested(job.get("id")):
                        _kill_process_group(pid)
                        return
                    time.sleep(0.15)
        except FileNotFoundError:
            if not _pid_alive(pid):
                return
            time.sleep(0.3)


def _watch_adopted(pid: int, gpu_id: int, job_id: Optional[str]) -> None:
    try:
        job = _load_job(job_id) if job_id else None
        if not job and job_id:
            job = {"id": job_id, "steps": 30}
        log_path = STORAGE / "jobs" / job_id / "inference.log" if job_id else None
        if job and log_path:
            start_off = _catch_up_job_log(log_path, job) if log_path.exists() else 0
            _follow_job_log(pid, log_path, start_off, job)
        else:
            while _pid_alive(pid):
                if tasks.is_cancel_requested(job_id):
                    _kill_process_group(pid)
                    break
                time.sleep(1)
        if not job_id:
            return
        output_path = STORAGE / "jobs" / job_id / "output.mp4"
        if tasks.is_cancel_requested(job_id):
            _mark_job_cancelled(job_id)
            return
        if _finalize_job_output(job_id, output_path):
            _mark_job_done(job_id, output_path, only_if_running=True)
        else:
            with db.db() as conn:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, error = ?, finished_at = ?, stage = ?, remaining_seconds = 0
                    WHERE id = ? AND status = ?
                    """,
                    ("failed", "合成进程已退出", db.utcnow(), "failed", job_id, "running"),
                )
    finally:
        _release_gpu(gpu_id)


def _mark_job_done(job_id: str, output_path: Path, *, only_if_running: bool = False) -> None:
    if tasks.is_cancel_requested(job_id):
        _mark_job_cancelled(job_id)
        return
    preview_path = output_path.parent / "preview.mp4"
    preview_stored = None
    try:
        write_preview_mp4(output_path, preview_path)
        preview_stored = str(preview_path)
    except Exception as exc:
        print(f"Job {job_id} preview failed: {exc}")
    sql = """
        UPDATE jobs
        SET status = ?, progress = ?, output_path = ?, preview_path = ?, finished_at = ?, error = NULL,
            progress_percent = 100, remaining_seconds = 0, stage = ?, tqdm_remaining = 0,
            progress_updated_at = ?
        WHERE id = ? AND status = ?
    """
    args = (
        "done",
        "已完成",
        str(output_path),
        preview_stored,
        db.utcnow(),
        "done",
        db.utcnow(),
        job_id,
        "running",
    )
    with db.db() as conn:
        conn.execute(sql, args)


def stop_worker() -> None:
    _stop.set()


def inference_running() -> bool:
    _reap_gpus()
    with _gpu_lock:
        if _busy_gpus:
            return True
    return bool(inference_pids())


def gpu_snapshot() -> list[dict]:
    _reap_gpus()
    with _gpu_lock:
        busy = dict(_busy_gpus)
    for pid in inference_pids():
        gid = cuda_visible_from_pid(pid)
        if gid is None:
            continue
        if gid not in busy:
            busy[gid] = job_id_from_pid(pid) or ""
    rows = []
    for gid in load_gpus():
        raw = busy.get(gid)
        job_id = raw if raw else None
        rows.append({"id": gid, "busy": gid in busy, "job_id": job_id})
    return rows


def _publish_status() -> None:
    write_status(
        {
            "pid": os.getpid(),
            "updated_at": db.utcnow(),
            "gpus": gpu_snapshot(),
        }
    )


def _reap_gpus() -> None:
    with _gpu_lock:
        dead = [gid for gid, th in _gpu_threads.items() if th is not None and not th.is_alive()]
        for gid in dead:
            _gpu_threads.pop(gid, None)
            _busy_gpus.pop(gid, None)


def _acquire_gpu() -> Optional[int]:
    _reap_gpus()
    occupied = set()
    for pid in inference_pids():
        gid = cuda_visible_from_pid(pid)
        if gid is not None:
            occupied.add(gid)
    with _gpu_lock:
        for gid in load_gpus():
            if gid not in _busy_gpus and gid not in occupied:
                _busy_gpus[gid] = ""
                return gid
    return None


def _bind_gpu(gpu_id: int, job_id: str) -> None:
    with _gpu_lock:
        _busy_gpus[gpu_id] = job_id


def _release_gpu(gpu_id: int) -> None:
    with _gpu_lock:
        _busy_gpus.pop(gpu_id, None)
        _gpu_threads.pop(gpu_id, None)


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
                _ffmpeg_copy_silent(source, video_out)
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
    if preview_is_current(preview_out, need_audio=False):
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


def _ffmpeg_copy_silent(src: Path, dst: Path) -> None:
    """Keep original video bitstream, drop audio so 形象预览不会出声。"""
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
                "-c:v",
                "copy",
                "-an",
                str(dst),
            ],
            check=True,
        )
    except subprocess.CalledProcessError:
        _ffmpeg_prepare_video(src, dst)


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
            "-vf",
            "scale=trunc(iw/2)*2:trunc(ih/2)*2",
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
    ffmpeg_preview_video(src, dst, silent=True)


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
            _try_start_jobs()
            _publish_status()
        except Exception:
            try:
                _publish_status()
            except Exception:
                pass
            time.sleep(2)
        try:
            from .avatars import purge_stale_uploads

            purge_stale_uploads()
            tasks.purge_expired_jobs()
        except Exception:
            pass
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
        if preview and preview_is_current(preview):
            continue
        video = Path(row["video_path"]) if row["video_path"] else None
        if video and video.exists():
            return row["id"]
    return None


def _try_start_jobs() -> None:
    while True:
        gpu_id = _acquire_gpu()
        if gpu_id is None:
            return
        try:
            job = _claim_next_job()
            if job is None:
                _release_gpu(gpu_id)
                return
            _bind_gpu(gpu_id, job["id"])
            with db.db() as conn:
                conn.execute(
                    "UPDATE jobs SET progress = ? WHERE id = ?",
                    (f"加载模型 (GPU {gpu_id})", job["id"]),
                )
            th = threading.Thread(
                target=_execute_job,
                args=(job, gpu_id),
                name=f"infer-gpu{gpu_id}",
                daemon=True,
            )
            with _gpu_lock:
                _gpu_threads[gpu_id] = th
            th.start()
        except Exception:
            _release_gpu(gpu_id)
            raise


def _live_inference_job_ids() -> set[str]:
    ids: set[str] = set()
    for pid in inference_pids():
        job_id = job_id_from_pid(pid)
        if job_id:
            ids.add(job_id)
    return ids


def _claim_next_job() -> Optional[dict]:
    live_ids = _live_inference_job_ids()
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
            if tasks.is_cancel_requested(item["id"]):
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, error = NULL, finished_at = ?, stage = ?, remaining_seconds = 0, progress = ?
                    WHERE id = ? AND status = ?
                    """,
                    ("cancelled", db.utcnow(), "cancelled", tasks.CANCEL_MESSAGE, item["id"], "queued"),
                )
                continue
            if item["id"] in live_ids:
                continue
            job = item
            break
        if job is None:
            return None
        claimed = conn.execute(
            """
            UPDATE jobs
            SET status = ?, progress = ?, stage = ?, started_at = ?, error = NULL,
                progress_percent = 0, remaining_seconds = estimated_seconds, progress_updated_at = ?
            WHERE id = ? AND status = ?
            """,
            ("running", "加载模型", "loading", db.utcnow(), db.utcnow(), job["id"], "queued"),
        )
        if not claimed.rowcount:
            return None
    return job


def _execute_job(job: dict, gpu_id: int) -> None:
    job_id = job["id"]
    job_dir = STORAGE / "jobs" / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    output_path = job_dir / "output.mp4"
    temp_dir = job_dir / "temp"

    if tasks.is_cancel_requested(job_id):
        _mark_job_cancelled(job_id)
        _release_gpu(gpu_id)
        return

    _archive_job_temp(temp_dir)

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
    env["CUDA_VISIBLE_DEVICES"] = str(gpu_id)
    log_path = job_dir / "inference.log"
    start_off = log_path.stat().st_size if log_path.exists() else 0

    try:
        log_f = open(log_path, "ab", buffering=0)
        try:
            proc = subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=log_f,
                stderr=subprocess.STDOUT,
                env=env,
                start_new_session=True,
            )
        finally:
            log_f.close()
        code = _drain_inference_log(proc, log_path, start_off, job)
        if tasks.is_cancel_requested(job_id):
            raise JobCancelled()
        if not _finalize_job_output(job_id, output_path):
            raise RuntimeError(f"合成失败，退出码 {code}")
        _mark_job_done(job_id, output_path)
    except JobCancelled:
        _mark_job_cancelled(job_id)
    except Exception as exc:
        if tasks.is_cancel_requested(job_id):
            _mark_job_cancelled(job_id)
        else:
            with db.db() as conn:
                conn.execute(
                    """
                    UPDATE jobs
                    SET status = ?, error = ?, finished_at = ?, stage = ?, remaining_seconds = 0
                    WHERE id = ? AND status = ?
                    """,
                    ("failed", str(exc), db.utcnow(), "failed", job_id, "running"),
                )
    finally:
        _release_gpu(gpu_id)


def _archive_job_temp(temp_dir: Path) -> None:
    """Move leftover temp aside. Never delete it; only task delete removes job files."""
    if not temp_dir.exists():
        return
    try:
        nonempty = any(temp_dir.iterdir())
    except OSError:
        nonempty = True
    if not nonempty:
        return
    stamp = db.utcnow().replace(":", "").replace("-", "")
    dest = temp_dir.with_name(f"temp.keep.{stamp}")
    n = 0
    while dest.exists():
        n += 1
        dest = temp_dir.with_name(f"temp.keep.{stamp}.{n}")
    temp_dir.rename(dest)
    print(f"kept previous temp as {dest}", flush=True)


def _finalize_job_output(job_id: str, output_path: Path) -> bool:
    """Accept a playable MP4, or remux temp/video.mp4 + audio if ffmpeg crashed after 100%."""
    if has_av_streams(output_path, need_audio=True):
        return True
    if output_path.exists():
        output_path.unlink(missing_ok=True)
    job_dir = STORAGE / "jobs" / job_id
    video = job_dir / "temp" / "video.mp4"
    audio = job_dir / "temp" / "audio.wav"
    if not audio.exists():
        fallback = job_dir / "audio.wav"
        if fallback.exists():
            audio = fallback
    if not video.exists() or video.stat().st_size < 1000 or not audio.exists():
        return False
    try:
        print(f"Job {job_id} remuxing temp video after inference exit", flush=True)
        mux_video_audio(video, audio, output_path)
    except Exception as exc:
        print(f"Job {job_id} remux failed: {exc}", flush=True)
        return False
    return has_av_streams(output_path, need_audio=True)


def _update_job_progress(job_id: str, fields: dict) -> None:
    if tasks.is_cancel_requested(job_id):
        return
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
    values.append("running")
    sql = f"UPDATE jobs SET {', '.join(assignments)} WHERE id = ? AND status = ?"
    with db.db() as conn:
        conn.execute(sql, values)


def _drain_inference_log(proc: subprocess.Popen, log_path: Path, start_off: int, job: dict) -> int:
    buf = ""
    with open(log_path, "rb") as rf:
        rf.seek(start_off)
        while True:
            chunk = rf.read(4096)
            if chunk:
                buf += chunk.decode("utf-8", errors="replace")
                buf = _consume_progress_buf(buf, job)
                continue
            code = proc.poll()
            if code is not None:
                extra = rf.read(4096)
                while extra:
                    buf += extra.decode("utf-8", errors="replace")
                    buf = _consume_progress_buf(buf, job)
                    extra = rf.read(4096)
                return code
            if tasks.is_cancel_requested(job.get("id")):
                _kill_process_group(proc.pid)
                deadline = time.time() + 8
                while proc.poll() is None and time.time() < deadline:
                    time.sleep(0.1)
                if proc.poll() is None:
                    _kill_process_group(proc.pid)
                raise JobCancelled()
            time.sleep(0.1)


def _consume_progress_buf(buf: str, job: dict) -> str:
    while True:
        idx_r, idx_n = buf.find("\r"), buf.find("\n")
        cuts = [i for i in (idx_r, idx_n) if i >= 0]
        if not cuts:
            return buf[-200:] if len(buf) > 800 else buf
        i = min(cuts)
        line, buf = buf[:i], buf[i + 1 :]
        try:
            fields = parse_job_line(line, steps=int(job.get("steps") or 30))
        except Exception:
            continue
        if fields:
            _update_job_progress(job["id"], fields)


def main() -> None:
    """Standalone worker process. Never kills live inference."""
    global _lock_fd
    ensure_dirs()
    db.init_db()
    fd = os.open(str(WORKER_LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        print("合成 worker 已在运行，本次退出", file=sys.stderr)
        sys.exit(0)
    _lock_fd = fd
    os.lseek(fd, 0, os.SEEK_SET)
    os.ftruncate(fd, 0)
    os.write(fd, f"{os.getpid()}\n".encode("utf-8"))
    os.fsync(fd)
    WORKER_PID_PATH.write_text(str(os.getpid()), encoding="utf-8")

    def _on_term(_signum, _frame) -> None:
        _stop.set()

    signal.signal(signal.SIGTERM, _on_term)
    signal.signal(signal.SIGINT, _on_term)
    _recover_stale()
    _publish_status()
    print(f"合成 worker 已启动 pid={os.getpid()} gpus={load_gpus()}", flush=True)
    try:
        _loop()
    finally:
        try:
            _publish_status()
        except Exception:
            pass


if __name__ == "__main__":
    main()
