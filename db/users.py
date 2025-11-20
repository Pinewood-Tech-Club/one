"""
User database operations
"""
import sqlite3
from datetime import datetime
from config import Config


def get_or_create_user(google_user_id, email, name):
    """Get existing user or create new one, return user ID"""
    conn = sqlite3.connect(Config.MAIN_DB_PATH)
    cursor = conn.cursor()

    # Try to find existing user
    cursor.execute("SELECT id, name FROM users WHERE google_user_id = ?", (google_user_id,))
    result = cursor.fetchone()

    if result:
        user_id = result[0]
        # Update last login and name if changed
        cursor.execute(
            "UPDATE users SET name = ?, last_login = ?, updated_at = ? WHERE id = ?",
            (name, datetime.now().isoformat(), datetime.now().isoformat(), user_id)
        )
        conn.commit()
        conn.close()
        return user_id
    else:
        # Create new user
        cursor.execute(
            "INSERT INTO users (google_user_id, email, name, last_login) VALUES (?, ?, ?, ?)",
            (google_user_id, email, name, datetime.now().isoformat())
        )
        user_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return user_id


def get_user_by_id(user_id):
    """Get user data by ID"""
    conn = sqlite3.connect(Config.MAIN_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, google_user_id, email, name, created_at, last_login FROM users WHERE id = ?", (user_id,))
    result = cursor.fetchone()
    conn.close()

    if not result:
        return None

    return {
        "id": result[0],
        "google_user_id": result[1],
        "email": result[2],
        "name": result[3],
        "created_at": result[4],
        "last_login": result[5]
    }

