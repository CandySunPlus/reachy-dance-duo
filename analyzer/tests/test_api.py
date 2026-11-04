from __future__ import annotations

from fastapi.testclient import TestClient

from app import main, qq


def _client(monkeypatch, tmp_path) -> TestClient:
    service = qq.QQMusicService(tmp_path / "credential.json")
    monkeypatch.setattr(main, "qq_service", service)
    return TestClient(main.app)


def test_health_reports_qq_state_without_tokens(monkeypatch, tmp_path) -> None:
    response = _client(monkeypatch, tmp_path).get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["qq"]["logged_in"] is False


def test_qq_state_endpoint(monkeypatch, tmp_path) -> None:
    response = _client(monkeypatch, tmp_path).get("/qq/state")
    assert response.status_code == 200
    assert response.json() == {"logged_in": False, "expired": False, "login_type": None}


def test_qq_analyze_requires_login(monkeypatch, tmp_path) -> None:
    response = _client(monkeypatch, tmp_path).post(
        "/qq/analyze",
        json={"mid": "0039MnYb0qxYhV", "media_mid": "0039MnYb0qxYhV", "song_type": 0},
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "qq_not_logged_in"


def test_qq_analyze_rejects_invalid_mid(monkeypatch, tmp_path) -> None:
    response = _client(monkeypatch, tmp_path).post("/qq/analyze", json={"mid": "bad mid!"})
    assert response.status_code == 422


def test_qq_search_maps_upstream_failure(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)

    async def fail(keyword: str, limit: int = 8):
        raise qq.QQUnavailable("upstream down")

    monkeypatch.setattr(main.qq_service, "search", fail)
    response = client.get("/qq/search", params={"q": "test"})
    assert response.status_code == 502
    assert response.json()["detail"] == "upstream down"


def test_qq_search_maps_auth_failure(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)

    async def fail(keyword: str, limit: int = 8):
        raise qq.QQAuthError("expired")

    monkeypatch.setattr(main.qq_service, "search", fail)
    response = client.get("/qq/search", params={"q": "test"})
    assert response.status_code == 401
    assert response.json()["detail"] == "qq_login_expired"


def test_qq_analyze_retries_download_once(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)
    calls = []

    async def resolve(mid, media_mid=None, song_type=0):
        return qq.ResolvedTrack("https://cdn.example/a.mp3", "higher", 60)

    async def fake_download(url, key, **kwargs):
        calls.append(key)
        if len(calls) == 1:
            raise RuntimeError("dead CDN node")
        path = main.download.CACHE_DIR / f"{key}.mp3"
        path.write_bytes(b"audio")
        return path

    monkeypatch.setattr(main.qq_service, "resolve", resolve)
    monkeypatch.setattr(main.download, "download", fake_download)
    monkeypatch.setattr(
        main.analyze_mod,
        "analyze",
        lambda path: {
            "duration_sec": 1.0,
            "tempo": 120.0,
            "beats": [0.0],
            "energy_per_beat": [0.5],
            "sequence_assignments": ["medium"],
            "energy_envelope": [0.5],
            "envelope_sr": 11025,
            "hop_length": 512,
        },
    )

    response = client.post(
        "/qq/analyze",
        json={"mid": "0039MnYb0qxYhW", "media_mid": "0039MnYb0qxYhW", "song_type": 1},
    )
    assert response.status_code == 200
    assert response.json()["quality"] == "higher"
    assert len(calls) == 2


def test_qq_analyze_maps_unavailable_song(monkeypatch, tmp_path) -> None:
    client = _client(monkeypatch, tmp_path)

    async def resolve(mid, media_mid=None, song_type=0):
        raise qq.QQSongUnavailable("no file")

    monkeypatch.setattr(main.qq_service, "resolve", resolve)
    response = client.post("/qq/analyze", json={"mid": "0039MnYb0qxYhX"})
    assert response.status_code == 404
    assert response.json()["detail"] == "qq_song_unavailable"


def test_qq_analyze_runs_librosa_on_a_cached_track(monkeypatch, tmp_path) -> None:
    import wave

    import numpy as np

    client = _client(monkeypatch, tmp_path)
    sr, duration = 16000, 6.0
    rng = np.random.default_rng(0)
    samples = np.zeros(int(sr * duration), dtype=np.float32)
    for beat in np.arange(0, duration, 0.5):
        start = int(beat * sr)
        samples[start : start + 300] += rng.normal(0, 0.6, 300).astype(np.float32)
    pcm = (np.clip(samples, -1, 1) * 32767).astype("<i2")

    path = main.download.CACHE_DIR / "qq-synthetic01-higher.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sr)
        handle.writeframes(pcm.tobytes())

    response = client.post("/qq/analyze", json={"mid": "synthetic01"})
    assert response.status_code == 200
    body = response.json()
    assert body["quality"] == "higher"
    assert body["audio_url"] == "/audio/qq-synthetic01-higher.wav"
    assert body["beats"]
    assert 100 < body["tempo"] < 140
    assert len(body["sequence_assignments"]) == (len(body["energy_per_beat"]) + 7) // 8
