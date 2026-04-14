# Pinewood One Monorepo

This repository consolidates the backend and frontend application histories.

## Local Commands

- `make setup` runs the interactive onboarding wizard.
- `make init` installs backend and frontend dependencies, seeds missing local env defaults such as `CONVEX_BRIDGE_SECRET`, and on macOS bootstraps Homebrew plus `python@3.12`, `node`, and `pnpm` when they are missing.
- `make doctor` checks whether local setup is healthy.
- `make secret-export` prints a paste-ready env block containing the current shared secrets from `backend/.env` and `frontend/.env.local`.
- `make dev` starts Convex, the frontend, and the backend together.
- `make dev-frontend`, `make dev-backend`, and `make dev-convex` start one service at a time.
