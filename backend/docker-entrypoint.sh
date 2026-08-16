#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-all}"

# --workers 1 is load-bearing, not a resource choice. The generation dedupe set
# in services/chat/launcher.py and the reaper started by create_app() are both
# per-process, so a second worker would double-spawn generations and run a
# second reaper. Concurrency comes from threads instead; SSE handlers block a
# thread each, so raise GUNICORN_THREADS before considering a second worker
# (which needs the dedupe moved into Redis or SQLite first).
start_web() {
  exec gunicorn \
    --bind "0.0.0.0:${PORT:-3111}" \
    --workers 1 \
    --worker-class gthread \
    --threads "${GUNICORN_THREADS:-32}" \
    --timeout "${GUNICORN_TIMEOUT:-120}" \
    --graceful-timeout 30 \
    --keep-alive 65 \
    --access-logfile - \
    --error-logfile - \
    "app:create_app()"
}

start_scraper() {
  exec python -m services.scraper.scheduler --loop
}

case "$MODE" in
  web)
    start_web
    ;;
  scraper)
    start_scraper
    ;;
  all)
    # The scraper shares SQLite files with the API over the local filesystem,
    # so it runs beside it in this container rather than as a separate service.
    python -m services.scraper.scheduler --loop &
    SCRAPER_PID=$!
    trap 'kill -TERM "$SCRAPER_PID" 2>/dev/null || true' TERM INT
    start_web
    ;;
  *)
    # Anything else is run verbatim, so `docker run <image> python -m ...` works.
    exec "$@"
    ;;
esac
