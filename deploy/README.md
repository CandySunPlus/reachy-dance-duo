# Deploying on the Spark box

One container runs `uvicorn app.main:app`: it serves the static page from
`site/` (mounted at `/srv`) and the analyzer API from the same origin, over TLS
on port 18888. No reverse proxy.

```bash
# on Spark, inside a checkout of this repo
cd ~/services/reachy-dance-duo        # or wherever you cloned it
mkdir -p data/audio data/qq           # audio cache + QQ credential
chmod 700 data/qq                     # the credential file is created at 0600
# The container runs as uid 1000; make the bind mounts writable by it.
sudo chown -R 1000:1000 data/audio data/qq

./deploy/gen-cert.sh                  # self-signed cert for the hostname + LAN IP
docker compose up -d --build          # first time builds the analyzer image

docker compose ps                     # expect app ... (healthy)
docker compose logs -f app            # follow
```

Then open `https://zysq.spark.aizhiyong.com:18888/` and accept the certificate
warning once (self-signed), or use `https://192.168.3.79:18888/` on the LAN.

## Update paths

| changed | command |
|---|---|
| `site/` (page, JS, assets) | `docker compose restart app` (bind-mounted, no rebuild) |
| `analyzer/app/*.py` | `docker compose restart app` |
| `analyzer/Dockerfile`, dependencies | `docker compose up -d --build app` |

## Notes

- `data/` (audio cache + QQ credential) and `certs/` are runtime state and
  are git-ignored.
- Open the page, switch to Beat Bandit, and scan the QQ Music QR once; the
  credential is written to `data/qq/credential.json` and refreshed in place.
- The page needs a secure context for Live Groove (`AudioWorklet`); Beat Bandit
  works either way, so never drop the TLS wrapper without accepting that loss.
- Sign-in on a self-hosted origin is a pasted HF token: HF OAuth needs the
  Space-provided client id and is only shown inside `*.hf.space`.
- Controlling the robot still goes through the public Hugging Face central
  relay (`pollen-robotics-reachy-mini-central.hf.space`); that is not something
  this deployment replaces.
