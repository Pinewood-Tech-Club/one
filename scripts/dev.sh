#!/bin/bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PIDS=()
DEV_CONFIG="$ROOT_DIR/.pinewood-dev"
CONVEX_ARGS=()

if [[ -f "$DEV_CONFIG" ]]; then
  # shellcheck disable=SC1090
  source "$DEV_CONFIG"
fi

if [[ "${CONVEX_DEV_MODE:-cloud}" == "local" ]]; then
  CONVEX_ARGS+=(--local)
fi

remove_generated_frontend_gitignore() {
  local frontend_gitignore="$ROOT_DIR/frontend/.gitignore"
  if [[ -f "$frontend_gitignore" ]]; then
    rm -f "$frontend_gitignore"
  fi
}

cleanup() {
  for pid in "${PIDS[@]:-}"; do
    if kill -0 "$pid" 2>/dev/null; then
      kill "$pid" 2>/dev/null || true
    fi
  done

  wait 2>/dev/null || true
}

trap cleanup EXIT INT TERM

remove_generated_frontend_gitignore

(
  cd "$ROOT_DIR/frontend"
  echo "Starting Convex dev server..."
  if ((${#CONVEX_ARGS[@]})); then
    pnpm exec convex dev "${CONVEX_ARGS[@]}" &
  else
    pnpm exec convex dev &
  fi
  convex_pid=$!
  for _ in {1..20}; do
    if [[ -f .gitignore ]]; then
      rm -f .gitignore
      break
    fi
    sleep 0.5
  done
  wait "$convex_pid"
) &
PIDS+=($!)

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
