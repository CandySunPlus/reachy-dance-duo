---
title: Reachy Dance Duo Analyzer
emoji: 🎧
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
short_description: yt-dlp + librosa backend for Reachy Dance Duo JS
---

Backend service for the Reachy Dance Duo JS app at
`TwinPeaksTownie/reachy-dance-duo-js`. Provides three things the static JS Space
cannot do on its own:

1. YouTube Music search via `ytmusicapi` (unauthenticated).
2. Audio download via `yt-dlp`, served back as `audio/mp4`.
3. Offline song analysis via `librosa` (tempo, beat times, per-beat energy,
   8-beat sequence assignments) used by the Beat Bandit dance mode.

CORS is open to the JS Space and to localhost for development.

## Endpoints

- `GET /health`
- `GET /search?q=<query>&limit=10`
- `POST /analyze` body `{ "video_id": "<yt-id>" }`
- `GET /audio/<video_id>.m4a`
