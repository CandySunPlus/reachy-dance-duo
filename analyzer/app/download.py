"""Audio cache for the analyzer.

Files are stored under ``CACHE_DIR`` and LRU-evicted at ``MAX_CACHE_BYTES``.
The only writer is ``download()``; the QQ module decides what to fetch and
``main`` decides what to analyze.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("AUDIO_CACHE_DIR", "/data/audio"))
MAX_CACHE_BYTES = int(os.environ.get("MAX_CACHE_BYTES", str(2 * 1024 * 1024 * 1024)))

try:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
except OSError as exc:  # local dev without /data; the container always has it
    logger.warning("audio cache directory unavailable: %s", exc)

AUDIO_EXTS = {".mp3", ".m4a", ".flac", ".ogg", ".aac", ".wav", ".opus"}
CONTENT_TYPE_EXT = {
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a",
    "audio/flac": ".flac",
    "audio/ogg": ".ogg",
    "audio/aac": ".aac",
    "audio/wav": ".wav",
}

DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
    ),
    "Referer": "https://y.qq.com/",
}


def find(prefix: str) -> Path | None:
    """Return the newest cached audio file whose name starts with ``prefix``."""
    matches = [
        path
        for path in CACHE_DIR.glob(f"{prefix}*")
        if path.is_file() and path.suffix.lower() in AUDIO_EXTS
    ]
    if not matches:
        return None
    newest = max(matches, key=lambda path: path.stat().st_mtime)
    os.utime(newest, None)  # touch atime for LRU
    return newest


def extension_from_url(url: str) -> str | None:
    suffix = Path(urlsplit(url).path).suffix.lower()
    return suffix if suffix in AUDIO_EXTS else None


def _evict_if_needed() -> None:
    files = sorted(CACHE_DIR.glob("*"), key=lambda path: path.stat().st_atime)
    total = sum(path.stat().st_size for path in files)
    while total > MAX_CACHE_BYTES and files:
        victim = files.pop(0)
        try:
            total -= victim.stat().st_size
            victim.unlink()
            logger.info("[cache] evicted %s", victim.name)
        except OSError:
            pass


async def download(
    url: str,
    key: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> Path:
    """Download ``url`` to ``<key><ext>``, atomically, and return the path."""
    own_client = client is None
    if client is None:
        client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(60.0, connect=15.0),
            headers=DOWNLOAD_HEADERS,
        )

    tmp: Path | None = None
    try:
        async with client.stream("GET", url) as response:
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";")[0].strip()
            ext = (
                extension_from_url(url)
                or CONTENT_TYPE_EXT.get(content_type)
                or ".mp3"
            )
            dest = CACHE_DIR / f"{key}{ext}"
            tmp = dest.with_suffix(dest.suffix + ".part")
            with tmp.open("wb") as handle:
                async for chunk in response.aiter_bytes(64 * 1024):
                    if chunk:
                        handle.write(chunk)
            if tmp.stat().st_size == 0:
                raise RuntimeError("empty download")
            os.replace(tmp, dest)
    except Exception:
        if tmp is not None:
            tmp.unlink(missing_ok=True)
        raise
    finally:
        if own_client:
            await client.aclose()

    _evict_if_needed()
    return dest


def cache_stats() -> dict[str, Any]:
    files = list(CACHE_DIR.glob("*"))
    return {
        "files": len(files),
        "bytes": sum(path.stat().st_size for path in files),
        "max_bytes": MAX_CACHE_BYTES,
        "dir": str(CACHE_DIR),
    }


def clear_cache() -> None:
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
