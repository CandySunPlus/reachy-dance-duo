# Beat Bandit uses QQ Music as its only source

The page previously picked a track from YouTube search or a local upload. We removed both and made QQ Music the only source for Beat Bandit. The user has a QQ Music membership, and the QQ path is the one that is legally and operationally acceptable for their use. The upload path and the YouTube/yt-dlp path are deleted, not hidden.

## Consequences

- The analyzer no longer ships yt-dlp, deno, or ytmusicapi.
- A failed QQ lookup or login fails the whole Beat Bandit flow; there is no fallback source.
- The original Python app in `reachy_dance_duo/` is out of scope and still uses its own source.
