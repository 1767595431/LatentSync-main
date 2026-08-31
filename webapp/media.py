"""Low-bitrate preview transcode shared by character bake and job finish."""
from __future__ import annotations

import subprocess
from pathlib import Path


def ffmpeg_preview_video(src: Path, dst: Path) -> None:
    """网页预览用低码率片：最高 720p、CRF28、faststart。合成仍用原片。"""
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


def ensure_preview(src: Path, dst: Path) -> Path:
    if dst.exists() and dst.stat().st_size >= 1000:
        return dst
    if not src.exists() or src.stat().st_size < 1000:
        raise FileNotFoundError("原片不存在")
    ffmpeg_preview_video(src, dst)
    if not dst.exists() or dst.stat().st_size < 1000:
        raise RuntimeError("预览转码失败")
    return dst
