"""Low-bitrate preview transcode shared by character bake and job finish."""
from __future__ import annotations

import fcntl
import os
import subprocess
from pathlib import Path

# 形象预览：原分辨率、视频 2Mbps、无音轨。
CHAR_PREVIEW_PROFILE = "orig-2m-an"
# 成片预览：原分辨率、视频 2Mbps、AAC 44.1kHz（浏览器可播）。
JOB_PREVIEW_PROFILE = "orig-2m-aac44"
PREVIEW_PROFILE = CHAR_PREVIEW_PROFILE


def preview_tag_path(dst: Path) -> Path:
    return dst.with_name(dst.name + ".profile")


def preview_lock_path(dst: Path) -> Path:
    return dst.with_name(dst.name + ".lock")


def _has_streams(path: Path, *, need_audio: bool) -> bool:
    if not path.exists() or path.stat().st_size < 1000:
        return False
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "stream=codec_type",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except Exception:
        return False
    kinds = {line.strip() for line in (result.stdout or "").splitlines() if line.strip()}
    if "video" not in kinds:
        return False
    if need_audio and "audio" not in kinds:
        return False
    return True


def preview_is_current(dst: Path, profile: str = CHAR_PREVIEW_PROFILE, *, need_audio: bool = False) -> bool:
    tag = preview_tag_path(dst)
    if not (
        dst.exists()
        and dst.stat().st_size >= 1000
        and tag.exists()
        and tag.read_text(encoding="utf-8").strip() == profile
    ):
        return False
    return _has_streams(dst, need_audio=need_audio)


def ffmpeg_preview_video(src: Path, dst: Path, *, silent: bool = True) -> None:
    """网页预览：保持原分辨率，视频 2Mbps。形象静音，成片保留可播 AAC。"""
    profile = CHAR_PREVIEW_PROFILE if silent else JOB_PREVIEW_PROFILE
    need_audio = not silent
    dst.parent.mkdir(parents=True, exist_ok=True)
    lock_path = preview_lock_path(dst)
    with open(lock_path, "a+", encoding="utf-8") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        if preview_is_current(dst, profile, need_audio=need_audio):
            return
        tmp = dst.with_name(f"{dst.stem}.tmp.{os.getpid()}.{os.urandom(4).hex()}.mp4")
        cmd = [
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
        ]
        if silent:
            cmd += ["-an", str(tmp)]
        else:
            cmd += [
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-ar",
                "44100",
                "-ac",
                "1",
                str(tmp),
            ]
        try:
            subprocess.run(cmd, check=True)
            if not _has_streams(tmp, need_audio=need_audio):
                raise RuntimeError("预览转码失败")
            tmp.replace(dst)
            preview_tag_path(dst).write_text(profile, encoding="utf-8")
        finally:
            tmp.unlink(missing_ok=True)


def ensure_preview(src: Path, dst: Path, *, silent: bool = False) -> Path:
    profile = CHAR_PREVIEW_PROFILE if silent else JOB_PREVIEW_PROFILE
    if preview_is_current(dst, profile, need_audio=not silent):
        return dst
    if not src.exists() or src.stat().st_size < 1000:
        raise FileNotFoundError("原片不存在")
    ffmpeg_preview_video(src, dst, silent=silent)
    if not preview_is_current(dst, profile, need_audio=not silent):
        raise RuntimeError("预览转码失败")
    return dst
