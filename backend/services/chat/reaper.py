"""
Stale-generation reaper.

A single daemon thread fails any queued/streaming generation whose worker has
stopped heartbeating for longer than CHAT_STALE_AFTER_SECONDS.
"""
import logging
import threading
import time

from config import Config
from db import chat_store

logger = logging.getLogger(__name__)

_started = threading.Event()
_stop = threading.Event()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _reaper_loop() -> None:
    stale_after_ms = Config.CHAT_STALE_AFTER_SECONDS * 1000
    while not _stop.wait(Config.CHAT_REAPER_INTERVAL_SECONDS):
        try:
            reaped = chat_store.fail_stale_generations(_now_ms(), stale_after_ms)
            logger.info("chat_reaper_run reaped=%s stale_after_ms=%s", reaped, stale_after_ms)
        except Exception:
            logger.exception("chat_reaper_run_failed")


def start_reaper() -> None:
    """Start the reaper thread exactly once per process."""
    if _started.is_set():
        return
    _started.set()
    threading.Thread(
        target=_reaper_loop,
        daemon=True,
        name="chat-reaper",
    ).start()
    logger.info(
        "chat_reaper_started interval_seconds=%s", Config.CHAT_REAPER_INTERVAL_SECONDS
    )
