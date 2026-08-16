# syntax=docker/dockerfile:1.7

FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# ── dependencies ─────────────────────────────────────────────────────────────
FROM base AS deps

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt requirements-scraper.txt ./

# Off by default: docling adds ~1 GB (torch + layout models) and the scraper
# degrades to "docling_unavailable" without it. Build with
# --build-arg INSTALL_DOCLING=true to enable PDF/DOCX/PPTX text extraction.
ARG INSTALL_DOCLING=false

RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --upgrade pip \
    && if [ "$INSTALL_DOCLING" = "true" ]; then \
         /opt/venv/bin/pip install -r requirements-scraper.txt; \
       else \
         /opt/venv/bin/pip install -r requirements.txt; \
       fi

# ── runtime ──────────────────────────────────────────────────────────────────
FROM base AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
        curl tini \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 10001 app

COPY --from=deps /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY --chown=app:app . .

# Every SQLite database and all scraped attachments live under /data, which is
# a mounted volume — the image itself holds no user state.
ENV MAIN_DB_PATH=/data/main.db \
    SESSIONS_DB_PATH=/data/api_sessions.db \
    CHAT_DB_PATH=/data/chat.db \
    SCRAPER_DB_PATH=/data/scraper.db \
    SCRAPER_STORAGE_ROOT=/data/storage/schoology \
    FLASK_ENV=production \
    PORT=3111

RUN mkdir -p /data/storage/schoology && chown -R app:app /data

COPY --chown=app:app docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER app
EXPOSE 3111

HEALTHCHECK --interval=30s --timeout=5s --start-period=25s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/api/health" || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["all"]
