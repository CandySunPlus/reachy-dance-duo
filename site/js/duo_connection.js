import { stateToFrame } from './state_to_pose.js';

// Manages two ReachyMini SDK instances side by side (Lite + Wireless).
// Each side exposes:
//   instance        the ReachyMini SDK object
//   robotId         currently-bound robot id (or null)
//   streaming       true once the `streaming` event fired
//   robotStream     MediaStream from the `videoTrack` event (mic + cam)
//   lastFrame       most recent dance-duo-shape pose frame from `state` events
//
// The page wires lifecycle in this order:
//   1. duo.bootstrap()       checks auth, returns 'authenticated' or 'needs-login'
//   2. duo.login()           HF OAuth redirect (one call covers both robots)
//   3. duo.connect()         opens signaling on BOTH instances
//   4. duo.startSession(side, robotId)   pairs a discovered robot to a side
//
// Multi-robot caveat: HF signaling is a single account. Both instances see the
// same `robots` list via `robotsChanged`. The page picks which robot id maps to
// which side; this module stores the mapping and starts sessions accordingly.

export class DuoConnection extends EventTarget {
    constructor(ReachyMini) {
        super();
        this.ReachyMini = ReachyMini;
        this.lite = this._makeSide('lite');
        this.wireless = this._makeSide('wireless');
        this.username = null;
    }

    _makeSide(side) {
        // Only the wireless side (Beat Bandit) sends audio out. SDK 1.13 no
        // longer uses `enableMicrophone`/getUserMedia: it creates a silent
        // placeholder track and exposes setOutgoingAudioTrack(). The page
        // applies the music track on the `streaming` event. The lite side
        // (Live Groove) only receives the robot's own mic/video.
        const inst = new this.ReachyMini({
            appName: `dance-duo-${side}`,
            signalingUrl: 'https://pollen-robotics-reachy-mini-central.hf.space',
        });
        // Kill the SDK's built-in auto-start — we control session lifecycle
        inst._maybeAutoStart = () => {};
        const s = {
            side,
            instance: inst,
            robotId: null,
            streaming: false,
            connected: false,
            robotStream: null,
            lastFrame: null,
        };
        const tag = `[duo:${side}]`;
        inst.addEventListener('connected', (e) => {
            console.log(`${tag} connected, peerId:`, e.detail?.peerId);
            s.connected = true;
            this._emit('side-connected', { side });
        });
        inst.addEventListener('disconnected', (e) => {
            console.warn(`${tag} disconnected:`, e.detail?.reason);
            s.connected = false;
            s.streaming = false;
            this._emit('side-disconnected', { side, reason: e.detail?.reason });
        });
        inst.addEventListener('streaming', (e) => {
            console.log(`${tag} streaming, session:`, e.detail?.sessionId);
            s.streaming = true;
            this._emit('side-streaming', { side, sessionId: e.detail?.sessionId });
        });
        inst.addEventListener('sessionStopped', (e) => {
            console.log(`${tag} session stopped:`, e.detail?.reason);
            s.streaming = false;
            this._emit('side-session-stopped', { side, reason: e.detail?.reason });
        });
        inst.addEventListener('sessionRejected', (e) => {
            console.warn(`${tag} session rejected, activeApp:`, e.detail?.activeApp);
            this._emit('side-session-rejected', { side, activeApp: e.detail?.activeApp });
        });
        inst.addEventListener('robotsChanged', (e) => {
            const robots = e.detail?.robots || [];
            console.log(`${tag} robotsChanged: ${robots.length} robot(s)`, JSON.stringify(robots));
            this._emit('robots-changed', { side, robots });
        });
        inst.addEventListener('videoTrack', (e) => {
            s.robotStream = e.detail?.stream || null;
            this._emit('side-stream', { side, stream: s.robotStream });
        });
        inst.addEventListener('state', (e) => {
            s.lastFrame = stateToFrame(e.detail);
            this._emit('side-state', { side, frame: s.lastFrame });
        });
        inst.addEventListener('error', (e) => {
            console.error(`${tag} ERROR [${e.detail?.source}]:`, e.detail?.error);
            this._emit('side-error', { side, source: e.detail?.source, error: e.detail?.error });
        });

        // Diagnostic probe: central keys a peer by HF token, so only ONE
        // instance's SSE stream is fed at a time. This shows whether the
        // robot's offer reaches THIS instance, and whether it has a peer
        // connection to answer it with.
        const origPeerMessage = inst._handlePeerMessage?.bind(inst);
        if (origPeerMessage) {
            inst._handlePeerMessage = (msg) => {
                // SDK 1.13 adds its silent placeholder track on every offer.
                // A repeated offer would throw "A sender already exists for
                // the track"; the page supplies audio via
                // setOutgoingAudioTrack, so drop the placeholder first.
                if (msg?.sdp?.type === 'offer' && inst._micStream) {
                    for (const track of inst._micStream.getAudioTracks()) {
                        inst._micStream.removeTrack(track);
                    }
                }
                const kind = msg?.sdp ? `sdp:${msg.sdp.type}` : (msg?.ice ? 'ice' : '?');
                console.log(`${tag} peer<- ${kind} sid=${String(msg?.sessionId).slice(0, 8)}`
                    + ` mySid=${String(inst._sessionId).slice(0, 8)} pc=${!!inst._pc}`);
                return origPeerMessage(msg);
            };
        }
        return s;
    }

