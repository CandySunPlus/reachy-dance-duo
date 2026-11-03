"""FastAPI entrypoint."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analyze as analyze_mod
from . import download, youtube

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("analyzer")

app = FastAPI(title="Reachy Dance Duo Analyzer", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"https://[a-z0-9-]+\.hf\.space|http://localhost(:\d+)?|http://127\.0\.0\.1(:\d+)?",
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)


VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,15}$")

# Optional static page served from the same origin (self-hosted
# deployments mount the Dance Duo checkout here).
SITE_DIR = Path(os.environ.get("SITE_DIR", "/srv"))


class AnalyzeBody(BaseModel):
    video_id: str


# Raw-body uploads: no python-multipart dependency, and the decoder
# (librosa/ffmpeg via av) sniffs the container anyway.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
UPLOAD_EXTS = {".m4a", ".mp3", ".wav", ".ogg", ".opus", ".flac", ".webm", ".mp4", ".aac"}


@app.get("/health")
def health() -> dict[str, object]:
    cookies_present = download.COOKIES_PATH.exists() and download.COOKIES_PATH.stat().st_size > 0
    helper_configured = bool(download.HELPER_URL and download.HELPER_SECRET)
    return {
        "ok": True,
        "cache": download.cache_stats(),
        "cookies": cookies_present,
        "helper": helper_configured,
        "helper_url": download.HELPER_URL if helper_configured else None,
    }


@app.get("/search")
def search(q: str = Query(..., min_length=1, max_length=200), limit: int = 10) -> list[dict]:
    try:
        return youtube.search(q, limit=limit)
    except Exception as e:
        logger.exception("search failed")
        raise HTTPException(status_code=502, detail=f"search failed: {e}")


@app.post("/analyze")
def analyze_endpoint(body: AnalyzeBody) -> dict:
    if not VIDEO_ID_RE.match(body.video_id):
        raise HTTPException(status_code=400, detail="invalid video_id")
    try:
        path = download.fetch(body.video_id)
    except Exception as e:
        logger.exception("download failed")
        raise HTTPException(status_code=502, detail=f"download failed: {e}")
    try:
        result = analyze_mod.analyze(path)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("analyze failed")
        raise HTTPException(status_code=500, detail=f"analyze failed: {e}")
    result["audio_url"] = f"/audio/{path.name}"
    result["video_id"] = body.video_id
    return result


@app.post("/analyze_upload")
async def analyze_upload(request: Request, name: str = Query("upload", max_length=200)) -> dict:
    """Analyze an uploaded audio file — no YouTube, no helper.

    Body: the raw audio bytes. Query ``name``: the original filename (its
    extension names the cached file; the decoder sniffs the container).
    Response: same shape as ``/analyze``, with ``video_id`` set to None.
    """
    data = await request.body()
    if not data:
        raise HTTPException(status_code=400, detail="empty body")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="file too large")

    ext = Path(name).suffix.lower()
    if ext not in UPLOAD_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported extension {ext!r}; use one of {sorted(UPLOAD_EXTS)}",
        )
    stem = re.sub(r"[^A-Za-z0-9_-]+", "", Path(name).stem)[:40] or "audio"
    stored = Path(download.CACHE_DIR) / f"upload-{stem}-{uuid.uuid4().hex[:8]}{ext}"
    try:
        stored.write_bytes(data)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"could not store upload: {e}")

    try:
        # librosa is CPU-bound for seconds; keep the event loop free so the
        # page's assets/health requests do not stall behind an upload.
        result = await asyncio.to_thread(analyze_mod.analyze, stored)
    except ValueError as e:
        stored.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        logger.exception("analyze failed")
        stored.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"analyze failed: {e}")

    try:
        download._evict_if_needed()  # keep the LRU cache bounded
    except Exception:
        pass

    result["audio_url"] = f"/audio/{stored.name}"
    result["video_id"] = None
    return result


@app.get("/audio/{filename}")
def audio_file(filename: str) -> FileResponse:
    if "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="invalid filename")
    p = Path(download.CACHE_DIR) / filename
    if not p.exists():
        raise HTTPException(status_code=404, detail="not found")
    media_type = {
        ".m4a": "audio/mp4",
        ".mp4": "audio/mp4",
        ".webm": "audio/webm",
        ".ogg": "audio/ogg",
        ".opus": "audio/ogg",
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".flac": "audio/flac",
        ".aac": "audio/aac",
    }.get(p.suffix.lower(), "application/octet-stream")
    return FileResponse(p, media_type=media_type, filename=p.name)


@app.get("/api")
def index() -> JSONResponse:
    return JSONResponse(
        {
            "name": "reachy-dance-duo-analyzer",
            "endpoints": ["/health", "/search?q=", "/analyze (POST)", "/analyze_upload (POST, raw audio body)", "/audio/<id>.<ext>", "/yt_debug?video_id="],
            "site": str(SITE_DIR) if SITE_DIR.is_dir() else None,
        }
    )


@app.get("/yt_debug")
def yt_debug(video_id: str = Query(..., min_length=6, max_length=15)) -> dict:
    """Probe yt-dlp without doing a full download.
    Returns the version and the result of an info-only extraction.
    Useful for diagnosing extractor / cookie / fingerprint issues."""
    if not VIDEO_ID_RE.match(video_id):
        raise HTTPException(status_code=400, detail="invalid video_id")
    import yt_dlp  # local import so /health works even if yt-dlp is broken

    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "simulate": True,
        "skip_download": True,
        "extractor_args": {"youtube": {"player_client": ["web"]}},
        "user_agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
        ),
    }
    if download._COOKIE_FILE is not None:
        ydl_opts["cookiefile"] = str(download._COOKIE_FILE)

    out: dict = {"yt_dlp_version": yt_dlp.version.__version__, "cookies_used": download._COOKIE_FILE is not None}
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:  # type: ignore[arg-type]
            info = ydl.extract_info(youtube.youtube_url(video_id), download=False)
        if info is None:
            out["status"] = "no_info"
            return out
        formats = info.get("formats", []) or []
        out["status"] = "ok"
        out["title"] = info.get("title")
        out["duration"] = info.get("duration")
        out["format_count"] = len(formats)
        out["audio_formats"] = [
            {"format_id": f.get("format_id"), "ext": f.get("ext"), "abr": f.get("abr"), "acodec": f.get("acodec")}
            for f in formats
            if f.get("acodec") and f.get("acodec") != "none" and not f.get("vcodec") or f.get("vcodec") == "none"
        ][:8]
    except Exception as e:
        out["status"] = "error"
        out["error"] = str(e)[:600]
    return out


# Mounted last so every API route above wins. html=True serves index.html for
# "/" and for unknown paths (the page is a single document plus ES modules).
if SITE_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(SITE_DIR), html=True), name="site")
