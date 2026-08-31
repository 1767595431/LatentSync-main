from urllib.parse import urlparse

from fastapi import Request

_TAILS = (
    "/login.html",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/admin",
)

_MEDIA_SUFFIXES = (
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".svg",
    ".mp4",
    ".mov",
    ".mkv",
    ".webm",
    ".avi",
    ".wav",
    ".mp3",
    ".m4a",
    ".aac",
    ".flac",
    ".ogg",
)


def _looks_like_storage_or_media(path: str) -> bool:
    """Disk/media paths must not be treated as reverse-proxy prefixes (that would serve the homepage)."""
    lower = (path or "").split("?", 1)[0].lower()
    stripped = lower.rstrip("/") or "/"
    if stripped == "/storage" or lower.startswith("/storage/"):
        return True
    if "/webapp/storage/" in lower:
        return True
    return any(lower.endswith(ext) for ext in _MEDIA_SUFFIXES)


def _norm_prefix(value: str) -> str:
    text = (value or "").strip()
    if not text or text == "/":
        return ""
    if not text.startswith("/"):
        text = "/" + text
    return text.rstrip("/")


def prefix_from_path(path: str) -> str:
    p = (path or "/").split("?", 1)[0]
    if len(p) > 1:
        p = p.rstrip("/") or "/"
    for tail in _TAILS:
        if p == tail:
            return ""
        if p.endswith(tail) and len(p) > len(tail):
            return p[: -len(tail)]
    if p == "/":
        return ""
    return p


def public_prefix(request: Request) -> str:
    headers = request.headers
    for key in ("x-forwarded-prefix", "x-script-name"):
        val = (headers.get(key) or "").strip()
        if val:
            return _norm_prefix(val)
    original = headers.get("x-original-uri") or headers.get("x-forwarded-uri") or ""
    if original:
        return prefix_from_path(original.split("?", 1)[0])
    referer = headers.get("referer") or ""
    if referer:
        return prefix_from_path(urlparse(referer).path)
    root = (request.scope.get("root_path") or "").strip()
    if root:
        return _norm_prefix(root)
    return ""


def split_public_path(path: str) -> tuple[str, str]:
    """Split a public URL path into (proxy_prefix, app_path)."""
    p = path or "/"
    markers = (
        "/admin/assets/",
        "/api/",
        "/admin/",
        "/login.html",
        "/openapi.json",
        "/favicon.svg",
        "/favicon.png",
        "/docs/",
        "/redoc/",
    )
    for marker in markers:
        idx = p.find(marker)
        if idx >= 0:
            return p[:idx], p[idx:]
    for marker in ("/docs", "/redoc", "/admin", "/api"):
        if p == marker or p.endswith(marker):
            idx = len(p) - len(marker)
            return p[:idx], p[idx:]
    if p in ("/", ""):
        return "", "/"
    if _looks_like_storage_or_media(p):
        return "", p
    return p.rstrip("/") or "", "/"


def cookie_path(request: Request) -> str:
    prefix = public_prefix(request)
    return prefix if prefix else "/"
