#!/usr/bin/env bash
set -euo pipefail

# Unified launcher: starts Redis with given flags and the app (worker+bot)
# Usage: VENV_BIN="$HOME/.venv/bin" ./scripts/launch.sh

VENV_BIN="${VENV_BIN:-$HOME/.venv/bin}"
export PYTHONUNBUFFERED=1

# Ensure we are in project root (this script is under scripts/)
cd "$(dirname "$0")/.."

REDIS_PID=""

is_redis_up() {
  if command -v redis-cli >/dev/null 2>&1; then
    redis-cli ping >/dev/null 2>&1 && return 0 || return 1
  fi
  # Fallback TCP check
  (exec 3<>/dev/tcp/127.0.0.1/6379) 2>/dev/null && exec 3>&- && return 0 || return 1
}

start_redis() {
  if is_redis_up; then
    echo "[launch] Reusing existing Redis on 127.0.0.1:6379"
    return 0
  fi
  redis-server --save "" --appendonly no --maxmemory 32mb --maxmemory-policy allkeys-lru &
  REDIS_PID=$!
  echo "[launch] Redis started PID=${REDIS_PID}"
  sleep 0.5
}

cleanup() {
  echo "[launch] Stopping..."
  if [[ -n "${APP_PID:-}" ]] && kill -0 "$APP_PID" 2>/dev/null; then
    kill "$APP_PID" || true
    wait "$APP_PID" || true
  fi
  if [[ -n "${REDIS_PID:-}" ]] && kill -0 "$REDIS_PID" 2>/dev/null; then
    kill "$REDIS_PID" || true
    wait "$REDIS_PID" || true
  fi
}
trap cleanup INT TERM EXIT

start_redis

VENV_BIN="$VENV_BIN" ./scripts/start_all.sh &
APP_PID=$!
wait "$APP_PID"
