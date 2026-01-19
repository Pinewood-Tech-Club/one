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
    return jsonify(get_jwks())

