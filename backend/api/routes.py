"""
General API routes
"""
import json
import time

from flask import Blueprint, Response, jsonify, request, stream_with_context
from config import Config
from auth.middleware import auth_required
from db import app_users, chat_store
from services.chat import live_stream

TERMINAL_CHAT_STATUSES = {"completed", "failed", "cancelled"}

api_bp = Blueprint('api', __name__, url_prefix='/api')


@api_bp.route("/health")
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok", "message": "Pinewood One API is running"})


@api_bp.route("/user")
@auth_required
def get_current_user(user):
    """Get current authenticated user with onboarding state (from SQLite)."""
    api_user = app_users.get_api_user(user["id"])
    if api_user is None:
        return jsonify({"error": "user_not_found"}), 404
    return jsonify(api_user)


@api_bp.route("/user/onboarding/start", methods=["POST"])
@auth_required
def start_onboarding(user):
    """
    Called when user clicks "Get Started" on welcome slide.
    Updates onboarding_step from "welcome" to "connect_lms".
    """
    try:
        api_user = app_users.update_onboarding_step(user["id"], "connect_lms")
        return jsonify({"success": True, "step": "connect_lms", "user": api_user})
    except Exception as e:
        print(f"[ERROR] Failed to start onboarding: {e}")
        return jsonify({"error": str(e)}), 500


@api_bp.route("/user/consent", methods=["POST"])
@auth_required
def save_user_consent(user):
    """
    Save smart features consent and complete onboarding.

    Request body:
    {
        "enabled": boolean,
        "version": string  # e.g., "1.0"
    }
    """
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "Request body required"}), 400

        enabled = data.get("enabled")
        version = data.get("version", "1.0")

        if enabled is None:
            return jsonify({"error": "enabled field required"}), 400

        consent = {
            "enabled": bool(enabled),
            "timestamp": int(time.time() * 1000),  # milliseconds for JS compatibility
            "version": str(version)
        }

        api_user = app_users.save_consent(user["id"], consent)

        return jsonify({
            "success": True,
            "step": "completed",
            "consent": consent,
            "user": api_user,
        })

    except Exception as e:
        print(f"[ERROR] Failed to save consent: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@api_bp.route("/chat/generations/<generation_id>/events")
@auth_required
def stream_chat_generation_events(generation_id, user):
    if not Config.UPSTASH_REDIS_URL:
        return jsonify({"error": "chat_not_configured"}), 503

    last_event_id = request.headers.get("Last-Event-ID", "").strip()

    # Authoritative, fail-closed ownership check against SQLite (the source of
    # truth), NOT the ephemeral Redis snapshot: the snapshot can be absent
    # (LRU eviction, post-terminal state) and a snapshot-only check would then
    # fail open and stream a victim's generation to any authenticated user.
    generation = chat_store.get_generation(generation_id)
    if not generation or generation.get("userId") != str(user["id"]):
        return jsonify({"error": "generation_not_found"}), 404

    snapshot = live_stream.get_live_state(generation_id)

    @stream_with_context
    def generate():
        current_event_id = last_event_id
        if snapshot:
            latest_event_id = snapshot.get("latestEventId")
            if isinstance(latest_event_id, str):
                current_event_id = latest_event_id
            yield _format_sse("snapshot", snapshot)

            if last_event_id:
                for event in live_stream.replay_events_after(generation_id, last_event_id):
                    current_event_id = event["id"]
                    yield _format_sse(event["type"], event, event_id=event["id"])

        while True:
            events = live_stream.block_for_new_events(
                generation_id,
                current_event_id or "$",
                block_ms=Config.CHAT_SSE_HEARTBEAT_SECONDS * 1000,
            )
            if not events:
                yield ": heartbeat\n\n"
                continue

            for event in events:
                current_event_id = event["id"]
                yield _format_sse(event["type"], event, event_id=event["id"])
                if event["type"] == "terminal":
                    return

    response = Response(generate(), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["X-Accel-Buffering"] = "no"
    return response


def _format_sse(event_name: str, payload, *, event_id: str | None = None):
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_name}")
    lines.append(f"data: {json.dumps(payload, separators=(',', ':'))}")
    return "\n".join(lines) + "\n\n"
