PYTHON ?= python3

.PHONY: setup init doctor dev dev-frontend dev-backend dev-convex dev-tunnel

setup:
	$(PYTHON) scripts/devtools.py setup

init:
	$(PYTHON) scripts/devtools.py init

doctor:
	$(PYTHON) scripts/devtools.py doctor

dev: doctor
	./scripts/dev.sh

dev-frontend:
	$(PYTHON) scripts/devtools.py doctor --component frontend
	cd frontend && pnpm run dev

dev-backend:
	$(PYTHON) scripts/devtools.py doctor --component backend
	cd backend && ./env/bin/python app.py

dev-convex:
	$(PYTHON) scripts/devtools.py doctor --component convex
	$(PYTHON) scripts/devtools.py print-convex-command | /bin/sh

dev-tunnel:
	$(PYTHON) scripts/devtools.py doctor --component backend
	cd backend && cloudflared tunnel --config cloudflared-config.yml run
