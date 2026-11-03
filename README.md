# Reachy Dance Duo

Turn any song into a Reachy dance party: a browser page that drives one or two
Reachy Minis over WebRTC, plus a small analyzer that finds the music and works
out its beat grid.

Two modes:

| mode | side | what it does |
|---|---|---|
| **Live Groove** | Lite | listens to the room through the *robot's* microphone, tracks BPM in real time and improvises moves; breathes when there is no music |
| **Beat Bandit** | Wireless | pick a song (YouTube search or a local upload), analyse the whole track (tempo, beat times, per-beat energy → 8-beat low/medium/high sequence) and dance to it in sync, playing the audio into the robot's speaker |

## Layout

| path | what |
|---|---|
| `site/` | the page (`index.html`), ES modules, Three.js/MJCF assets — served at `/` |
| `analyzer/` | FastAPI service: `/health`, `/search`, `/analyze`, `/analyze_upload`, `/audio/<file>`, `/yt_debug` |
| `deploy/` | deployment notes and the self-signed cert script |
| `reachy_dance_duo/` | the original Python app (same behaviours, runs on the robot itself) |
| `compose.yaml` | single-container deployment: one uvicorn process serves the page and the API |

The page and the analyzer share an origin, so there is no CORS and no reverse
proxy. `analyzer/app` and `site` are bind-mounted in `compose.yaml`, so edits
only need `docker compose restart app`.

## Quick start

Deployment (Spark or any Docker host) — see `deploy/README.md`:

```bash
mkdir -p data/audio
./deploy/gen-cert.sh          # TLS is required for Live Groove (AudioWorklet)
docker compose up -d --build
# https://<host>:18888/
```

Local development of the page only:

```bash
python3 start_server.py       # serves site/ on http://localhost:8000
```

Sign-in: on an HF Space the page uses HF OAuth; anywhere else paste an HF token
(OAuth needs the Space-provided client id). The token authorises *your* account
against the central relay, so only your robots appear.

## Runtime dependencies on Hugging Face

- **Central signalling** — `pollen-robotics-reachy-mini-central.hf.space`.
  Required: the browser and the robot daemon both connect out to it and it
  relays SDP/ICE. Media itself is peer-to-peer (host/srflx/TURN candidates).
- **Dance library** — moves are fetched from the
  `pollen-robotics/reachy-mini-dances-library` dataset at runtime.

## Analyzer API

| endpoint | purpose |
|---|---|
| `GET /search?q=&limit=` | YouTube Music search (ytmusicapi) |
| `POST /analyze` `{video_id}` | yt-dlp download + librosa analysis |
| `POST /analyze_upload?name=` | raw audio body → same analysis (no YouTube; 422 on silent/short audio) |
| `GET /audio/<file>` | cached audio for `<audio>` playback |
| `GET /yt_debug?video_id=` | yt-dlp extractor diagnostics |
| `GET /health` | liveness + cache stats |
