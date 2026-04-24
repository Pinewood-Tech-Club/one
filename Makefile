DEVTOOLS := /bin/bash scripts/run-devtools.sh

.PHONY: setup init doctor secret-export dev dev-frontend dev-backend dev-convex dev-tunnel dev-scraper

setup:
	$(DEVTOOLS) setup

init:
	$(DEVTOOLS) init

doctor:
	$(DEVTOOLS) doctor

secret-export:
	$(DEVTOOLS) secret-export

dev: doctor
	./scripts/dev.sh

dev-frontend:
	$(DEVTOOLS) doctor --component frontend
	cd frontend && pnpm run dev

dev-backend:
	$(DEVTOOLS) doctor --component backend
	cd backend && ./env/bin/python app.py

dev-convex:
	$(DEVTOOLS) doctor --component convex
	$(DEVTOOLS) print-convex-command | /bin/sh

dev-tunnel:
	$(DEVTOOLS) doctor --component backend
	cd backend && cloudflared tunnel --config cloudflared-config.yml run

dev-scraper:
	$(DEVTOOLS) doctor --component backend
	cd backend && ./env/bin/python -m services.scraper.scheduler --loop
