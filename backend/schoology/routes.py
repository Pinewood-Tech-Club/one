"""
Schoology API routes.
"""
import time

from flask import Blueprint, jsonify, redirect, request

from auth.middleware import auth_required
from config import Config
from db import app_users, schoology_cache_store
from db.encryption import decrypt_token
from db.tokens import (
    delete_schoology_tokens,
    get_schoology_request_token_record,
    save_schoology_access_tokens,
    save_schoology_credentials,
    save_schoology_request_tokens,
)
from services.schoology import SchoologyService, complete_oauth, start_oauth
from services.schoology.refresh import start_schoology_refresh_for_user
from services.schoology.runtime import create_schoology_service

# Blueprint for /oauth/schoology/* routes
oauth_bp = Blueprint('schoology_oauth', __name__, url_prefix='/oauth/schoology')

# Blueprint for /api/schoology/* routes
schoology_api_bp = Blueprint('schoology_api', __name__, url_prefix='/api/schoology')


@schoology_api_bp.route("/developer-override", methods=["POST"])
@auth_required
def schoology_developer_override(user):
    """
    Save per-user Schoology consumer credentials (two-legged auth).
    This is intentionally low-discoverability (frontend provides unlabeled fields).
    """
    data = request.get_json(silent=True) or {}
    client_id = str(data.get("clientId", "")).strip()
    client_secret = str(data.get("clientSecret", "")).strip()

    if not client_id or not client_secret:
        return jsonify({"error": "Invalid credentials"}), 400

    try:
        service = SchoologyService(
            user_id=str(user["id"]),
            access_token=None,
            access_token_secret=None,
            consumer_key=client_id,
            consumer_secret=client_secret,
            schoology_domain=Config.SCHOOLOGY_DOMAIN,
            schoology_api_domain=Config.SCHOOLOGY_API_DOMAIN,
        )

        # Validate credentials with a lightweight call.
        schoology_user = service.get_user_info()
        save_schoology_credentials(user["id"], client_id, client_secret)

        try:
            app_users.set_schoology_connected(user["id"], True)
            app_users.update_onboarding_step(user["id"], "smart_consent")
        except Exception as e:
            print(f"[WARNING] Failed to update onboarding state: {e}")

        try:
            if schoology_user.get("picture_url"):
                app_users.set_profile_picture_url(user["id"], schoology_user["picture_url"])
        except Exception as e:
            print(f"[WARNING] Failed to sync profile picture: {e}")

        return jsonify({
            "success": True,
            "schoology_user": schoology_user,
            "user": app_users.get_api_user(user["id"]),
        })

    except Exception as e:
        print(f"[ERROR] Schoology developer override error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": "Invalid credentials"}), 400


# OAuth routes
@oauth_bp.route("/start")
@auth_required
def schoology_oauth_start(user):
    """Start Schoology OAuth flow by redirecting to Schoology authorization page."""
    try:
        callback_url = f"{Config.BACKEND_URL}/oauth/schoology/callback"

        # Start OAuth flow using the package
        auth_url, request_token, request_token_secret = start_oauth(
            consumer_key=Config.SCHOOLOGY_CONSUMER_KEY,
            consumer_secret=Config.SCHOOLOGY_CONSUMER_SECRET,
            callback_url=callback_url,
            schoology_domain=Config.SCHOOLOGY_DOMAIN
        )

        # Save request tokens for this user
        save_schoology_request_tokens(user["id"], request_token, request_token_secret)

        # Redirect user to Schoology authorization page
        return redirect(auth_url)

    except Exception as e:
        print(f"Schoology OAuth start error: {e}")
        import traceback
        traceback.print_exc()
        return redirect(f"{Config.FRONTEND_URL}?error=schoology_oauth_failed")


