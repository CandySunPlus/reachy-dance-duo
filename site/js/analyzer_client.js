// HTTP client for the Reachy Dance Duo analyzer.
//
// Public API surface:
//   const c = new AnalyzerClient();
//   await c.qqState();
//   const login = await c.qqLoginQrcode();
//   await c.qqLoginStatus(login.session_id);
//   const tracks = await c.qqSearch("song or artist");
//   const analysis = await c.qqAnalyze(tracks[0]);
//   <audio src={c.audioUrl(analysis.audio_url)} />
//
// The analyzer resolves a short-lived QQ Music playback URL, downloads the
// audio into its own cache, and returns a same-origin /audio path. The page
// never sees a QQ credential or CDN URL.

const DEFAULT_BASE = (() => {
    // `?analyzer=https://<your>-reachy-dance-duo-analyzer.hf.space` points a
    // copy of this page at a different analyzer deployment.
    try {
        const override = new URLSearchParams(location.search).get("analyzer");
        if (override) return override.replace(/\/+$/, "");
        // Self-hosted (not on HF): the analyzer process serves this page as
        // well, so the API is just the same origin — no prefix, no CORS.
        if (!location.hostname.endsWith(".hf.space")) return "";
    } catch (_) { /* no location (tests) */ }
    return "https://twinpeakstownie-reachy-dance-duo-analyzer.hf.space";
})();

export class AnalyzerClient {
    constructor({ baseUrl = DEFAULT_BASE } = {}) {
        this.baseUrl = baseUrl.replace(/\/+$/, "");
    }

    async _request(path, { method = "GET", body, signal } = {}) {
        const init = { method, signal };
        if (body !== undefined) {
            init.headers = { "Content-Type": "application/json" };
            init.body = JSON.stringify(body);
        }
        const r = await fetch(`${this.baseUrl}${path}`, init);
        const text = await r.text();
        let payload = text;
        try { payload = text ? JSON.parse(text) : null; } catch (_) { /* text error */ }
        if (!r.ok) {
            const detail = payload && typeof payload === "object"
                ? (payload.detail ?? payload.message)
                : payload;
            const err = new Error(typeof detail === "string" ? detail : `HTTP ${r.status}`);
            err.status = r.status;
            throw err;
        }
        return payload;
    }

    async health() {
        return this._request("/health");
    }

    // Login state: { logged_in, expired, login_type }. Never includes tokens.
    async qqState() {
        return this._request("/qq/state");
    }

    // Returns { session_id, qr_data_url, expires_in }.
    async qqLoginQrcode() {
        return this._request("/qq/login/qrcode", { method: "POST" });
    }

    // Returns { status, message, logged_in }; status is one of
    // waiting | scanned | done | timeout | refused.
    async qqLoginStatus(sessionId) {
        return this._request(`/qq/login/status?session=${encodeURIComponent(sessionId)}`);
    }

    async qqLogout() {
        return this._request("/qq/logout", { method: "POST" });
    }

    // Returns [{ mid, media_mid, song_type, title, singers, album,
    // duration_sec, cover_url, pay_play }].
    async qqSearch(query, limit = 8) {
        return this._request(`/qq/search?q=${encodeURIComponent(query)}&limit=${limit}`);
    }

    // Resolves, downloads, and analyzes one search result. The response is the
    // analyzer analysis plus { audio_url, mid, quality }.
    async qqAnalyze(track, { signal } = {}) {
        return this._request("/qq/analyze", {
            method: "POST",
            signal,
            body: {
                mid: track.mid,
                media_mid: track.media_mid || null,
                song_type: track.song_type || 0,
            },
        });
    }

    // The analyzer returns audio_url as a relative path. Prepend base.
    audioUrl(audioUrlOrPath) {
        if (audioUrlOrPath.startsWith("http")) return audioUrlOrPath;
        return `${this.baseUrl}${audioUrlOrPath}`;
    }
}
