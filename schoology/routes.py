"""
Schoology API routes (Proxy to Schoology Service)
"""
from flask import Blueprint, jsonify, redirect, request
from config import Config
from auth.middleware import auth_required
from db.tokens import (
    delete_schoology_tokens, 
    save_schoology_access_tokens, 
    save_schoology_request_tokens,
    get_schoology_tokens
)
from db.encryption import decrypt_token
from services.schoology_client import (
    service_oauth_start,
    service_oauth_callback,
    service_status,
    service_courses,
    service_upcoming,
    service_refresh,
    service_disconnect
)
import sqlite3

# Blueprint for /oauth/schoology/* routes
oauth_bp = Blueprint('schoology_oauth', __name__, url_prefix='/oauth/schoology')

# Blueprint for /api/schoology/* routes
schoology_api_bp = Blueprint('schoology_api', __name__, url_prefix='/api/schoology')


# Helper to get decrypted tokens
def get_decrypted_tokens(user_id):
    tokens = get_schoology_tokens(user_id)
    if not tokens:
        return None, None
        
    access_token = decrypt_token(tokens["access_token"]) if tokens.get("access_token") else None
    access_token_secret = decrypt_token(tokens["access_token_secret"]) if tokens.get("access_token_secret") else None
    
    return access_token, access_token_secret

# OAuth routes
@oauth_bp.route("/start")
@auth_required
def schoology_oauth_start(user):
    """Start Schoology OAuth flow by redirecting to Schoology authorization page."""
    try:
        # The callback URL is still this backend's callback endpoint
        callback_url = f"{Config.BACKEND_URL}/oauth/schoology/callback"
        
        result = service_oauth_start(callback_url)
        auth_url = result.get("auth_url")
        request_token = result.get("request_token")
        request_token_secret = result.get("request_token_secret")
        
        if auth_url and request_token and request_token_secret:
            # Save request tokens locally so we can verify the callback
            save_schoology_request_tokens(user["id"], request_token, request_token_secret)
            return redirect(auth_url)
        
        return redirect(f"{Config.FRONTEND_URL}?error=schoology_oauth_failed")
    except Exception as e:
        print(f"Schoology OAuth start error: {e}")
        return redirect(f"{Config.FRONTEND_URL}?error=schoology_oauth_failed")


@oauth_bp.route("/callback")
def schoology_oauth_callback():
    """
    Schoology OAuth callback endpoint (official three-legged OAuth flow).
    This endpoint is called by Schoology after user authorization.
    """
    try:
        # Get oauth_token from query params (sent by Schoology)
        oauth_token = request.args.get('oauth_token')

        if not oauth_token:
            return redirect(f"{Config.FRONTEND_URL}?error=schoology_callback_failed")

        # Find which user this request token belongs to
        # We still need to do this locally because we stored the request token locally
        conn = sqlite3.connect(Config.MAIN_DB_PATH)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT user_id, request_token, request_token_secret FROM schoology_tokens WHERE request_token IS NOT NULL"
        )
        all_tokens = cursor.fetchall()
        conn.close()

        user_id = None
        request_token_secret = None

        # Match the oauth_token with stored encrypted request tokens
        for uid, encrypted_req_token, encrypted_req_secret in all_tokens:
            decrypted_token = decrypt_token(encrypted_req_token)
            if decrypted_token == oauth_token:
                user_id = uid
                request_token_secret = decrypt_token(encrypted_req_secret)
                break

        if not user_id or not request_token_secret:
            print(f"[ERROR] Could not find user for oauth_token: {oauth_token}")
            return redirect(f"{Config.FRONTEND_URL}?error=schoology_callback_failed")

        # Call service to exchange tokens
        result = service_oauth_callback(oauth_token, request_token_secret)
        
        access_token = result.get("access_token")
        access_token_secret = result.get("access_token_secret")

        if not access_token or not access_token_secret:
            return redirect(f"{Config.FRONTEND_URL}?error=schoology_callback_failed")

        # Save access tokens and clear request tokens
        save_schoology_access_tokens(user_id, access_token, access_token_secret)

        print(f"✅ Schoology OAuth successful for user_id: {user_id}")

        # Redirect to frontend with success parameter
        return redirect(f"{Config.FRONTEND_URL}?schoology_connected=true")
    except Exception as e:
        print(f"[ERROR] Schoology OAuth callback error: {e}")
        import traceback
        traceback.print_exc()
        return redirect(f"{Config.FRONTEND_URL}?error=schoology_callback_failed")


# API routes
@schoology_api_bp.route("/status")
@auth_required
def schoology_status(user):
    """Check if user has connected their Schoology account"""
    try:
        at, ats = get_decrypted_tokens(user["id"])
        # If no tokens locally, we know it's not connected
        if not at or not ats:
            return jsonify({"connected": False})
            
        return jsonify(service_status(at, ats))

    except Exception as e:
        print(f"Schoology status error: {str(e)}")
        return jsonify({"connected": False, "error": str(e)}), 500


@schoology_api_bp.route("/courses")
@auth_required
def schoology_courses(user):
    """Get user's Schoology courses"""
    try:
        at, ats = get_decrypted_tokens(user["id"])
        if not at or not ats:
            return jsonify({"error": "Schoology account not connected"}), 400

        return jsonify(service_courses(at, ats))

    except Exception as e:
        print(f"[ERROR] Schoology courses error: {str(e)}")
        return jsonify({"error": str(e)}), 500


@schoology_api_bp.route("/upcoming")
@auth_required
def schoology_upcoming(user):
    """Get upcoming assignments"""
    try:
        at, ats = get_decrypted_tokens(user["id"])
        if not at or not ats:
            return jsonify({"error": "Schoology account not connected"}), 400
            
        days = request.args.get("days", 7, type=int)
        
        return jsonify(service_upcoming(user["id"], at, ats, days))
        
    except Exception as e:
        print(f"[ERROR] Schoology upcoming error: {str(e)}")
        return jsonify({"error": str(e)}), 500


@schoology_api_bp.route("/refresh", methods=["POST"])
@auth_required
def schoology_refresh(user):
    """Refresh Schoology data and update Convex cache"""
    try:
        at, ats = get_decrypted_tokens(user["id"])
        if not at or not ats:
            return jsonify({"error": "Schoology account not connected"}), 400

        return jsonify(service_refresh(user["id"], at, ats))

    except Exception as e:
        print(f"[ERROR] Schoology refresh error: {str(e)}")
        return jsonify({"error": str(e)}), 500


@schoology_api_bp.route("/disconnect", methods=["POST"])
@auth_required
def schoology_disconnect(user):
    """Disconnect Schoology account"""
    try:
        # Call service to clear cache
        service_disconnect(user["id"])
        
        # Delete tokens locally
        delete_schoology_tokens(user["id"])
        return jsonify({"message": "Schoology account disconnected successfully"})
    except Exception as e:
        print(f"Schoology disconnect error: {str(e)}")
        return jsonify({"error": str(e)}), 500
