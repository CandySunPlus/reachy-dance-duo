"""QQ Music source: login, credential persistence, search, URL resolution.

The browser never sees a playback URL or a credential. This module resolves a
short-lived QQ Music CDN URL, and the analyzer downloads it into the audio
cache so the page only ever plays a same-origin file.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import random
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from qqmusic_api import (
    ApiDataError,
    Client,
    Credential,
    CredentialExpiredError,
    CredentialInvalidError,
    CredentialRefreshError,
    HTTPError,
    LoginAuthExpiredError,
    LoginError,
    NetworkError,
)
from qqmusic_api.models.login import QR, QRCodeLoginEvents, QRLoginType
from qqmusic_api.modules.search import SearchType
from qqmusic_api.modules.song import SongFileInfo, SongFileType

logger = logging.getLogger(__name__)

CREDENTIAL_PATH = Path(os.environ.get("QQ_CREDENTIAL_PATH", "/data/qq/credential.json"))
QR_TTL_SECONDS = int(os.environ.get("QQ_QR_TTL_SECONDS", "180"))
FALLBACK_CDN = "https://isure.stream.qqmusic.qq.com/"

QUALITY_MAP: dict[str, SongFileType] = {
    "standard": SongFileType.MP3_128,
    "higher": SongFileType.MP3_320,
    "exhigh": SongFileType.ACC_192,
    "lossless": SongFileType.FLAC,
    "hires": SongFileType.FLAC,
}

# Highest first. Resolution walks this list from the requested tier down.
QUALITY_ORDER = ["hires", "lossless", "exhigh", "higher", "standard"]

AUTH_ERRORS = (
    CredentialExpiredError,
    CredentialInvalidError,
    LoginAuthExpiredError,
    LoginError,
)


class QQError(Exception):
    """Base class for QQ Music source failures."""


class QQNotLoggedIn(QQError):
    """No credential has been stored yet."""


class QQAuthError(QQError):
    """The credential is expired and could not be refreshed."""


class QQSongUnavailable(QQError):
    """QQ Music has no playable file for the requested track."""


class QQUnavailable(QQError):
    """QQ Music's upstream API or network is unavailable."""


@dataclass(frozen=True)
class ResolvedTrack:
    """A short-lived QQ Music playback URL and the tier it actually resolved to."""

    url: str
    quality: str
    expires_in: int


_EVENT_STATUS = {
    QRCodeLoginEvents.SCAN: "waiting",
    QRCodeLoginEvents.CONF: "scanned",
    QRCodeLoginEvents.TIMEOUT: "timeout",
    QRCodeLoginEvents.REFUSE: "refused",
}

_EVENT_MESSAGE = {
    QRCodeLoginEvents.SCAN: "Waiting for scan",
    QRCodeLoginEvents.CONF: "Scanned — confirm on your phone",
    QRCodeLoginEvents.TIMEOUT: "QR code expired; start again",
    QRCodeLoginEvents.REFUSE: "Login refused",
}


def configured_quality() -> str:
    """Return the configured quality tier, falling back to ``higher``."""
    quality = os.environ.get("QQ_QUALITY", "higher").strip().lower()
    if quality not in QUALITY_MAP:
        logger.warning("unknown QQ_QUALITY=%r; using higher", quality)
        return "higher"
    return quality


def quality_chain(requested: str) -> list[tuple[str, SongFileType]]:
    """Requested tier and every lower tier, without duplicate file types."""
    if requested not in QUALITY_MAP:
        requested = "higher"
    out: list[tuple[str, SongFileType]] = []
    seen: set[SongFileType] = set()
    for name in QUALITY_ORDER[QUALITY_ORDER.index(requested) :]:
        file_type = QUALITY_MAP[name]
        if file_type in seen:
            continue
        seen.add(file_type)
        out.append((name, file_type))
    return out


def _join_url(base: str, purl: str) -> str:
    return f"{base.rstrip('/')}/{purl.lstrip('/')}"


