"""Low-bitrate preview transcode shared by character bake and job finish."""
from __future__ import annotations

import subprocess
from pathlib import Path

# 原分辨率、视频码率 2Mbps、无音轨。scale 只把宽高收成偶数，不改分辨率。
PREVIEW_PROFILE = "orig-2m-an"


def preview_tag_path(dst: Path) -> Path:
    return dst.with_name(dst.name + ".profile")


def preview_is_current(dst: Path) -> bool:
    tag = preview_tag_path(dst)
    return (
        dst.exists()
        and dst.stat().st_size >= 1000
        and tag.exists()
        and tag.read_text(encoding="utf-8").strip() == PREVIEW_PROFILE
    )


def ffmpeg_preview_video(src: Path, dst: Path) -> None:
    """网页预览：保持原分辨率，视频 2Mbps，静音。"""
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
                "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-b:v",
                "2M",
                "-maxrate",
                "2M",
                "-bufsize",
                "4M",
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
        preview_tag_path(dst).write_text(PREVIEW_PROFILE, encoding="utf-8")
    finally:
        tmp.unlink(missing_ok=True)


def ensure_preview(src: Path, dst: Path) -> Path:
    if preview_is_current(dst):
        return dst
    if not src.exists() or src.stat().st_size < 1000:
        raise FileNotFoundError("原片不存在")
    ffmpeg_preview_video(src, dst)
    if not dst.exists() or dst.stat().st_size < 1000:
        raise RuntimeError("预览转码失败")
    return dst
