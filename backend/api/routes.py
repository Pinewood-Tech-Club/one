"""
General API routes
"""
import json
import time
from hashlib import sha256

from flask import Blueprint, Response, jsonify, request, stream_with_context
from config import Config
from auth.middleware import auth_required
from onboarding import get_user as convex_get_user, update_onboarding_step, save_consent
from services.chat import convex_sync, live_stream

TERMINAL_CHAT_STATUSES = {"completed", "failed", "cancelled"}

api_bp = Blueprint('api', __name__, url_prefix='/api')


@api_bp.route("/health")
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok", "message": "Pinewood One API is running"})


@api_bp.route("/user")
@auth_required
def get_current_user(user):
    """Get current authenticated user with onboarding state"""
    # Get onboarding state from Convex
    onboarding_step = "welcome"
    schoology_connected = False

    try:
        convex_user = convex_get_user(Config.CONVEX_URL, str(user["id"]))
        if convex_user:
            onboarding_step = convex_user.get("onboardingStep", "welcome")
            schoology_connected = convex_user.get("schoologyConnected", False)
    except Exception as e:
        print(f"[WARNING] Failed to get Convex user: {e}")

    return jsonify({
        "user_id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "created_at": user["created_at"],
        "last_login": user["last_login"],
        "onboarding_step": onboarding_step,
        "schoology_connected": schoology_connected
    })


@api_bp.route("/convex-token")
@auth_required
def get_convex_token(user):
    """Get a JWT token for Convex authentication"""
    from auth.jwt_utils import create_convex_token
    token = create_convex_token(user["id"], user["email"], user["name"])
    return jsonify({"token": token})


@api_bp.route("/.well-known/jwks.json")
def get_jwks():
    """
    Get the JSON Web Key Set for JWT verification.
    This endpoint is public and used by Convex to verify JWT signatures.
    """
    from auth.jwt_utils import get_jwks

    jwks = get_jwks()
    payload = json.dumps(jwks, sort_keys=True, separators=(",", ":")).encode("utf-8")
    etag = sha256(payload).hexdigest()

    if request.if_none_match.contains(etag):
        response = api_bp.make_response(("", 304))
    else:
        response = jsonify(jwks)

    response.headers["Cache-Control"] = "public, max-age=3600, stale-while-revalidate=86400"
    response.headers["ETag"] = f"\"{etag}\""
    return response


@api_bp.route("/user/onboarding/start", methods=["POST"])
@auth_required
def start_onboarding(user):
    """
    Called when user clicks "Get Started" on welcome slide.
    Updates onboarding_step from "welcome" to "connect_lms".
    """
    try:
        result = update_onboarding_step(
            Config.CONVEX_URL,
            str(user["id"]),
            "connect_lms"
        )
        return jsonify({"success": True, "step": "connect_lms"})
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

        result = save_consent(
            Config.CONVEX_URL,
            str(user["id"]),
            consent
        )

        return jsonify({
            "success": True,
            "step": "completed",
            "consent": consent
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

    # Authoritative ownership check against Convex (the source of truth for
    # chat generations). This must not fail open: streaming is only allowed
    # once ownership is affirmatively confirmed.
    try:
        owner = convex_sync.get_generation_owner(generation_id)
    except Exception as e:
        print(f"[ERROR] Failed to verify chat generation ownership: {e}")
        return jsonify({"error": "chat_stream_unavailable"}), 503
    if not owner or owner.get("userId") != str(user["id"]):
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
