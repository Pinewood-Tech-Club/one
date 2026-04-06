"""
General API routes
"""
import json
import time
from hashlib import sha256

from flask import Blueprint, jsonify, request
from config import Config
from auth.middleware import auth_required
from onboarding import get_user as convex_get_user, update_onboarding_step, save_consent

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
