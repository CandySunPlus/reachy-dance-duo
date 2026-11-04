---
title: Reachy Dance Duo Analyzer
emoji: 🎧
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: QQ Music + librosa backend for Reachy Dance Duo
---

Backend service for the Reachy Dance Duo JS app. It provides three things the
static page cannot do on its own:

1. QQ Music song search via `qqmusic-api-python`.
2. Login and playback-URL resolution: the analyzer stores the QQ credential,
   refreshes it, resolves a short-lived CDN URL, and caches the audio locally.
3. Offline track analysis via `librosa` (tempo, beat times, per-beat energy,
   8-beat sequence assignments) used by the Beat Bandit dance mode.

The browser never receives the QQ credential or a playback URL; it only plays
same-origin `/audio/...` files.

## Endpoints

- `GET /health` — liveness, cache stats, QQ login state
- `GET /qq/state` — `{ logged_in, expired, login_type }`
- `POST /qq/login/qrcode` — `{ session_id, qr_data_url, expires_in }`
- `GET /qq/login/status?session=<id>` — `{ status, message, logged_in }`
- `POST /qq/logout`
- `GET /qq/search?q=<query>&limit=8`
- `POST /qq/analyze` body `{ "mid": "...", "media_mid": "...", "song_type": 0 }`
- `GET /audio/<file>`

## Configuration

| variable | default | meaning |
|---|---|---|
| `AUDIO_CACHE_DIR` | `/data/audio` | LRU audio cache |
| `MAX_CACHE_BYTES` | `2 GiB` | cache eviction threshold |
| `QQ_CREDENTIAL_PATH` | `/data/qq/credential.json` | persisted QQ credential (0600) |
| `QQ_QUALITY` | `higher` | requested tier: `standard`, `higher`, `exhigh`, `lossless`, `hires` |
| `SITE_DIR` | `/srv` | optional static page served from the same origin |

TLS is required in production for the page (Live Groove's `AudioWorklet` and
`navigator.mediaDevices`); see `deploy/README.md`.

## Tests

```bash
python -m pip install fastapi "uvicorn[standard]" librosa av "numpy<2" \
    pydantic httpx "qqmusic-api-python>=0.8.2,<0.9" pytest
cd analyzer && python -m pytest
```

`tests/conftest.py` redirects the audio cache and credential path to a temp
directory, so tests never touch `/data`. The QQ tests mock the upstream client;
the analysis test writes a synthetic click track through the real librosa path.
