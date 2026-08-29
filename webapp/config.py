from pathlib import Path

ROOT = Path("/root/AI/wav2lip/LatentSync-main")
WEBAPP_DIR = ROOT / "webapp"
STORAGE = WEBAPP_DIR / "storage"
DB_PATH = STORAGE / "app.db"

PYTHON = "/usr/local/miniconda3/envs/latentsync/bin/python"

CHUNK_SIZE = 2 * 1024 * 1024
MAX_VIDEO_SIZE = 2 * 1024 * 1024 * 1024
MAX_AUDIO_SIZE = 300 * 1024 * 1024

ALLOWED_STEPS = (20, 30, 40, 50, 60, 70, 80)
DEFAULT_STEPS = 20
GUIDANCE_SCALE = 1.5

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg"}

UNET_CONFIG = ROOT / "configs" / "unet" / "stage2_512.yaml"
UNET_CKPT = ROOT / "checkpoints" / "latentsync_unet.pt"

HOST = "0.0.0.0"
PORT = 8765

ADMIN_KEY = "King"
ADMIN_COOKIE = "qemix_gate"
SESSION_IDLE_SECONDS = 30 * 60


def ensure_dirs() -> None:
    for name in ("chunks", "uploads", "characters", "jobs"):
        (STORAGE / name).mkdir(parents=True, exist_ok=True)
