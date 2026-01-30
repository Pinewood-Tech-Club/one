"""
Schoology API routes (using schoology_service package)
"""
from flask import Blueprint, jsonify, redirect, request
from config import Config
from auth.middleware import auth_required
from schoology_service import SchoologyService, start_oauth, complete_oauth
from db.tokens import (
    get_schoology_tokens,
    save_schoology_request_tokens,
    save_schoology_access_tokens,
    delete_schoology_tokens
)
from db.encryption import decrypt_token
from onboarding import update_schoology_connected, update_onboarding_step

# Blueprint for /oauth/schoology/* routes
oauth_bp = Blueprint('schoology_oauth', __name__, url_prefix='/oauth/schoology')

# Blueprint for /api/schoology/* routes
schoology_api_bp = Blueprint('schoology_api', __name__, url_prefix='/api/schoology')


def _create_service(user_id: int) -> SchoologyService | None:
    """
    Create a SchoologyService instance for the given user

    Args:
        user_id: User ID

    Returns:
        SchoologyService instance or None if tokens not found
    """
    tokens = get_schoology_tokens(user_id)
    if not tokens or not tokens.get("access_token") or not tokens.get("access_token_secret"):
        return None

    access_token = decrypt_token(tokens["access_token"])
    access_token_secret = decrypt_token(tokens["access_token_secret"])

    if not access_token or not access_token_secret:
        return None

    return SchoologyService(
        user_id=str(user_id),
        access_token=access_token,
        access_token_secret=access_token_secret,
        consumer_key=Config.SCHOOLOGY_CONSUMER_KEY,
        consumer_secret=Config.SCHOOLOGY_CONSUMER_SECRET,
        convex_url=Config.CONVEX_URL,
        schoology_domain=Config.SCHOOLOGY_DOMAIN
    )


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

        # Find which user this request token belongs to
        import sqlite3
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

        # Update Convex: mark Schoology as connected and advance onboarding
        try:
            update_schoology_connected(Config.CONVEX_URL, str(user_id), True)
            update_onboarding_step(Config.CONVEX_URL, str(user_id), "smart_consent")
        except Exception as e:
            print(f"[WARNING] Failed to update Convex onboarding state: {e}")

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
        service = _create_service(user["id"])
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

        service = _create_service(user["id"])
        if not service:
            print(f"[DEBUG] Failed to create Schoology service for user_id: {user['id']}")
            return jsonify({"error": "Schoology account not connected"}), 400

        print(f"[DEBUG] Fetching courses from Schoology API...")
        courses = service.get_courses()  # Automatically syncs to Convex
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
    """Get upcoming assignments within the next N days"""
    try:
        print(f"[DEBUG] /api/schoology/upcoming called for user_id: {user['id']}")

        service = _create_service(user["id"])
        if not service:
            print(f"[DEBUG] Failed to create Schoology service for user_id: {user['id']}")
            return jsonify({"error": "Schoology account not connected"}), 400

        days = request.args.get("days", 7, type=int)
        print(f"[DEBUG] Fetching upcoming assignments for next {days} days...")

        assignments = service.get_upcoming_assignments(days=days)  # Automatically syncs to Convex
        print(f"[DEBUG] Retrieved {len(assignments)} upcoming assignments")

        return jsonify({"assignments": assignments})

    except Exception as e:
        print(f"[ERROR] Schoology upcoming error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@schoology_api_bp.route("/refresh", methods=["POST"])
@auth_required
def schoology_refresh(user):
    """Refresh all Schoology data and update Convex cache"""
    try:
        print(f"[DEBUG] /api/schoology/refresh called for user_id: {user['id']}")

        service = _create_service(user["id"])
        if not service:
            print(f"[DEBUG] Failed to create Schoology service for user_id: {user['id']}")
            return jsonify({"error": "Schoology account not connected"}), 400

        print(f"[DEBUG] Refreshing all Schoology data...")
        result = service.refresh_all()  # Fetches courses + assignments, syncs to Convex
        print(f"[DEBUG] Refresh result: {result}")

        return jsonify({
            "success": True,
            "coursesUpdated": result.get("courses_updated", 0),
            "assignmentsUpdated": result.get("assignments_updated", 0),
            "message": "Cache updated successfully"
        })

    except Exception as e:
        print(f"[ERROR] Schoology refresh error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@schoology_api_bp.route("/disconnect", methods=["POST"])
@auth_required
def schoology_disconnect(user):
    """Disconnect Schoology account"""
    try:
        # Create service to clear cache
        service = _create_service(user["id"])
        if service:
            service.disconnect()  # Clear Convex cache

        # Delete tokens
        delete_schoology_tokens(user["id"])

        # Update Convex: mark Schoology as disconnected
        try:
            update_schoology_connected(Config.CONVEX_URL, str(user["id"]), False)
        except Exception as e:
            print(f"[WARNING] Failed to update Convex: {e}")

        return jsonify({"message": "Schoology account disconnected successfully"})

    except Exception as e:
        print(f"Schoology disconnect error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500
