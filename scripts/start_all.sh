#!/usr/bin/env bash
set -euo pipefail

# Start ARQ worker in background, then start the bot. Propagate signals to both.

VENV_BIN="${VENV_BIN:-$HOME/.venv/bin}"
export PYTHONUNBUFFERED=1

# Ensure we are in project root (this script is under scripts/)
cd "$(dirname "$0")/.."

# Start worker
"$VENV_BIN/arq" app.worker.main.Settings &
WORKER_PID=$!

echo "[start_all] Worker started PID=$WORKER_PID"

cleanup() {
  echo "[start_all] Stopping..."
  if kill -0 "$WORKER_PID" 2>/dev/null; then
    kill "$WORKER_PID" || true
    wait "$WORKER_PID" || true
  fi
}
trap cleanup INT TERM EXIT

# Start bot (foreground)
"$VENV_BIN/python" -m app.bot.main
