from __future__ import annotations

import asyncio

import httpx
import pytest

from app import download


def test_extension_from_url_only_accepts_audio_suffixes() -> None:
    assert download.extension_from_url("https://cdn.example/a.MP3?vkey=1") == ".mp3"
    assert download.extension_from_url("https://cdn.example/stream") is None
    assert download.extension_from_url("https://cdn.example/a.txt") is None


def test_download_writes_file_atomically(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(download, "CACHE_DIR", tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"audio")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await download.download("https://cdn.example/a.mp3", "qq-mid-higher", client=client)

    path = asyncio.run(run())
    assert path == tmp_path / "qq-mid-higher.mp3"
    assert path.read_bytes() == b"audio"
    assert not list(tmp_path.glob("*.part"))


def test_download_uses_content_type_when_url_has_no_extension(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(download, "CACHE_DIR", tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "audio/mp4"}, content=b"audio")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await download.download("https://cdn.example/stream", "qq-mid-higher", client=client)

    assert asyncio.run(run()) == tmp_path / "qq-mid-higher.m4a"


def test_download_rejects_empty_body_and_leaves_no_files(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(download, "CACHE_DIR", tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "audio/mpeg"}, content=b"")

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            await download.download("https://cdn.example/a.mp3", "qq-mid-higher", client=client)

    with pytest.raises(RuntimeError, match="empty download"):
        asyncio.run(run())
    assert not list(tmp_path.iterdir())
