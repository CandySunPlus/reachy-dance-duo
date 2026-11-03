"""yt-dlp wrapper with on-disk cache.

Cache lives at /data/audio/<video_id>.<ext>. We keep the original container
(usually m4a or webm) so PyAV / browser can decode it directly with no
re-encode. The cache is LRU-evicted at MAX_CACHE_BYTES.
"""

from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

import requests
import yt_dlp  # type: ignore

from .youtube import youtube_url

logger = logging.getLogger(__name__)

CACHE_DIR = Path(os.environ.get("AUDIO_CACHE_DIR", "/data/audio"))
MAX_CACHE_BYTES = int(os.environ.get("MAX_CACHE_BYTES", str(2 * 1024 * 1024 * 1024)))

CACHE_DIR.mkdir(parents=True, exist_ok=True)


# Path 1: ngrok-tunneled local helper running on Carson's Mac. Set both env
# vars to enable. The helper does the actual yt-dlp call from a residential
# IP that YouTube does not block. This is the path that actually works.
HELPER_URL = os.environ.get("YT_DLP_HELPER_URL", "").rstrip("/")
HELPER_SECRET = os.environ.get("YT_HELPER_SECRET", "")

# Path 2: in-Space yt-dlp with a cookies.txt secret. Currently does not
# work because YouTube TLS-resets requests from HF egress IPs even with
# valid cookies. Kept as a fallback for if/when YouTube relaxes the block.
COOKIES_PATH = Path(os.environ.get("COOKIES_PATH", "/data/cookies.txt"))


def _materialize_cookies() -> Path | None:
    inline = os.environ.get("YT_COOKIES")
    if inline:
        try:
            COOKIES_PATH.parent.mkdir(parents=True, exist_ok=True)
            COOKIES_PATH.write_text(inline)
            logger.info(f"[cookies] wrote {len(inline)} bytes to {COOKIES_PATH}")
        except OSError as e:
            logger.error(f"[cookies] failed to write {COOKIES_PATH}: {e}")
            return None
    if COOKIES_PATH.exists() and COOKIES_PATH.stat().st_size > 0:
        return COOKIES_PATH
    logger.warning("[cookies] no cookies file present; YouTube will likely block requests from this Space")
    return None


_COOKIE_FILE = _materialize_cookies()


def _cached_path(video_id: str) -> Path | None:
    for ext in (".m4a", ".webm", ".mp4", ".opus", ".mp3"):
        p = CACHE_DIR / f"{video_id}{ext}"
        if p.exists():
            return p
    return None


def _evict_if_needed() -> None:
    files = sorted(CACHE_DIR.glob("*"), key=lambda p: p.stat().st_atime)
    total = sum(p.stat().st_size for p in files)
    while total > MAX_CACHE_BYTES and files:
        victim = files.pop(0)
        try:
            total -= victim.stat().st_size
            victim.unlink()
            logger.info(f"[cache] evicted {victim.name}")
        except OSError:
            pass


def fetch(video_id: str) -> Path:
    """Download <video_id> if not cached, return path to the audio file."""
    existing = _cached_path(video_id)
    if existing is not None:
        os.utime(existing, None)  # touch atime for LRU
        return existing

    if HELPER_URL and HELPER_SECRET:
        return _fetch_via_helper(video_id)

    return _fetch_via_local_ytdlp(video_id)


def _fetch_via_helper(video_id: str) -> Path:
    """Proxy to the local yt-dlp helper running on Carson's Mac via ngrok."""
    logger.info(f"[helper] requesting {video_id} from {HELPER_URL}")
    r = requests.post(
        f"{HELPER_URL}/download",
        json={"video_id": video_id},
        headers={
            "Authorization": f"Bearer {HELPER_SECRET}",
            # ngrok-free.dev shows a browser warning page for browser UAs;
            # this header bypasses it for our backend-to-backend traffic.
            "ngrok-skip-browser-warning": "true",
        },
        stream=True,
        timeout=300,
    )
    if r.status_code != 200:
        raise RuntimeError(f"helper returned {r.status_code}: {r.text[:200]}")

    # Pick extension from helper's content-disposition or content-type.
    ext = ".m4a"
    cd = r.headers.get("content-disposition", "")
    if "filename=" in cd:
        name = cd.split("filename=", 1)[1].strip("\"';")
        if "." in name:
            ext = "." + name.rsplit(".", 1)[1].lower()
    elif "content-type" in r.headers:
        ext = {
            "audio/mp4": ".m4a",
            "audio/webm": ".webm",
            "audio/ogg": ".opus",
            "audio/mpeg": ".mp3",
        }.get(r.headers["content-type"].split(";")[0].strip(), ".m4a")

    out = CACHE_DIR / f"{video_id}{ext}"
    with out.open("wb") as fh:
        for chunk in r.iter_content(chunk_size=64 * 1024):
            if chunk:
                fh.write(chunk)
    logger.info(f"[helper] cached {out.name} ({out.stat().st_size} bytes)")
    _evict_if_needed()
    return out


def _fetch_via_local_ytdlp(video_id: str) -> Path:
    out_template = str(CACHE_DIR / f"{video_id}.%(ext)s")
    ydl_opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": out_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "extractor_args": {
            "youtube": {
                # web matches the cookies' fingerprint. Single client so we
                # do not silently fail over to a less-cookie-compatible one.
                "player_client": ["web"],
            }
        },
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
        "restrictfilenames": True,
        "sleep_interval": 1,
        "max_sleep_interval": 3,
    }
    if _COOKIE_FILE is not None:
        ydl_opts["cookiefile"] = str(_COOKIE_FILE)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore[arg-type]
        ydl.download([youtube_url(video_id)])

    p = _cached_path(video_id)
    if p is None:
        raise RuntimeError(f"yt-dlp finished but no file for {video_id} in {CACHE_DIR}")

    _evict_if_needed()
    return p


def cache_stats() -> dict[str, Any]:
    files = list(CACHE_DIR.glob("*"))
    return {
        "count": len(files),
        "bytes": sum(p.stat().st_size for p in files),
        "max_bytes": MAX_CACHE_BYTES,
    }


def clear_cache() -> None:
    if CACHE_DIR.exists():
        shutil.rmtree(CACHE_DIR)
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
