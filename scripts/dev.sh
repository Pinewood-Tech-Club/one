#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDS=()
DEV_CONFIG="$ROOT_DIR/.pinewood-dev"

if [[ -f "$DEV_CONFIG" ]]; then
  # shellcheck disable=SC1090
  source "$DEV_CONFIG"
fi

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done

  wait 2>/dev/null || true
}

trap cleanup EXIT INT TERM

(
  cd "$ROOT_DIR/frontend"
  echo "Starting frontend dev server..."
  pnpm run dev
) &
PIDS+=($!)

(
  cd "$ROOT_DIR/backend"
  echo "Starting backend dev server..."
  ./env/bin/python app.py
) &
PIDS+=($!)

(
  cd "$ROOT_DIR/backend"
  echo "Starting scraper scheduler..."
  ./env/bin/python -m services.scraper.scheduler --loop
) &
PIDS+=($!)

wait
