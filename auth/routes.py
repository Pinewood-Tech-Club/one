"""
Authentication routes
"""
from flask import Blueprint, redirect, request, session, jsonify
from config import Config
from auth.google import get_google_auth_url, exchange_code_for_token, get_user_info
from db.users import get_or_create_user
from db.sessions import create_session, delete_session

auth_bp = Blueprint('auth', __name__, url_prefix='/auth')


@auth_bp.route("/google")
def auth_google():
    """Initiate Google OAuth flow"""
    google_auth_url = get_google_auth_url()
    return redirect(google_auth_url)


@auth_bp.route("/google/callback")
def auth_google_callback():
    """Handle Google OAuth callback"""
    print("=== GOOGLE CALLBACK HIT ===")
    print(f"Request args: {request.args}")
    try:
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

        # Check if the email domain is allowed
        email = user_response["email"]
        if not email.endswith("@pinewood.edu"):
            return redirect(f"{Config.FRONTEND_URL}?error=invalid_domain")

        # Get or create user in main database
        google_user_id = user_response.get("id", "")
        name = user_response.get("name", email.split("@")[0])

        # Get or create user account
        user_id = get_or_create_user(google_user_id, email, name)

        # Create session
        session_id = create_session(user_id)
        session["session_id"] = session_id

        # Redirect to frontend success page
        return redirect(f"{Config.FRONTEND_URL}?success=true")

    except Exception as e:
        print(f"Authentication error: {str(e)}")
        return redirect(f"{Config.FRONTEND_URL}?error=unexpected")


@auth_bp.route("/logout", methods=["POST"])
def logout():
    """Logout user"""
    session_id = session.get("session_id")
    if session_id:
        delete_session(session_id)
    session.pop("session_id", None)
    return jsonify({"message": "Logged out successfully"})