class QQMusicService:
    """Owns the QQ credential, the QR login flow, and URL resolution."""

    def __init__(self, credential_path: Path | str = CREDENTIAL_PATH) -> None:
        self.credential_path = Path(credential_path)
        self._credential: Credential | None = self._load()
        self._qr: dict[str, tuple[QR, float]] = {}
        self._lock = asyncio.Lock()

    # --- credential file -------------------------------------------------

    def _load(self) -> Credential | None:
        try:
            data = json.loads(self.credential_path.read_text(encoding="utf-8"))
            return Credential.model_validate(data)
        except FileNotFoundError:
            return None
        except Exception as exc:  # noqa: BLE001 - a bad file must not break startup
            logger.warning("could not load QQ credential: %s", exc)
            return None

    def _save(self, credential: Credential) -> None:
        self.credential_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.credential_path.with_suffix(self.credential_path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(credential.model_dump(by_alias=True), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.credential_path)

    def state(self) -> dict[str, Any]:
        """Login state safe to expose to the page (never includes tokens)."""
        credential = self._credential
        if credential is None:
            return {"logged_in": False, "expired": False, "login_type": None}
        expired = credential.is_expired() if credential.key_expires_in else False
        return {
            "logged_in": True,
            "expired": expired,
            "login_type": credential.login_type,
        }

    async def logout(self) -> None:
        async with self._lock:
            self._credential = None
        try:
            self.credential_path.unlink()
        except FileNotFoundError:
            pass

    # --- QR login --------------------------------------------------------

    def _prune_qr(self) -> None:
        now = time.monotonic()
        for session_id in [sid for sid, (_, deadline) in self._qr.items() if deadline <= now]:
            self._qr.pop(session_id, None)

    async def login_qrcode(self) -> dict[str, Any]:
        """Create a QQ login QR and remember its qrsig server-side."""
        self._prune_qr()
        async with Client() as client:
            qr = await client.login.get_qrcode(QRLoginType.QQ)
        session_id = uuid.uuid4().hex
        self._qr[session_id] = (qr, time.monotonic() + QR_TTL_SECONDS)
        encoded = base64.b64encode(qr.data).decode("ascii")
        return {
            "session_id": session_id,
            "qr_data_url": f"data:{qr.mimetype};base64,{encoded}",
            "expires_in": QR_TTL_SECONDS,
        }

    async def login_status(self, session_id: str) -> dict[str, Any]:
        """Poll one QR session; persist the credential when the phone confirms."""
        entry = self._qr.get(session_id)
        if entry is None:
            return {"status": "timeout", "message": "QR session expired; start again", "logged_in": False}

        qr, deadline = entry
        if time.monotonic() > deadline:
            self._qr.pop(session_id, None)
            return {"status": "timeout", "message": "QR code expired; start again", "logged_in": False}

        async with Client() as client:
            result = await client.login.check_qrcode(qr)

        if result.done:
            credential = result.credential
            if credential is None:
                self._qr.pop(session_id, None)
                raise QQAuthError("QQ login finished without a credential")
            async with self._lock:
                self._credential = credential
                self._save(credential)
            self._qr.pop(session_id, None)
            return {"status": "done", "message": "Logged in", "logged_in": True}

        if result.event in (QRCodeLoginEvents.TIMEOUT, QRCodeLoginEvents.REFUSE):
            self._qr.pop(session_id, None)
        return {
            "status": _EVENT_STATUS.get(result.event, "waiting"),
            "message": _EVENT_MESSAGE.get(result.event, "Waiting for scan"),
            "logged_in": False,
        }

    # --- search ----------------------------------------------------------

    async def search(self, keyword: str, limit: int = 8) -> list[dict[str, Any]]:
        try:
            response = await self._search_with(self._credential, keyword, limit)
        except AUTH_ERRORS:
            # Search works without a credential; a stale stored login must not
            # make the search box unusable.
            response = await self._search_with(None, keyword, limit)
        return [self._serialize(song) for song in response.song]

    @staticmethod
    async def _search_with(credential: Credential | None, keyword: str, limit: int) -> Any:
        async with Client(credential=credential) as client:
            return await client.search.search_by_type(
                keyword,
                SearchType.SONG,
                num=limit,
                page=1,
            )

    @staticmethod
    def _serialize(song: Any) -> dict[str, Any]:
        singers = [singer.name for singer in song.singer if singer.name]
        return {
            "mid": song.mid,
            "media_mid": song.file.media_mid,
            "song_type": song.type,
            "title": song.name,
            "singers": singers,
            "album": song.album.name,
            "duration_sec": song.interval,
            "cover_url": song.cover_url(150),
            "pay_play": song.pay.pay_play,
        }

    # --- playback URL ----------------------------------------------------

    async def _current_credential(self) -> Credential:
        credential = self._credential
        if credential is None:
            raise QQNotLoggedIn("QQ Music login required")
        if credential.key_expires_in and credential.is_expired():
            credential = await self._refresh(credential)
        return credential

    async def _refresh(self, stale: Credential) -> Credential:
        async with self._lock:
            # Another request may have refreshed while this one waited.
            current = self._credential
            if current is not None and current is not stale:
                return current
            if current is None:
                raise QQNotLoggedIn("QQ Music login required")
            try:
                async with Client(credential=current) as client:
                    fresh = await client.login.refresh_credential(current)
            except (
                CredentialRefreshError,
                CredentialExpiredError,
                CredentialInvalidError,
                LoginAuthExpiredError,
                LoginError,
                NetworkError,
                HTTPError,
                ApiDataError,
            ) as exc:
                raise QQAuthError("QQ Music login expired; log in again") from exc
            self._credential = fresh
            self._save(fresh)
            return fresh

    async def resolve(
        self,
        mid: str,
        media_mid: str | None = None,
        song_type: int = 0,
    ) -> ResolvedTrack:
        """Resolve a track to a short-lived URL, refreshing the credential once."""
        credential = await self._current_credential()
        try:
            return await self._resolve_with(credential, mid, media_mid, song_type)
        except AUTH_ERRORS:
            credential = await self._refresh(credential)
            return await self._resolve_with(credential, mid, media_mid, song_type)
        except (NetworkError, HTTPError, ApiDataError) as exc:
            raise QQUnavailable(f"QQ Music request failed: {exc}") from exc

    async def _resolve_with(
        self,
        credential: Credential,
        mid: str,
        media_mid: str | None,
        song_type: int,
    ) -> ResolvedTrack:
        async with Client(credential=credential) as client:
            dispatch = await client.song.get_cdn_dispatch()
            base = random.choice(dispatch.sip) if dispatch.sip else FALLBACK_CDN

            for name, file_type in quality_chain(configured_quality()):
                urls = await client.song.get_song_urls(
                    [
                        SongFileInfo(
                            mid=mid,
                            song_type=song_type or None,
                            media_mid=media_mid or None,
                        )
                    ],
                    file_type=file_type,
                    credential=credential,
                )
                if urls.data and urls.data[0].purl:
                    return ResolvedTrack(
                        url=_join_url(base, urls.data[0].purl),
                        quality=name,
                        expires_in=urls.expiration,
                    )

        raise QQSongUnavailable("QQ Music has no playable file for this track")
