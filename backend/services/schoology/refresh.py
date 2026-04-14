"""
Schoology refresh orchestration.
"""
import logging
from pathlib import Path
import secrets
import subprocess
import sys

from config import Config
from db.job_leases import acquire_schoology_refresh_lease, utcnow
from .runtime import create_schoology_service

logger = logging.getLogger(__name__)
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def run_schoology_refresh_for_user(user_id: int) -> None:
    logger.info("schoology_refresh_start user_id=%s", user_id)
    service = create_schoology_service(user_id)
    if not service:
        raise RuntimeError("Schoology account not connected")

    result = service.refresh_all()
    logger.info("schoology_refresh_result user_id=%s result=%s", user_id, result)


def start_schoology_refresh_for_user(user_id: int) -> tuple[dict, int]:
    """
    Start an out-of-process refresh if one is not already running.
    """
    service = create_schoology_service(user_id)
    if not service:
        logger.info("schoology_refresh_not_connected user_id=%s", user_id)
        return {"error": "Schoology account not connected"}, 400

    owner_token = secrets.token_hex(16)
    if not acquire_schoology_refresh_lease(
        user_id=user_id,
        owner_token=owner_token,
        now=utcnow(),
        lease_ttl_seconds=Config.SCHOOLOGY_REFRESH_LEASE_TTL_SECONDS,
    ):
        logger.info("schoology_refresh_already_running user_id=%s", user_id)
        return {
            "success": True,
            "alreadyInProgress": True,
            "message": "Refresh already in progress",
        }, 200

    try:
        subprocess.Popen(
            [sys.executable, "-m", "services.schoology.worker", str(user_id), owner_token],
            cwd=str(BACKEND_ROOT),
        )
    except Exception:
        from db.job_leases import release_schoology_refresh_lease

        release_schoology_refresh_lease(user_id, owner_token)
        raise

    return {
        "success": True,
        "refreshStarted": True,
        "message": "Refresh started",
    }, 202