@oauth_bp.route("/callback")
def schoology_oauth_callback():
    """
    Schoology OAuth callback endpoint (official three-legged OAuth flow).
    This endpoint is called by Schoology after user authorization.
    """
    try:
        # Check for error parameter (user cancelled or denied access)
        error = request.args.get('error')
        if error:
            print(f"[INFO] Schoology OAuth cancelled/denied: {error}")
            return redirect(f"{Config.FRONTEND_URL}/onboarding?error=access_denied")

        # Get oauth_token from query params (sent by Schoology)
        oauth_token = request.args.get('oauth_token')

        if not oauth_token:
            return redirect(f"{Config.FRONTEND_URL}/onboarding?error=schoology_callback_failed")

        request_record = get_schoology_request_token_record(oauth_token)
        user_id = request_record["user_id"] if request_record else None
        request_token_secret = (
            decrypt_token(request_record["request_token_secret"])
            if request_record
            else None
        )

        if not user_id or not request_token_secret:
            print(f"[ERROR] Could not find user for oauth_token: {oauth_token}")
            return redirect(f"{Config.FRONTEND_URL}/onboarding?error=schoology_callback_failed")

        # Complete OAuth flow using the package
        access_token, access_token_secret = complete_oauth(
            consumer_key=Config.SCHOOLOGY_CONSUMER_KEY,
            consumer_secret=Config.SCHOOLOGY_CONSUMER_SECRET,
            request_token=oauth_token,
            request_token_secret=request_token_secret,
            schoology_domain=Config.SCHOOLOGY_DOMAIN
        )

        if not access_token or not access_token_secret:
            return redirect(f"{Config.FRONTEND_URL}/onboarding?error=schoology_callback_failed")

        # Save access tokens and clear request tokens
        save_schoology_access_tokens(user_id, access_token, access_token_secret)

        print(f"✅ Schoology OAuth successful for user_id: {user_id}")

        # Mark Schoology as connected and advance onboarding
        try:
            app_users.set_schoology_connected(int(user_id), True)
            app_users.update_onboarding_step(int(user_id), "smart_consent")
        except Exception as e:
            print(f"[WARNING] Failed to update onboarding state: {e}")

        # Fetch and cache profile picture
        try:
            service = create_schoology_service(user_id)
            if service:
                schoology_user = service.get_user_info()
                if schoology_user.get("picture_url"):
                    app_users.set_profile_picture_url(int(user_id), schoology_user["picture_url"])
        except Exception as e:
            print(f"[WARNING] Failed to sync profile picture: {e}")

        # Redirect to onboarding page (frontend will show smart_consent step)
        return redirect(f"{Config.FRONTEND_URL}/onboarding?schoology_connected=true")

    except Exception as e:
        print(f"[ERROR] Schoology OAuth callback error: {e}")
        import traceback
        traceback.print_exc()
        return redirect(f"{Config.FRONTEND_URL}/onboarding?error=schoology_callback_failed")


# API routes
@schoology_api_bp.route("/status")
@auth_required
def schoology_status(user):
    """Check if user has connected their Schoology account"""
    try:
        service = create_schoology_service(user["id"])
        if not service:
            return jsonify({"connected": False})

        # Test if credentials are still valid
        try:
            user_info = service.get_user_info()
            return jsonify({
                "connected": True,
                "schoology_user": user_info
            })
        except Exception as e:
            print(f"Schoology API error: {e}")
            import traceback
            traceback.print_exc()
            # Don't delete tokens on first error - could be temporary API issue
            return jsonify({"connected": False, "error": str(e)})

    except Exception as e:
        print(f"Schoology status error: {str(e)}")
        return jsonify({"connected": False, "error": str(e)}), 500


@schoology_api_bp.route("/courses")
@auth_required
def schoology_courses(user):
    """Get user's Schoology courses"""
    try:
        print(f"[DEBUG] /api/schoology/courses called for user_id: {user['id']}")

        service = create_schoology_service(user["id"])
        if not service:
            print(f"[DEBUG] Failed to create Schoology service for user_id: {user['id']}")
            return jsonify({"error": "Schoology account not connected"}), 400

        print("[DEBUG] Fetching courses from Schoology API...")
        courses = service.get_courses()  # Also refreshes the local cache
        print(f"[DEBUG] Retrieved {len(courses)} courses")

        return jsonify({"courses": courses})

    except Exception as e:
        print(f"[ERROR] Schoology courses error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@schoology_api_bp.route("/upcoming")
@auth_required
def schoology_upcoming(user):
    """Get upcoming (future-due) assignments from the local cache."""
    try:
        now_ms = int(time.time() * 1000)
        assignments = schoology_cache_store.get_upcoming(user["id"], now_ms)
        return jsonify({"assignments": assignments})

    except Exception as e:
        print(f"[ERROR] Schoology upcoming error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@schoology_api_bp.route("/refresh", methods=["POST"])
@auth_required
def schoology_refresh(user):
    """Refresh all Schoology data and update the local cache."""
    payload, status_code = start_schoology_refresh_for_user(user["id"])
    return jsonify(payload), status_code

@schoology_api_bp.route("/disconnect", methods=["POST"])
@auth_required
def schoology_disconnect(user):
    """Disconnect Schoology account"""
    try:
        # Create service to clear cache
        service = create_schoology_service(user["id"])
        if service:
            service.disconnect()  # Clear the local cache

        # Delete tokens
        delete_schoology_tokens(user["id"])

        # Mark Schoology as disconnected
        try:
            app_users.set_schoology_connected(user["id"], False)
        except Exception as e:
            print(f"[WARNING] Failed to update connection state: {e}")

        return jsonify({"message": "Schoology account disconnected successfully"})

    except Exception as e:
        print(f"Schoology disconnect error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
