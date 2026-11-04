# Reachy Dance Duo

A browser page that drives one or two Reachy Minis over WebRTC, plus an
analyzer that resolves, caches, and analyzes a track's beat grid.

## Language

**Analyzer**:
The FastAPI service that resolves a track, caches its audio, and computes its beat grid.
_Avoid_: backend, helper, server

**Beat Bandit**:
The dance mode that plays a pre-analyzed track through a Reachy Mini's speaker and follows that track's beat grid.
_Avoid_: Connected Choreographer, pre-analyzed mode

**Live Groove**:
The dance mode that listens to the room through a Reachy Mini's microphone and improvises from the live tempo.
_Avoid_: realtime mode, mic mode

**Track**:
A selectable piece of music, identified in this context by its QQ Music song mid.
_Avoid_: song, music item, video

**Song mid**:
QQ Music's stable string identifier for a track.
_Avoid_: song id, track id

**Media mid**:
The identifier of the specific audio file variant behind a track; it can differ from the song mid.
_Avoid_: file mid, audio id

**Song type**:
QQ Music's variant discriminator for a track; it is passed through unchanged when resolving audio.
_Avoid_: version, variant id

**Quality tier**:
The requested audio quality (`standard`, `higher`, `exhigh`, `lossless`, `hires`); resolution may return a lower tier than requested.
_Avoid_: bitrate, format

**Playback URL**:
A short-lived QQ Music CDN URL for one audio file. It is resolved and consumed server-side and is never sent to the browser.
_Avoid_: song URL, stream URL, CDN link

**Credential**:
The persisted QQ Music login state. It stays on the analyzer and is never sent to the browser.
_Avoid_: token, cookie, session

**QQ login session**:
The short-lived in-page QR flow that produces a credential.
_Avoid_: login, auth session

**Robot session**:
A live WebRTC connection between the page and one Reachy Mini.
_Avoid_: connection, peer, pairing

**Analysis**:
The beat grid, per-beat energy, and 8-beat low/medium/high sequence computed from a track and consumed by Beat Bandit.
_Avoid_: beatmap, features
