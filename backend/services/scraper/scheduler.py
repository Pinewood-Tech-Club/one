"""
Recurring scheduler entrypoint for the Schoology shared-content scraper.
"""
from __future__ import annotations

import logging
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from config import Config
from db.init import init_scraper_db
from db.tokens import get_schoology_tokens
from db.app_users import list_eligible_scraper_users
from services.schoology.runtime import create_schoology_service

from . import store

logger = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _coerce_backend_user_id(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def refresh_eligible_user_memberships() -> dict[str, int]:
    now = store.utcnow()
    convex_users = list_eligible_scraper_users()
    eligible_count = 0
    refreshed_count = 0

    for user in convex_users:
        user_id = _coerce_backend_user_id(user.get("userId"))
        if user_id is None:
            continue

        schoology_connected = bool(user.get("schoologyConnected"))
        smart_features_enabled = bool(
            (user.get("smartFeaturesConsent") or {}).get("enabled")
        )
        tokens = get_schoology_tokens(user_id)
        service = create_schoology_service(user_id) if tokens else None
        has_valid_credentials = service is not None
        eligible = schoology_connected and smart_features_enabled and has_valid_credentials
        store.upsert_scraper_user(
            user_id,
            eligible=eligible,
            schoology_connected=schoology_connected,
            smart_features_enabled=smart_features_enabled,
            has_valid_credentials=has_valid_credentials,
            last_convex_check_at=now,
            last_credential_error=None if has_valid_credentials else "missing_or_invalid_credentials",
        )

        if not eligible:
            continue

        eligible_count += 1
        try:
            sections = service.get_sections(sync_to_cache=False)
            store.refresh_user_section_memberships(user_id, sections, now)
            store.mark_user_sections_refreshed(user_id, now)
            refreshed_count += 1
        except Exception as exc:
            logger.exception("scraper_membership_refresh_failed user_id=%s", user_id)
            store.upsert_scraper_user(
                user_id,
                eligible=False,
                schoology_connected=schoology_connected,
                smart_features_enabled=smart_features_enabled,
                has_valid_credentials=True,
                last_convex_check_at=now,
                last_credential_error=exc.__class__.__name__,
            )

    return {
        "convex_candidates": len(convex_users),
        "eligible_users": eligible_count,
        "refreshed_users": refreshed_count,
    }


def _spawn_section_worker(section_id: str, credential_user_id: int, owner_token: str) -> None:
    subprocess.Popen(
        [
            sys.executable,
            "-m",
            "services.scraper.worker",
            section_id,
            str(credential_user_id),
            owner_token,
        ],
        cwd=str(BACKEND_ROOT),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def run_scheduler_once() -> dict[str, Any]:
    init_scraper_db()
    now = store.utcnow()
    membership_summary = refresh_eligible_user_memberships()

    active_runs = store.count_active_section_runs(now, Config.SCRAPER_LEASE_STALE_SECONDS)
    available_slots = max(0, Config.SCRAPER_MAX_SECTION_CONCURRENCY - active_runs)
    due_sections = store.list_due_sections(
        now,
        Config.SCRAPER_SYNC_INTERVAL_MINUTES,
        Config.SCRAPER_LEASE_STALE_SECONDS,
        available_slots,
    )

    spawned = 0
    for section_id in due_sections:
        credential_user_id = store.choose_credential_user_for_section(section_id)
        if credential_user_id is None:
            continue
        owner_token = secrets.token_hex(16)
        run_id = store.acquire_section_run(
            section_id,
            credential_user_id,
            owner_token,
            now,
            Config.SCRAPER_LEASE_STALE_SECONDS,
        )
        if run_id is None:
            continue
        _spawn_section_worker(section_id, credential_user_id, owner_token)
        spawned += 1

    summary = {
        **membership_summary,
        "active_runs": active_runs,
        "due_sections": len(due_sections),
        "spawned_runs": spawned,
    }
    logger.info("scraper_scheduler_once result=%s", summary)
    return summary


def run_scheduler_loop(*, poll_seconds: int | None = None) -> None:
    interval = max(5, int(poll_seconds or Config.SCRAPER_SCHEDULER_POLL_SECONDS))
    logger.info("scraper_scheduler_loop_start poll_seconds=%s", interval)
    while True:
        started_at = time.monotonic()
        try:
            run_scheduler_once()
        except Exception:
            logger.exception("scraper_scheduler_loop_iteration_failed")

        elapsed = time.monotonic() - started_at
        sleep_for = max(1.0, interval - elapsed)
        time.sleep(sleep_for)


def main(argv: list[str]) -> int:
    logging.basicConfig(level=logging.INFO)
    if len(argv) != 2 or argv[1] not in {"--once", "--loop"}:
        print("Usage: python -m services.scraper.scheduler --once|--loop", file=sys.stderr)
        return 2

    if argv[1] == "--once":
        run_scheduler_once()
    else:
        run_scheduler_loop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
