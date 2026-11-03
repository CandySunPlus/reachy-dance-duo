#!/usr/bin/env bash
# Self-signed TLS pair for the single-container deployment.
#
# Why TLS at all: Live Groove's BPM analyser runs in an AudioWorklet, and
# AudioWorklet / navigator.mediaDevices / crypto.randomUUID only exist in a
# secure context. Plain http silently loses those features.
#
# ACME is not used because this deployment does not occupy 80/443. If you want
# a browser-trusted certificate instead, either run an HTTP-01 challenge with
# those ports open for a minute, or use DNS-01 with your DNS provider.
set -euo pipefail

HOST="${1:-zysq.spark.aizhiyong.com}"
IP="${2:-192.168.3.79}"
OUT="$(cd "$(dirname "$0")/.." && pwd)/certs"

mkdir -p "$OUT"
openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
  -keyout "$OUT/key.pem" -out "$OUT/cert.pem" \
  -subj "/CN=$HOST" \
  -addext "subjectAltName=DNS:$HOST,IP:$IP"

# The container runs as uid 1000, so the key must be readable by it.
chmod 644 "$OUT/key.pem" "$OUT/cert.pem"
echo "wrote $OUT/cert.pem and $OUT/key.pem"
openssl x509 -in "$OUT/cert.pem" -noout -subject -ext subjectAltName
