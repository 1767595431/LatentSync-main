import sys
from pathlib import Path
from typing import Any, Dict, List

from omegaconf import OmegaConf

ROOT = Path(__file__).resolve().parent.parent
WEBAPP_DIR = ROOT / "webapp"
STORAGE = WEBAPP_DIR / "storage"
DB_PATH = STORAGE / "app.db"
CONFIG_PATH = ROOT / "config.yaml"
WORKER_STATUS_PATH = STORAGE / "worker_status.json"
WORKER_LOCK_PATH = STORAGE / "worker.lock"
WORKER_PID_PATH = STORAGE / "worker.pid"
WORKER_LOG_PATH = STORAGE / "worker.log"

PYTHON = sys.executable

CHUNK_SIZE = 2 * 1024 * 1024
MAX_VIDEO_SIZE = 2 * 1024 * 1024 * 1024
MAX_AUDIO_SIZE = 300 * 1024 * 1024

ALLOWED_STEPS = (30, 50, 80)
DEFAULT_STEPS = 30
GUIDANCE_SCALE = 1.5

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}

UNET_CONFIG = ROOT / "configs" / "unet" / "stage2_512.yaml"
UNET_CKPT = ROOT / "checkpoints" / "latentsync_unet.pt"

ADMIN_COOKIE = "qemix_gate"
SESSION_IDLE_SECONDS = 30 * 60
JOB_TTL_DAYS = 15


def load_yaml() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        data = OmegaConf.to_container(OmegaConf.load(CONFIG_PATH), resolve=True) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def server_host() -> str:
    return str(load_yaml().get("host") or "0.0.0.0").strip() or "0.0.0.0"


def server_port() -> int:
    try:
        return int(load_yaml().get("port") or 8811)
    except (TypeError, ValueError):
        return 8811


def admin_key() -> str:
    val = load_yaml().get("admin_key")
    text = str(val).strip() if val is not None else ""
    return text or "King"


def load_gpus() -> List[int]:
    """Read GPU ids from config.yaml. One synthesis job per GPU."""
    raw = load_yaml().get("gpus", [0])
    ids: List[int] = []
    if isinstance(raw, str):
        parts = raw.replace("，", ",").split(",")
        raw = [p.strip() for p in parts if p.strip()]
    elif isinstance(raw, int):
        raw = [raw]
    elif not isinstance(raw, (list, tuple)):
        raw = [0]
    seen = set()
    for item in raw:
        try:
            gid = int(item)
        except (TypeError, ValueError):
            continue
        if gid < 0 or gid in seen:
            continue
        seen.add(gid)
        ids.append(gid)
    return ids or [0]


def job_ttl_days() -> int:
    raw = load_yaml().get("job_ttl_days", JOB_TTL_DAYS)
    try:
        days = int(raw)
    except (TypeError, ValueError):
        return JOB_TTL_DAYS
    return days if days > 0 else JOB_TTL_DAYS


def ensure_dirs() -> None:
    STORAGE.mkdir(parents=True, exist_ok=True)
    for name in ("chunks", "uploads", "characters", "jobs"):
        (STORAGE / name).mkdir(parents=True, exist_ok=True)
