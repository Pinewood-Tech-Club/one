"""
Internal chat execution routes.
"""
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from config import Config
from services.chat import live_stream

internal_chat_bp = Blueprint("internal_chat", __name__, url_prefix="/api/internal/chat")
logger = logging.getLogger(__name__)
_generation_lock = threading.Lock()
_active_generations: set[str] = set()
BACKEND_ROOT = Path(__file__).resolve().parents[1]


def _json_error(code: str, status_code: int, **extra):
    payload = {"error": code}
    payload.update(extra)
    return jsonify(payload), status_code


def _is_valid_internal_secret() -> bool:
    configured_secret = Config.CHAT_INTERNAL_SECRET
    request_secret = request.headers.get("X-Internal-Chat-Secret", "")
    return bool(configured_secret) and request_secret == configured_secret


def _mark_generation_started(generation_id: str) -> bool:
    with _generation_lock:
        if generation_id in _active_generations:
            return False
        _active_generations.add(generation_id)
        return True


def _mark_generation_finished(generation_id: str) -> None:
    with _generation_lock:
        _active_generations.discard(generation_id)


def _watch_generation_process(generation_id: str, process: subprocess.Popen[bytes]) -> None:
    try:
        return_code = process.wait()
        logger.info(
            "chat_generation_worker_exit generation_id=%s return_code=%s",
            generation_id,
            return_code,
        )
    finally:
        _mark_generation_finished(generation_id)


def _now_ms() -> int:
    return int(time.time() * 1000)


@internal_chat_bp.route("/generate", methods=["POST"])
def generate_chat_completion():
    if not Config.CHAT_INTERNAL_SECRET:
        return _json_error("chat_not_configured", 503)

    if not _is_valid_internal_secret():
        return _json_error("invalid_internal_secret", 401)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _json_error("invalid_request", 400)

    generation_id = data.get("generationId")
    if not isinstance(generation_id, str) or not generation_id.strip():
        return _json_error("generation_id_required", 400)

    generation_id = generation_id.strip()

    if not _mark_generation_started(generation_id):
        return jsonify({"generationId": generation_id, "queued": False, "alreadyRunning": True}), 202

    live_stream.initialize_live_state(
        generation_id,
        status="queued",
        content="",
        provider="pending",
        model="pending",
        updated_at=_now_ms(),
    )

    process = subprocess.Popen(
        [sys.executable, "-m", "services.chat.worker", generation_id],
        cwd=str(BACKEND_ROOT),
    )

    threading.Thread(
        target=_watch_generation_process,
        args=(generation_id, process),
        daemon=True,
        name=f"chat-request-{generation_id}",
    ).start()

    return jsonify({"generationId": generation_id, "queued": True}), 202
