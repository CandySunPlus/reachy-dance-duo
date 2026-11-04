# The analyzer embeds QQMusicApi and proxies playback URLs

We embed the `qqmusic-api-python` client in the analyzer instead of running qq-musicfox's `hack/qqapi/app.py` as a sidecar. The analyzer owns the QQ credential, refreshes it proactively, resolves a short-lived playback URL, downloads the audio into its cache, and serves only the cached file to the browser. The browser never receives a QQ credential or CDN URL.

## Considered Options

- **Sidecar qq-musicfox backend**: less code, but its backend has no `refresh_credential()` and clears the credential file on a 401; a second process and credential store would also have to be deployed.
- **Browser loads the QQ CDN URL directly**: rejected because playback URLs expire, may need QQ referer/TLS behavior, and would expose the CDN URL to every viewer.

## Consequences

- The analyzer carries the QQ login UI and credential lifecycle.
- Changing the QQ client library means touching `analyzer/app/qq.py`; the dependency is pinned to 0.8.x.
