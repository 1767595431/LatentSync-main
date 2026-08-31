"""Shared GPU/worker observation for the API process.

The API never starts, stops, or kills synthesis. It only reads worker status.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Optional

from .config import WORKER_LOCK_PATH, WORKER_STATUS_PATH, ensure_dirs, load_gpus

_JOB_IN_CMDLINE = re.compile(r"/jobs/([0-9a-fA-F-]{8,})/")


def inference_pids() -> list[int]:
    try:
        result = subprocess.run(
            ["pgrep", "-af", "scripts.inference"],
            capture_output=True,
            text=True,
        )
    except Exception:
        return []
    pids: list[int] = []
    for line in result.stdout.splitlines():
        if "pgrep" in line or "/bin/bash" in line or "extglob" in line:
            continue
        if "scripts.inference" in line and "python" in line.lower():
            try:
                pids.append(int(line.split(None, 1)[0]))
            except ValueError:
                continue
    return pids


def cuda_visible_from_pid(pid: int) -> Optional[int]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return None
    for item in raw.split(b"\0"):
        if item.startswith(b"CUDA_VISIBLE_DEVICES="):
            val = item.split(b"=", 1)[1].decode("utf-8", "replace").strip()
            if not val:
                return None
            try:
                return int(val.split(",")[0].strip())
            except ValueError:
                return None
    return None


def job_id_from_pid(pid: int) -> Optional[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return None
    text = raw.replace(b"\0", b" ").decode("utf-8", "replace")
    m = _JOB_IN_CMDLINE.search(text)
    return m.group(1) if m else None


def write_status(payload: dict) -> None:
    ensure_dirs()
    tmp = WORKER_STATUS_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(WORKER_STATUS_PATH)


def read_status() -> dict:
    try:
        return json.loads(WORKER_STATUS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def worker_alive() -> bool:
    import fcntl

    ensure_dirs()
    fd = os.open(str(WORKER_LOCK_PATH), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def gpu_snapshot() -> list[dict]:
    data = read_status()
    gpus = data.get("gpus")
    if isinstance(gpus, list) and gpus:
        return gpus
    busy_by_gpu: dict[int, Optional[str]] = {}
    for pid in inference_pids():
        gid = cuda_visible_from_pid(pid)
        if gid is None:
            continue
        busy_by_gpu[gid] = job_id_from_pid(pid)
    rows = []
    for gid in load_gpus():
        job_id = busy_by_gpu.get(gid)
        rows.append({"id": gid, "busy": gid in busy_by_gpu, "job_id": job_id})
    return rows


def inference_running() -> bool:
    if any(item.get("busy") for item in gpu_snapshot()):
        return True
    return bool(inference_pids())
