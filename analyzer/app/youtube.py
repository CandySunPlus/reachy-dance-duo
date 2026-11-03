"""YouTube Music search.

Tries ytmusicapi locally first. YouTube also blocks `music.youtube.com`
TLS handshakes from HF Space IPs, so when the local call fails we proxy
through the same ngrok helper that handles audio downloads.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import requests
from ytmusicapi import YTMusic

logger = logging.getLogger(__name__)

HELPER_URL = os.environ.get("YT_DLP_HELPER_URL", "").rstrip("/")
HELPER_SECRET = os.environ.get("YT_HELPER_SECRET", "")

_yt: YTMusic | None = None


def _client() -> YTMusic:
    global _yt
    if _yt is None:
        _yt = YTMusic()
    return _yt


def _search_via_helper(query: str, limit: int) -> list[dict[str, Any]]:
    r = requests.get(
        f"{HELPER_URL}/search",
        params={"q": query, "limit": limit},
        headers={
            "Authorization": f"Bearer {HELPER_SECRET}",
            "ngrok-skip-browser-warning": "true",
        },
        timeout=30,
    )
    if r.status_code != 200:
        raise RuntimeError(f"helper /search returned {r.status_code}: {r.text[:200]}")
    return r.json()


def _search_local(query: str, limit: int) -> list[dict[str, Any]]:
    yt = _client()
    try:
        results = yt.search(query, filter="songs", limit=limit)
    except Exception:
        global _yt
        _yt = None
        results = _client().search(query, filter="songs", limit=limit)

    tracks: list[dict[str, Any]] = []
    for item in results:
        if item.get("resultType") != "song":
            continue
        tracks.append(
            {
                "videoId": item.get("videoId"),
                "title": item.get("title", "Unknown Title"),
                "artists": item.get("artists", []),
                "thumbnails": item.get("thumbnails", []),
                "album": item.get("album"),
                "duration": item.get("duration"),
                "duration_seconds": item.get("duration_seconds"),
            }
        )
    return tracks


def search(query: str, limit: int = 10) -> list[dict[str, Any]]:
    if HELPER_URL and HELPER_SECRET:
        try:
            return _search_via_helper(query, limit)
        except Exception as e:
            logger.warning(f"helper search failed, falling back to local: {e}")
    return _search_local(query, limit)


def youtube_url(video_id: str) -> str:
    return f"https://www.youtube.com/watch?v={video_id}"
