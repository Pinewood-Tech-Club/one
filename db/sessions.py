"""
Session database operations
"""
import sqlite3
import secrets
from datetime import datetime, timedelta
from config import Config
from db.users import get_user_by_id


def create_session(user_id):
    """Create session for a user ID (from main.db)"""
    session_id = secrets.token_hex(32)
    expires_at = datetime.now() + timedelta(days=7)

    conn = sqlite3.connect(Config.SESSIONS_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sessions (session_id, user_id, expires_at) VALUES (?, ?, ?)",
        (session_id, user_id, expires_at),
    )
    conn.commit()
    conn.close()

    return session_id


def get_session(session_id):
    """Get session and return full user data"""
    conn = sqlite3.connect(Config.SESSIONS_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        "SELECT user_id, expires_at FROM sessions WHERE session_id = ?",
        (session_id,),
    )
    result = cursor.fetchone()
    conn.close()

    if not result:
        return None

    user_id, expires_at = result
    expires_at = datetime.fromisoformat(expires_at)

    if expires_at < datetime.now():
        delete_session(session_id)
        return None

    # Get full user data from main database
    user_data = get_user_by_id(user_id)
    if not user_data:
        delete_session(session_id)
        return None

    return user_data


def delete_session(session_id):
    """Delete a session"""
    conn = sqlite3.connect(Config.SESSIONS_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
    conn.commit()
    conn.close()

