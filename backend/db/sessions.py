"""
Session database operations
"""
import secrets
from datetime import datetime, timedelta
from config import Config
from db.pool import get_conn
from db.users import get_user_by_id


def create_session(user_id):
    """Create session for a user ID (from main.db)"""
    session_id = secrets.token_hex(32)
    expires_at = datetime.now() + timedelta(days=7)

    conn = get_conn(Config.SESSIONS_DB_PATH)
    conn.execute(
        "INSERT INTO sessions (session_id, user_id, expires_at) VALUES (?, ?, ?)",
        (session_id, user_id, expires_at),
    )
    conn.commit()

    return session_id


def get_session(session_id):
    """Get session and return full user data"""
    conn = get_conn(Config.SESSIONS_DB_PATH)
    cursor = conn.execute(
        "SELECT user_id, expires_at FROM sessions WHERE session_id = ?",
        (session_id,),
    )
    result = cursor.fetchone()

    if not result:
        return None

    user_id, expires_at = result
    expires_at = datetime.fromisoformat(expires_at)

    if expires_at < datetime.now():
        delete_session(session_id)
        return None

    user_data = get_user_by_id(user_id)
    if not user_data:
        delete_session(session_id)
        return None

    return user_data


def delete_session(session_id):
    """Delete a session"""
    conn = get_conn(Config.SESSIONS_DB_PATH)
    conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()
