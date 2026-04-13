"""
Schoology refresh orchestration.
"""
import logging
from pathlib import Path
import subprocess
import sys
import threading

from .runtime import create_schoology_service

logger = logging.getLogger(__name__)
_refresh_lock = threading.Lock()
_active_refreshes: set[int] = set()
BACKEND_ROOT = Path(__file__).resolve().parents[2]


def _mark_refresh_started(user_id: int) -> bool:
    with _refresh_lock:
        if user_id in _active_refreshes:
            return False
        _active_refreshes.add(user_id)
        return True


def _mark_refresh_completed(user_id: int) -> None:
    with _refresh_lock:
        _active_refreshes.discard(user_id)
    logger.info("schoology_refresh_complete user_id=%s", user_id)


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

    if not _mark_refresh_started(user_id):
        logger.info("schoology_refresh_already_running user_id=%s", user_id)
        return {
            "success": True,
            "alreadyInProgress": True,
            "message": "Refresh already in progress",
        }, 200

    process = subprocess.Popen(
        [sys.executable, "-m", "services.schoology.worker", str(user_id)],
        cwd=str(BACKEND_ROOT),
    )

    threading.Thread(
        target=_watch_refresh_process,
        args=(user_id, process),
        daemon=True,
        name=f"schoology-refresh-{user_id}",
    ).start()

    return {
        "success": True,
        "refreshStarted": True,
        "message": "Refresh started",
    }, 202


def _watch_refresh_process(user_id: int, process: subprocess.Popen[bytes]) -> None:
    try:
        return_code = process.wait()
        logger.info(
            "schoology_refresh_worker_exit user_id=%s return_code=%s",
            user_id,
            return_code,
        )
    finally:
        _mark_refresh_completed(user_id)
