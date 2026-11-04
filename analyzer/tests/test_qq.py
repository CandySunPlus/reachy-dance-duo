from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from qqmusic_api import Credential, CredentialExpiredError
from qqmusic_api.modules.song import SongFileType

from app import qq


def _credential(musickey: str = "key") -> Credential:
    return Credential(
        musicid=1,
        musickey=musickey,
        login_type=1,
        musickey_create_time=int(time.time()),
        key_expires_in=3600,
    )


def test_quality_chain_starts_at_requested_and_dedupes() -> None:
    assert [name for name, _ in qq.quality_chain("higher")] == ["higher", "standard"]
    assert [name for name, _ in qq.quality_chain("lossless")] == [
        "lossless",
        "exhigh",
        "higher",
        "standard",
    ]
    assert [name for name, _ in qq.quality_chain("hires")] == [
        "hires",
        "exhigh",
        "higher",
        "standard",
    ]


def test_quality_chain_unknown_falls_back_to_higher() -> None:
    assert [name for name, _ in qq.quality_chain("bogus")] == ["higher", "standard"]


def test_state_without_credential(tmp_path) -> None:
    service = qq.QQMusicService(tmp_path / "credential.json")
    assert service.state() == {"logged_in": False, "expired": False, "login_type": None}


def test_state_reads_saved_credential(tmp_path) -> None:
    path = tmp_path / "credential.json"
    path.write_text(json.dumps(_credential().model_dump(by_alias=True)), encoding="utf-8")
    service = qq.QQMusicService(path)
    state = service.state()
    assert state["logged_in"] is True
    assert state["expired"] is False
    assert state["login_type"] == 1


def test_state_reports_expired_without_leaking_tokens(tmp_path) -> None:
    expired = Credential(
        musicid=1,
        musickey="secret",
        login_type=1,
        musickey_create_time=0,
        key_expires_in=10,
    )
    path = tmp_path / "credential.json"
    path.write_text(json.dumps(expired.model_dump(by_alias=True)), encoding="utf-8")
    state = qq.QQMusicService(path).state()
    assert state["expired"] is True
    assert "secret" not in json.dumps(state)


def test_resolve_requires_login(tmp_path) -> None:
    service = qq.QQMusicService(tmp_path / "credential.json")
    with pytest.raises(qq.QQNotLoggedIn):
        asyncio.run(service.resolve("0039MnYb0qxYhV"))


def test_unknown_login_session_times_out(tmp_path) -> None:
    service = qq.QQMusicService(tmp_path / "credential.json")
    result = asyncio.run(service.login_status("does-not-exist"))
    assert result["status"] == "timeout"
    assert result["logged_in"] is False


def _fake_client(monkeypatch, state: dict) -> None:
    class FakeSong:
        async def get_cdn_dispatch(self):
            return SimpleNamespace(sip=["https://cdn.example"])

        async def get_song_urls(self, info, file_type=None, credential=None):
            state["calls"].append(file_type)
            if state["fail_auth"]:
                state["fail_auth"] = False
                raise CredentialExpiredError(code=1000)
            if file_type is SongFileType.MP3_320:
                return SimpleNamespace(data=[], expiration=0)
            return SimpleNamespace(
                data=[SimpleNamespace(purl="/path/a.mp3", mid="mid", result=0)],
                expiration=60,
            )

    class FakeLogin:
        async def refresh_credential(self, credential):
            state["refreshed"] += 1
            return _credential("refreshed")

    class FakeClient:
        def __init__(self, credential=None, **kwargs):
            self.song = FakeSong()
            self.login = FakeLogin()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            return False

    monkeypatch.setattr(qq, "Client", FakeClient)


def test_resolve_falls_back_to_the_next_quality(tmp_path, monkeypatch) -> None:
    state = {"calls": [], "fail_auth": False, "refreshed": 0}
    _fake_client(monkeypatch, state)

    service = qq.QQMusicService(tmp_path / "credential.json")
    service._credential = _credential()

    resolved = asyncio.run(service.resolve("mid", "media", 0))
    assert resolved.url == "https://cdn.example/path/a.mp3"
    assert resolved.quality == "standard"
    assert state["calls"] == [SongFileType.MP3_320, SongFileType.MP3_128]


def test_resolve_refreshes_on_auth_error_and_persists(tmp_path, monkeypatch) -> None:
    state = {"calls": [], "fail_auth": True, "refreshed": 0}
    _fake_client(monkeypatch, state)

    path = tmp_path / "credential.json"
    service = qq.QQMusicService(path)
    service._credential = _credential("old")
    service._save(service._credential)

    resolved = asyncio.run(service.resolve("mid"))
    assert resolved.quality == "standard"
    assert state["refreshed"] == 1

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["musickey"] == "refreshed"
    assert path.stat().st_mode & 0o777 == 0o600


def test_search_falls_back_to_anonymous_on_auth_error(tmp_path, monkeypatch) -> None:
    service = qq.QQMusicService(tmp_path / "credential.json")
    service._credential = _credential("stale")
    calls = []

    song = SimpleNamespace(
        mid="mid",
        file=SimpleNamespace(media_mid="media"),
        type=1,
        name="title",
        singer=[SimpleNamespace(name="singer")],
        album=SimpleNamespace(name="album"),
        interval=120,
        cover_url=lambda size=150: "https://cover",
        pay=SimpleNamespace(pay_play=1),
    )

    async def fake_search(credential, keyword, limit):
        calls.append(credential)
        if len(calls) == 1:
            raise CredentialExpiredError(code=1000)
        return SimpleNamespace(song=[song])

    monkeypatch.setattr(service, "_search_with", fake_search)
    tracks = asyncio.run(service.search("keyword", limit=1))

    assert calls[0] is service._credential
    assert calls[1] is None
    assert tracks == [
        {
            "mid": "mid",
            "media_mid": "media",
            "song_type": 1,
            "title": "title",
            "singers": ["singer"],
            "album": "album",
            "duration_sec": 120,
            "cover_url": "https://cover",
            "pay_play": 1,
        }
    ]


def test_login_qr_image_returns_bytes_and_rejects_unknown(tmp_path) -> None:
    from qqmusic_api.models.login import QR, QRLoginType

    service = qq.QQMusicService(tmp_path / "credential.json")
    service._qr["session-1234"] = (
        QR(b"\x89PNG\r\n\x1a\n", QRLoginType.QQ, "image/png", "qrsig"),
        time.monotonic() + 60,
    )
    assert service.login_qr_image("session-1234") == (b"\x89PNG\r\n\x1a\n", "image/png")
    assert service.login_qr_image("missing") is None


def test_snapshot_payload_handles_cookie_without_domain() -> None:
    from types import SimpleNamespace

    from qqmusic_api.core import executor

    class Cookie:
        def __init__(self, name: str, value: str) -> None:
            self.name = name
            self.value = value

    class Jar:
        def __iter__(self):
            return iter([Cookie("ETK", "etk-value"), Cookie("p_skey", "skey-value")])

    response = SimpleNamespace(
        status_code=200,
        url="https://ssl.ptlogin2.qq.com/ptqrlogin",
        headers={"content-type": "text/plain"},
        cookies=Jar(),
        content=b"ptuiCB()",
        text="ptuiCB()",
    )

    payload = qq._snapshot_payload_safe(response)
    assert payload.cookies == {"ETK": "etk-value", "p_skey": "skey-value"}
    assert payload.text == "ptuiCB()"
    assert executor.snapshot_payload is qq._snapshot_payload_safe