    _emit(type, detail) {
        this.dispatchEvent(new CustomEvent(type, { detail }));
    }

    async bootstrap() {
        try {
            const okLite = await this.lite.instance.authenticate();
            const okWireless = await this.wireless.instance.authenticate();
            console.log(`[duo] bootstrap: lite=${okLite}, wireless=${okWireless}`);
            const ok = okLite && okWireless;
            if (ok) {
                this.username = this.lite.instance.username || this.wireless.instance.username || 'you';
            }
            return ok;
        } catch (e) {
            console.error('[duo] bootstrap error:', e);
            return false;
        }
    }

    login() {
        this.lite.instance.login();
    }

    async connect(side = 'wireless') {
        return this._ensureSide(side);
    }

    /**
     * Make `side` the ONE instance holding central's SSE stream.
     *
     * Central keys a peer by HF token: a second SDK instance on the same
     * account supersedes the first, whose stream then stops being fed
     * (`_is_current_connection()` goes false server-side). When that
     * happens the dialing instance never sees its own offer — its twin
     * receives it, has no RTCPeerConnection (`if (!this._pc) return`),
     * and drops it — so no answer is ever posted and the robot dies with
     * `signaling_state: have-local-offer` after its ICE deadline.
     *
     * Dial calls are serialized: the page dials lite and wireless through
     * `Promise.all`, and interleaved connects would otherwise leave both
     * sides half-switched.
     */
    async _ensureSide(side) {
        const target = this[side];
        if (!target) throw new Error(`unknown side ${side}`);
        const run = async () => {
            const other = side === 'lite' ? this.wireless : this.lite;
            if (other.connected) {
                console.log(`[duo] releasing ${other.side} signaling (one stream per HF account)`);
                try { other.instance.disconnect(); } catch (e) {
                    console.warn('[duo] disconnect(other side) failed:', e);
                }
                other.connected = false;
                other.streaming = false;
            }
            if (!target.connected) {
                console.log(`[duo] connect: opening signaling on ${side}`);
                await target.instance.connect();
                console.log(`[duo] connect: ${side} signaling open`);
            }
            this.activeSide = side;
        };
        this._connectChain = (this._connectChain || Promise.resolve()).then(run, run);
        return this._connectChain;
    }

    /** Use a pasted HF token instead of the OAuth redirect.
     *
     * `login()` needs the Space's OAuth client id, so on a self-hosted
     * origin it throws "Missing clientId". Setting the token on both
     * instances skips `authenticate()` entirely: the SDK then uses it for
     * central signaling and /api/robot-status.
     */
    setToken(token) {
        const t = String(token || "").trim();
        if (!t) throw new Error("empty token");
        for (const s of [this.lite, this.wireless]) {
            s.instance._token = t;
            s.instance._tokenExpires = null;
            s.instance._username = null;
        }
        this.username = "token";
        return true;
    }

    async refreshRobots() {
        // One account, one producer list: ask the instance that actually
        // holds the SSE stream (the idle one cannot POST /send anyway).
        if (!this.activeSide) {
            await this.connect('wireless');
        }
        const instance = this[this.activeSide].instance;
        console.log('[duo] refreshRobots: querying signaling server...');

        try {
            const owned = await instance._fetchOwnedRobots({ filterBusy: false });
            console.log(`[duo] /api/robot-status returned ${owned.length} robot(s):`, JSON.stringify(owned));
            if (owned.length > 0) {
                const robots = owned.map(r => ({ id: r.id, meta: { name: r.name, ...r.meta } }));
                this._emit('robots-changed', { side: this.activeSide, robots });
                return;
            }
        } catch (e) {
            console.warn('[duo] _fetchOwnedRobots failed:', e);
        }

        try {
            const res = await instance._sendToServer({ type: 'list' });
            const robots = res?.producers || [];
            console.log(`[duo] refreshRobots ${this.activeSide}: ${robots.length} producer(s)`, JSON.stringify(robots));
            this._emit('robots-changed', { side: this.activeSide, robots });
        } catch (e) {
            console.warn('[duo] refreshRobots failed:', e);
        }
    }

    async startSession(side, robotId) {
        const s = this[side];
        if (!s) throw new Error(`unknown side ${side}`);
        // The side that dials must be the side holding the SSE stream.
        if (!s.connected || this.activeSide !== side) {
            await this._ensureSide(side);
        }
        await s.instance.startSession(robotId);
        s.robotId = robotId;
    }

    async stopSession(side) {
        const s = this[side];
        if (!s) return;
        try { await s.instance.stopSession(); } catch {}
        s.robotId = null;
        s.streaming = false;
    }

    sideOf(side) {
        return this[side];
    }
}