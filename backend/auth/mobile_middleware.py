"""
Bearer-token middleware for mobile API routes.
"""
from functools import wraps

from flask import g, jsonify, request

from auth.jwt_utils import verify_mobile_access_token
from db.users import get_user_by_id


def _extract_bearer_token() -> str | None:
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return None
    token = auth_header[len("Bearer ") :].strip()
    return token or None


def mobile_auth_required(func):
    """Require a valid mobile access token and attach user context."""

    @wraps(func)
    def wrapper(*args, **kwargs):
        token = _extract_bearer_token()
        if not token:
            return jsonify({"error": "authentication_required"}), 401

        payload = verify_mobile_access_token(token)
        if not payload:
            return jsonify({"error": "invalid_token"}), 401

        try:
            user_id = int(payload.get("sub", ""))
        except (TypeError, ValueError):
            return jsonify({"error": "invalid_token"}), 401

        user_data = get_user_by_id(user_id)
        if not user_data:
            return jsonify({"error": "invalid_token"}), 401

        g.mobile_user = {
            "id": user_data["id"],
            "device_id": payload.get("device_id"),
        }
        kwargs["user"] = user_data
        kwargs["token_payload"] = payload
        return func(*args, **kwargs)

    return wrapper
