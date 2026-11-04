"""FastAPI entrypoint for the Reachy Dance Duo analyzer."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import analyze as analyze_mod
from . import download, qq

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

# Optional static page served from the same origin (self-hosted
# deployments mount the Dance Duo checkout here).
SITE_DIR = Path(os.environ.get("SITE_DIR", "/srv"))

qq_service = qq.QQMusicService()


class QQAnalyzeBody(BaseModel):
    """A QQ Music track selection; identifiers come from ``/qq/search``."""

    mid: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9]+$")
    media_mid: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9]+$")
    song_type: int = Field(default=0, ge=0)


def _quality_from_path(path: Path, mid: str) -> str | None:
    prefix = f"qq-{mid}-"
    return path.stem[len(prefix) :] if path.stem.startswith(prefix) else None


async def _resolve_or_http(body: QQAnalyzeBody) -> qq.ResolvedTrack:
    """Resolve a track and translate source failures into HTTP statuses."""
    try:
        return await qq_service.resolve(body.mid, body.media_mid, body.song_type)
    except qq.QQNotLoggedIn:
        raise HTTPException(status_code=401, detail="qq_not_logged_in")
    except qq.QQAuthError:
        raise HTTPException(status_code=401, detail="qq_login_expired")
    except qq.QQSongUnavailable:
        raise HTTPException(status_code=404, detail="qq_song_unavailable")
    except qq.QQUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.get("/health")
def health() -> dict[str, object]:
    return {
        "ok": True,
        "cache": download.cache_stats(),
        "qq": qq_service.state(),
    }


@app.get("/qq/state")
def qq_state() -> dict[str, object]:
    return qq_service.state()


@app.post("/qq/login/qrcode")
async def qq_login_qrcode() -> dict[str, object]:
    try:
        return await qq_service.login_qrcode()
    except Exception as exc:  # noqa: BLE001 - surface the QQ/network reason
        logger.exception("QQ login QR failed")
        raise HTTPException(status_code=502, detail=f"qq login unavailable: {exc}")


@app.get("/qq/login/status")
async def qq_login_status(session: str = Query(..., min_length=8, max_length=64)) -> dict[str, object]:
    try:
        return await qq_service.login_status(session)
    except qq.QQAuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("QQ login poll failed")
        raise HTTPException(status_code=502, detail=f"qq login poll failed: {exc}")


@app.post("/qq/logout")
async def qq_logout() -> dict[str, bool]:
    await qq_service.logout()
    return {"ok": True}


@app.get("/qq/search")
async def qq_search(
    q: str = Query(..., min_length=1, max_length=200),
    limit: int = Query(8, ge=1, le=30),
) -> list[dict[str, object]]:
    try:
        return await qq_service.search(q, limit=limit)
    except qq.QQAuthError:
        raise HTTPException(status_code=401, detail="qq_login_expired")
    except qq.QQUnavailable as exc:
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("QQ search failed")
        raise HTTPException(status_code=502, detail=f"qq search failed: {exc}")


@app.post("/qq/analyze")
async def qq_analyze(body: QQAnalyzeBody) -> dict[str, object]:
    prefix = f"qq-{body.mid}-"
    path = download.find(prefix)
    quality = _quality_from_path(path, body.mid) if path is not None else None

    if path is None:
        try:
            resolved = await _resolve_or_http(body)
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("QQ resolve failed")
            raise HTTPException(status_code=502, detail=f"qq resolve failed: {exc}")

        # QQ picks a random CDN node per resolution; a dead node is worth one
        # fresh resolve. This is still the same source, not a fallback.
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                path = await download.download(
                    resolved.url,
                    f"qq-{body.mid}-{resolved.quality}",
                )
                quality = resolved.quality
                break
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning("QQ download attempt %d failed: %s", attempt + 1, exc)
                if attempt == 0:
                    resolved = await _resolve_or_http(body)
        else:
            raise HTTPException(status_code=502, detail=f"qq download failed: {last_error}")

    try:
        # librosa is CPU-bound for seconds; keep the event loop free so the
        # page's assets/health requests do not stall behind analysis.
        result = await asyncio.to_thread(analyze_mod.analyze, path)
    except ValueError as exc:
        path.unlink(missing_ok=True)  # unplayable/silent; next attempt re-downloads
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001
        logger.exception("analyze failed")
        raise HTTPException(status_code=500, detail=f"analyze failed: {exc}")

    result["audio_url"] = f"/audio/{path.name}"
    result["mid"] = body.mid
    result["quality"] = quality
    return result


@app.get("/audio/{filename}")
def audio_file(filename: str) -> FileResponse:
    if "/" in filename or "\\" in filename or ".." in filename or filename.endswith(".part"):
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
            "endpoints": [
                "/health",
                "/qq/state",
                "/qq/login/qrcode (POST)",
                "/qq/login/status?session=",
                "/qq/logout (POST)",
                "/qq/search?q=",
                "/qq/analyze (POST)",
                "/audio/<file>",
            ],
            "site": str(SITE_DIR) if SITE_DIR.is_dir() else None,
            "quality": qq.configured_quality(),
        }
    )


# Mounted last so every API route above wins. html=True serves index.html for
# "/" and for unknown paths (the page is a single document plus ES modules).
if SITE_DIR.is_dir():
    app.mount("/", StaticFiles(directory=str(SITE_DIR), html=True), name="site")
