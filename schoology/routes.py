"""
Schoology API routes
"""
from flask import Blueprint, jsonify, redirect, request
from config import Config
from auth.middleware import auth_required
from schoology.client import create_schoology_client
from schoology.oauth import start_oauth_flow, handle_oauth_callback
from db.tokens import delete_schoology_tokens, save_schoology_access_tokens

# Blueprint for /oauth/schoology/* routes
oauth_bp = Blueprint('schoology_oauth', __name__, url_prefix='/oauth/schoology')

# Blueprint for /api/schoology/* routes
schoology_api_bp = Blueprint('schoology_api', __name__, url_prefix='/api/schoology')


# OAuth routes
@oauth_bp.route("/start")
@auth_required
def schoology_oauth_start(user):
    """Start Schoology OAuth flow by redirecting to Schoology authorization page."""
    try:
        auth_url = start_oauth_flow(user["id"])
        # Redirect user to Schoology authorization page
        return redirect(auth_url)
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

        # Handle the callback and exchange tokens
        user_id, access_token, access_token_secret = handle_oauth_callback(oauth_token)

        if not user_id or not access_token or not access_token_secret:
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
        sc = create_schoology_client(user["id"])
        if not sc:
            return jsonify({"connected": False})

        # Test if credentials are still valid
        try:
            user_data = sc.get_me()
            return jsonify({
                "connected": True,
                "schoology_user": {
                    "id": user_data.uid,
                    "name": user_data.name_display,
                    "email": getattr(user_data, 'primary_email', '')
                }
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
        sc = create_schoology_client(user["id"])
        if not sc:
            print(f"[DEBUG] Failed to create Schoology client for user_id: {user['id']}")
            return jsonify({"error": "Schoology account not connected"}), 400

        print(f"[DEBUG] Schoology client created, fetching sections...")
        # Get user's sections (courses)
        sections = sc.get_sections()
        print(f"[DEBUG] Retrieved {len(sections) if sections else 0} sections")

        # Format course data
        # courses = []
        # for section in sections:
        #     courses.append({
        #         "id": section.id,
        #         "title": getattr(section, 'course_title', ''),
        #         "section_title": getattr(section, 'section_title', ''),
        #         "subject_area": getattr(section, 'subject_area', ''),
        #         "grade_level": getattr(section, 'grade_level', ''),
        #         "building": getattr(section, 'building', ''),
        #         "access_code": getattr(section, 'access_code', '')
        #     })

        # Instead of returning some fields, return the entire section object in JSON format
        courses = [section.__dict__ for section in sections]

        print(f"[DEBUG] Returning {len(courses)} courses")
        return jsonify({"courses": courses})

    except Exception as e:
        print(f"[ERROR] Schoology courses error: {str(e)}")
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@schoology_api_bp.route("/refresh", methods=["POST"])
@auth_required
def schoology_refresh(user):
    """Refresh Schoology data and update Convex cache"""
    try:
        from convex_client import update_courses_cache

        print(f"[DEBUG] /api/schoology/refresh called for user_id: {user['id']}")
        sc = create_schoology_client(user["id"])
        if not sc:
            print(f"[DEBUG] Failed to create Schoology client for user_id: {user['id']}")
            return jsonify({"error": "Schoology account not connected"}), 400

        print(f"[DEBUG] Fetching courses from Schoology API...")
        # Get user's sections (courses)
        sections = sc.get_sections()
        print(f"[DEBUG] Retrieved {len(sections) if sections else 0} sections")

        # Convert to dict format
        courses = [section.__dict__ for section in sections]

        # Update Convex cache
        print(f"[DEBUG] Updating Convex cache with {len(courses)} courses...")
        result = update_courses_cache(str(user["id"]), courses)
        print(f"[DEBUG] Convex update result: {result}")

        return jsonify({
            "success": True,
            "coursesUpdated": len(courses),
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
        from convex_client import clear_cache

        # Clear Convex cache
        clear_cache(str(user["id"]))

        # Delete tokens
        delete_schoology_tokens(user["id"])
        return jsonify({"message": "Schoology account disconnected successfully"})
    except Exception as e:
        print(f"Schoology disconnect error: {str(e)}")
        return jsonify({"error": str(e)}), 500

