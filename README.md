# Reachy Dance Duo

Turn a song into a Reachy dance party: a browser page that drives one or two
Reachy Minis over WebRTC, plus an analyzer that searches QQ Music and works out
the track's beat grid.

Two modes:

| mode | side | what it does |
|---|---|---|
| **Live Groove** | Lite | listens to the room through the *robot's* microphone, tracks BPM in real time and improvises moves; breathes when there is no music |
| **Beat Bandit** | Wireless | search QQ Music, analyse the whole track (tempo, beat times, per-beat energy → 8-beat low/medium/high sequence) and dance to it in sync, playing the audio into the robot's speaker |

## Layout

| path | what |
|---|---|
| `site/` | the page (`index.html`), ES modules, Three.js/MJCF assets — served at `/` |
| `analyzer/` | FastAPI service: `/health`, `/qq/state`, `/qq/login/*`, `/qq/search`, `/qq/analyze`, `/audio/<file>` |
| `deploy/` | deployment notes and the self-signed cert script |
| `reachy_dance_duo/` | the original Python app (same behaviours, runs on the robot itself) |
| `compose.yaml` | single-container deployment: one uvicorn process serves the page and the API |

The page and the analyzer share an origin, so there is no CORS and no reverse
proxy. `analyzer/app` and `site` are bind-mounted in `compose.yaml`, so edits
only need `docker compose restart app`.

## Quick start

Deployment (Spark or any Docker host) — see `deploy/README.md`:

```bash
mkdir -p data/audio data/qq
chmod 700 data/qq              # QQ Music credential lives here
./deploy/gen-cert.sh           # TLS is required for Live Groove (AudioWorklet)
docker compose up -d --build
# https://<host>:18888/
```

Local development of the page only:

```bash
python3 start_server.py       # serves site/ on http://localhost:8000
```

Sign-in:

- **Robot control**: on an HF Space the page uses HF OAuth; anywhere else paste
  an HF token. The token authorises *your* account against the central relay,
  so only your robots appear.
- **QQ Music**: open Beat Bandit, click **Log in**, and scan the QR with QQ or
  WeChat. The credential is stored server-side at
  `/data/qq/credential.json` (0600) and is never sent to the browser. Playback
  URLs are resolved and downloaded by the analyzer; the page only plays a
  same-origin `/audio/...` file.

## Runtime dependencies on Hugging Face

- **Central signalling** — `pollen-robotics-reachy-mini-central.hf.space`.
  Required: the browser and the robot daemon both connect out to it and it
  relays SDP/ICE. Media itself is peer-to-peer (host/srflx/TURN candidates).
- **QQ Music** — a QQ Music account with a membership for paid tracks and
  higher quality tiers. The analyzer embeds `qqmusic-api-python`.
- **Dance library** — moves are fetched from the
  `pollen-robotics/reachy-mini-dances-library` dataset at runtime.

## Analyzer API

| endpoint | purpose |
|---|---|
| `GET /health` | liveness, cache stats, QQ login state |
| `GET /qq/state` | QQ login state (no tokens) |
| `POST /qq/login/qrcode` | create a QQ login QR; returns a session id, image URL, and data URL |
| `GET /qq/login/qr?session=` | QR image bytes for the login panel |
| `GET /qq/login/status?session=` | poll the QR session; persists the credential on success |
| `POST /qq/logout` | delete the stored credential |
| `GET /qq/search?q=&limit=` | QQ Music song search |
| `POST /qq/analyze` `{mid, media_mid, song_type}` | resolve + download + librosa analysis |
| `GET /audio/<file>` | cached audio for `<audio>` playback |

`QQ_QUALITY` selects the requested tier (`standard`, `higher`, `exhigh`,
`lossless`, `hires`; default `higher`). Resolution falls back to lower tiers
when the account or the track does not offer the requested one.
