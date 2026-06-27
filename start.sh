#!/usr/bin/env bash
# noodle — one-command launcher for Linux / macOS.
#
# Double-click it (or run ./start.sh). It checks Docker, builds + starts the
# container, waits for the app to be healthy, then opens your browser.
set -euo pipefail
cd "$(dirname "$0")"

say()  { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
err()  { printf '\n\033[1;31m%s\033[0m\n' "$*" >&2; }

# --- 1. Docker present? ---------------------------------------------------
if ! command -v docker >/dev/null 2>&1; then
  err "Docker is not installed."
  echo "Install Docker Desktop (macOS) or Docker Engine (Linux):"
  echo "  https://docs.docker.com/get-docker/"
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  err "Docker is installed but not running. Start Docker Desktop and retry."
  exit 1
fi

# `docker compose` (v2) vs legacy `docker-compose`
if docker compose version >/dev/null 2>&1; then COMPOSE="docker compose"; else COMPOSE="docker-compose"; fi

# --- 2. Build + start -----------------------------------------------------
say "Building and starting noodle (first run downloads ~1 GB, be patient)…"
$COMPOSE up -d --build

# --- 3. Wait for health ---------------------------------------------------
say "Waiting for the app to come up…"
for _ in $(seq 1 60); do
  if curl -fsS http://localhost:8090/health >/dev/null 2>&1; then break; fi
  sleep 2
done

URL="http://localhost:8090/nodes"
say "noodle is running →  $URL"
echo "Stop it later with:  $COMPOSE down"

# --- 4. Open browser (best effort) ---------------------------------------
( command -v xdg-open >/dev/null 2>&1 && xdg-open "$URL" ) >/dev/null 2>&1 || \
( command -v open     >/dev/null 2>&1 && open     "$URL" ) >/dev/null 2>&1 || true
