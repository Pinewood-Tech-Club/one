# Pinewood One Monorepo

This repository consolidates the backend and frontend application histories.

## Local Commands

- `make setup` runs the interactive onboarding wizard.
- `make init` installs backend and frontend dependencies, seeds missing local env defaults such as `FLASK_SECRET_KEY` and `ENCRYPTION_KEY`, and on macOS bootstraps Homebrew plus `python@3.12`, `node`, and `pnpm` when they are missing.
- `make doctor` checks whether local setup is healthy.
- `make secret-export` prints a paste-ready env block containing the current shared secrets from `backend/.env` and `frontend/.env.local`.
- `make dev` starts the frontend, the backend, and the scraper together. State is stored in SQLite and the frontend receives live updates over the SSE events channel at `/api/events`.
- `make dev-frontend` and `make dev-backend` start one service at a time.
