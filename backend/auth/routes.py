"""
Authentication routes
"""
import hmac
import logging
import secrets
import threading

from flask import Blueprint, jsonify, redirect, request, session

from auth.google import exchange_code_for_token, get_google_auth_url, get_user_info
from config import Config
from db.sessions import create_session, delete_session
from db.users import get_or_create_user
from onboarding import get_or_create_user as convex_get_or_create_user

logger = logging.getLogger(__name__)

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


def _bootstrap_convex_user(user_id: int):
    try:
        convex_get_or_create_user(Config.CONVEX_URL, str(user_id))
    except Exception as exc:
        logger.warning("Failed to create Convex user record for user %s: %s", user_id, exc)


def _bootstrap_convex_user_async(user_id: int):
    threading.Thread(
        target=_bootstrap_convex_user,
        args=(user_id,),
        daemon=True,
        name=f"convex-bootstrap-{user_id}",
    ).start()


@auth_bp.route("/google")
def auth_google():
    """Initiate Google OAuth flow"""
    state = secrets.token_urlsafe(32)
    session["google_oauth_state"] = state
    google_auth_url = get_google_auth_url(state=state)
    return redirect(google_auth_url)


@auth_bp.route("/google/callback")
def auth_google_callback():
    """Handle Google OAuth callback"""
    logger.debug("Google OAuth callback hit")
    logger.debug("Request args keys: %s", sorted(request.args.keys()))
    try:
        returned_state = request.args.get("state", "")
        expected_state = session.pop("google_oauth_state", None)
        if not expected_state or not hmac.compare_digest(returned_state, expected_state):
            return redirect(f"{Config.FRONTEND_URL}?error=invalid_state")

        code = request.args.get("code")
        if not code:
            return redirect(f"{Config.FRONTEND_URL}?error=no_code")

        # Exchange code for token
        token_response = exchange_code_for_token(code)

        if "error" in token_response or "access_token" not in token_response:
            return redirect(f"{Config.FRONTEND_URL}?error=token_failed")

        # Get user info
        user_response = get_user_info(token_response['access_token'])

        if "error" in user_response or "email" not in user_response:
            return redirect(f"{Config.FRONTEND_URL}?error=user_info_failed")

        # Check if the email domain is allowed (legacy code)
        # if not email.endswith("@pinewood.edu"):
        #     return redirect(f"{Config.FRONTEND_URL}?error=invalid_domain")

        # ensure hd param is in user response
        if "hd" not in user_response or user_response["hd"] != "pinewood.edu":
            return redirect(f"{Config.FRONTEND_URL}?error=invalid_domain")

        # Get or create user in main database
        email = user_response["email"]
        google_user_id = user_response.get("id", "")
        name = user_response.get("name", email.split("@")[0])

        # Get or create user account
        user_id = get_or_create_user(google_user_id, email, name)

        # Convex bootstrap can block on network; run it in the background.
        _bootstrap_convex_user_async(user_id)

        # Create session
        session_id = create_session(user_id)
        session["session_id"] = session_id

        # Redirect to frontend
        return redirect(Config.FRONTEND_URL)

    except Exception as e:
        logger.error(f"Authentication error: {e}")
        return redirect(f"{Config.FRONTEND_URL}?error=unexpected")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Logout user"""
    session_id = session.get("session_id")
    if session_id:
        delete_session(session_id)
    session.pop("session_id", None)
    return jsonify({"message": "Logged out successfully"})
