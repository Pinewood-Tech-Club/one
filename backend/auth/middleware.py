"""
Authentication middleware
"""
from functools import wraps

from flask import jsonify, session

from db.sessions import get_session


def auth_required(func):
    """Decorator to require authentication for API routes"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        session_id = session.get("session_id")
        if not session_id:
            return jsonify({"error": "Authentication required"}), 401

        user_data = get_session(session_id)
        if not user_data:
            session.pop("session_id", None)
            return jsonify({"error": "Invalid session"}), 401

        # Add user info to kwargs
        kwargs["user"] = user_data
        return func(*args, **kwargs)

    return wrapper

