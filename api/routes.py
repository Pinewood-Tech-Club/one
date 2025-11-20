"""
General API routes
"""
from flask import Blueprint, jsonify
from auth.middleware import auth_required

api_bp = Blueprint('api', __name__, url_prefix='/api')


@api_bp.route("/health")
def health():
    """Health check endpoint"""
    return jsonify({"status": "ok", "message": "Pinewood One API is running"})


@api_bp.route("/user")
@auth_required
def get_current_user(user):
    """Get current authenticated user"""
    return jsonify({
        "user_id": user["id"],
        "email": user["email"],
        "name": user["name"],
        "created_at": user["created_at"],
        "last_login": user["last_login"]
    })

