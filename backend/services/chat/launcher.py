"""
Chat generation worker launcher.

Extracted from the deleted internal_chat blueprint: dedupes per-generation
spawns in-process, initializes Redis live state, and runs the worker as a
subprocess with a watcher thread.
"""
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from db import chat_store

from . import live_stream

logger = logging.getLogger(__name__)

BACKEND_ROOT = Path(__file__).resolve().parents[2]

_generation_lock = threading.Lock()
_active_generations: set[str] = set()


class ChatSpawnError(RuntimeError):
    """Raised when the worker subprocess could not be launched.

    The generation has already been marked failed (spawn_failed) by the time
    this propagates.
    """


def _now_ms() -> int:
    return int(time.time() * 1000)


def _mark_generation_started(generation_id: str) -> bool:
    with _generation_lock:
        if generation_id in _active_generations:
            return False
        _active_generations.add(generation_id)
        return True


def _mark_generation_finished(generation_id: str) -> None:
    with _generation_lock:
        _active_generations.discard(generation_id)


def _watch_generation_process(generation_id: str, process: subprocess.Popen) -> None:
    try:
        return_code = process.wait()
        logger.info(
            "chat_generation_worker_exit generation_id=%s return_code=%s",
            generation_id,
            return_code,
        )
    finally:
        _mark_generation_finished(generation_id)


def _mark_spawn_failed(generation_id: str, error_message: str) -> None:
    completed_at = _now_ms()
    try:
        chat_store.mark_generation_failed(
            generation_id,
            "spawn_failed",
            error_message,
            completed_at,
        )
    except Exception:
        logger.exception(
            "chat_launcher_mark_failed_error generation_id=%s", generation_id
        )
    try:
        live_stream.publish_terminal(
            generation_id,
            status="failed",
            content="",
            updated_at=completed_at,
            error_code="spawn_failed",
            error_message=error_message,
        )
    except Exception:
        logger.warning(
            "chat_launcher_publish_terminal_error generation_id=%s", generation_id
        )


def launch_generation(generation_id: str, *, user_id: int) -> bool:
    """Spawn the generation worker subprocess.

    Returns False when the generation is already being run by this process
    (dedupe), True when a worker was spawned. Raises ChatSpawnError on launch
    failure, after marking the generation failed with error_code=spawn_failed.
    """
    if not _mark_generation_started(generation_id):
        logger.info(
            "chat_generation_already_running generation_id=%s", generation_id
        )
        return False

    try:
        live_stream.initialize_live_state(
            generation_id,
            status="queued",
            content="",
            provider="pending",
            model="pending",
            updated_at=_now_ms(),
            user_id=str(user_id),
        )

        process = subprocess.Popen(
            [sys.executable, "-m", "services.chat.worker", generation_id],
            cwd=str(BACKEND_ROOT),
        )
    except Exception as exc:
        _mark_generation_finished(generation_id)
        message = str(exc) or exc.__class__.__name__
        logger.exception("chat_generation_spawn_failed generation_id=%s", generation_id)
        _mark_spawn_failed(generation_id, message)
        raise ChatSpawnError(message) from exc

    threading.Thread(
        target=_watch_generation_process,
        args=(generation_id, process),
        daemon=True,
        name=f"chat-request-{generation_id}",
    ).start()

    return True
