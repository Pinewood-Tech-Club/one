"""
Chat REST API: threads, messages, generation kickoff, and cancel.
"""
import logging
import time

from flask import Blueprint, jsonify, request

from auth.middleware import auth_required
from db import app_users, chat_store
from db.chat_store import ChatStateError
from services.chat.launcher import ChatSpawnError, launch_generation

chat_api_bp = Blueprint("chat_api", __name__, url_prefix="/api/chat")
logger = logging.getLogger(__name__)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json_error(code: str, status_code: int):
    return jsonify({"error": code}), status_code


def _require_entitlement(user):
    """chatModel.ts entitlement gate, now server-side: onboarding completed
    and smart-features consent enabled."""
    if not app_users.is_chat_entitled(user["id"]):
        return _json_error("chat_not_entitled", 403)
    return None


@chat_api_bp.route("/threads", methods=["GET"])
@auth_required
def list_threads(user):
    denied = _require_entitlement(user)
    if denied:
        return denied
    return jsonify({"threads": chat_store.list_threads(user["id"])})


@chat_api_bp.route("/threads/<thread_id>/messages", methods=["GET"])
@auth_required
def list_thread_messages(thread_id, user):
    denied = _require_entitlement(user)
    if denied:
        return denied
    thread = chat_store.get_owned_thread(thread_id, user["id"])
    if not thread:
        return _json_error("thread_not_found", 404)
    return jsonify({"messages": chat_store.list_messages(thread_id)})


@chat_api_bp.route("/threads/<thread_id>/active-generation", methods=["GET"])
@auth_required
def get_active_generation(thread_id, user):
    denied = _require_entitlement(user)
    if denied:
        return denied
    thread = chat_store.get_owned_thread(thread_id, user["id"])
    if not thread:
        return _json_error("thread_not_found", 404)
    return jsonify({"generation": chat_store.get_active_generation_for_thread(thread_id)})


@chat_api_bp.route("/messages", methods=["POST"])
@auth_required
def send_message(user):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _json_error("invalid_request", 400)

    client_request_id = data.get("clientRequestId")
    content = data.get("content")
    thread_id = data.get("threadId")

    if not isinstance(client_request_id, str) or not client_request_id.strip():
        return _json_error("invalid_request", 400)
    if not isinstance(content, str) or not content.strip():
        return _json_error("invalid_request", 400)
    if thread_id is not None and (not isinstance(thread_id, str) or not thread_id.strip()):
        return _json_error("invalid_request", 400)

    client_request_id = client_request_id.strip()
    thread_id = thread_id.strip() if thread_id else None

    denied = _require_entitlement(user)
    if denied:
        return denied

    # Idempotent replay: an already-seen clientRequestId returns the existing
    # ids and must NOT respawn a worker.
    existing = chat_store.get_generation_by_client_request_id(
        user["id"], client_request_id
    )
    if existing:
        return jsonify(
            {
                "threadId": existing["threadId"],
                "userMessageId": existing["userMessageId"],
                "assistantMessageId": existing["assistantMessageId"],
                "generationId": existing["_id"],
                "createdThread": False,
            }
        )

    try:
        result = chat_store.create_generation(
            user["id"],
            thread_id=thread_id,
            client_request_id=client_request_id,
            content=content,
            now_ms=_now_ms(),
        )
    except ChatStateError as exc:
        code = str(exc)
        if code == "thread_busy":
            return _json_error("thread_busy", 409)
        if code == "thread_not_found":
            return _json_error("thread_not_found", 404)
        logger.warning("chat_send_state_error user_id=%s error=%s", user["id"], code)
        return _json_error("chat_state_error", 500)

    try:
        launch_generation(result["generationId"], user_id=user["id"])
    except ChatSpawnError:
        # launch_generation already marked the generation failed (spawn_failed).
        return _json_error("spawn_failed", 500)

    return jsonify(result)


@chat_api_bp.route("/generations/<generation_id>/cancel", methods=["POST"])
@auth_required
def cancel_generation(generation_id, user):
    denied = _require_entitlement(user)
    if denied:
        return denied
    try:
        result = chat_store.request_cancel(generation_id, user["id"])
    except ChatStateError:
        return _json_error("generation_not_found", 404)
    return jsonify(result)
