// HTTP client for the reachy-dance-duo-analyzer Space.
//
// Public API surface:
//   const c = new AnalyzerClient();
//   await c.search("daft punk one more time");
//   const analysis = await c.analyze("wU26xVT_vBU");
//   <audio src={c.audioUrl(analysis.audio_url)} />
//
// The analyzer Space lives at TwinPeaksTownie/reachy-dance-duo-analyzer and
// proxies yt-dlp through Carson's local helper via ngrok. Search uses
// ytmusicapi (anonymous). Analyze runs librosa.
//
// analyzeUpload() is the self-contained path: it skips YouTube entirely, so
// it works when the helper is down, when a track is region-restricted, or
// when yt-dlp cannot find an audio format for it.

const DEFAULT_BASE = (() => {
    // `?analyzer=https://<your>-reachy-dance-duo-analyzer.hf.space` points a
    // copy of this page at a different analyzer deployment (e.g. your own
    // duplicate, once the shared one's yt-dlp helper stops resolving).
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

    async health() {
        const r = await fetch(`${this.baseUrl}/health`);
        if (!r.ok) throw new Error(`health ${r.status}`);
        return r.json();
    }

    // Returns array of { videoId, title, artists: [{name}], album, duration,
    // duration_seconds, thumbnails: [{url, width, height}] }.
    async search(query, limit = 10) {
        const url = `${this.baseUrl}/search?q=${encodeURIComponent(query)}&limit=${limit}`;
        const r = await fetch(url);
        if (!r.ok) throw new Error(`search ${r.status}: ${await r.text()}`);
        return r.json();
    }

    // Triggers download (via ngrok helper) + librosa analysis. Slow (20-60s
    // depending on song length and helper warm/cold cache). Returns:
    //   { video_id, audio_url, duration_sec, tempo, beats: number[],
    //     energy_per_beat: number[], sequence_assignments: ("low"|"medium"|"high")[] }
    async analyze(videoId, { signal } = {}) {
        const r = await fetch(`${this.baseUrl}/analyze`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ video_id: videoId }),
            signal,
        });
        if (!r.ok) throw new Error(`analyze ${r.status}: ${await r.text()}`);
        return r.json();
    }

    // Analyze a local audio file instead of a YouTube id: no helper, no
    // yt-dlp, no region/format lottery. The raw bytes go straight to the
    // analyzer, which decodes them with ffmpeg and runs the same librosa
    // pass. Returns the same shape as analyze(), with video_id null.
    async analyzeUpload(file, { signal } = {}) {
        const r = await fetch(
            `${this.baseUrl}/analyze_upload?name=${encodeURIComponent(file.name || "upload")}`,
            {
                method: "POST",
                headers: { "Content-Type": file.type || "application/octet-stream" },
                body: file,
                signal,
            },
        );
        if (!r.ok) throw new Error(`analyze_upload ${r.status}: ${await r.text()}`);
        return r.json();
    }

    // The analyzer returns audio_url as a relative path. Prepend base.
    audioUrl(audioUrlOrPath) {
        if (audioUrlOrPath.startsWith("http")) return audioUrlOrPath;
        return `${this.baseUrl}${audioUrlOrPath}`;
    }
}
