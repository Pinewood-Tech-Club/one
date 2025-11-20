"""
Schoology token database operations
"""
import sqlite3
from datetime import datetime
from config import Config
from db.encryption import encrypt_token, decrypt_token


def save_schoology_credentials(user_id, consumer_key, consumer_secret):
    """Save encrypted API credentials for a user (two-legged OAuth)"""
    conn = sqlite3.connect(Config.MAIN_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """INSERT OR REPLACE INTO schoology_tokens
           (user_id, consumer_key, consumer_secret, updated_at)
           VALUES (?, ?, ?, ?)""",
        (user_id, encrypt_token(consumer_key), encrypt_token(consumer_secret), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def save_schoology_request_tokens(user_id, request_token, request_token_secret):
    """Temporarily save request tokens for three-legged OAuth"""
    conn = sqlite3.connect(Config.MAIN_DB_PATH)
    cursor = conn.cursor()

    # Check if a row exists for this user
    cursor.execute("SELECT id FROM schoology_tokens WHERE user_id = ? ORDER BY rowid DESC LIMIT 1", (user_id,))
    existing = cursor.fetchone()

    if existing:
        # Update existing row
        cursor.execute(
            """UPDATE schoology_tokens
               SET request_token = ?, request_token_secret = ?, updated_at = ?
               WHERE id = ?""",
            (encrypt_token(request_token), encrypt_token(request_token_secret), datetime.now().isoformat(), existing[0])
        )
    else:
        # Insert new row
        cursor.execute(
            """INSERT INTO schoology_tokens (user_id, request_token, request_token_secret, updated_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, encrypt_token(request_token), encrypt_token(request_token_secret), datetime.now().isoformat())
        )

    conn.commit()
    conn.close()


def save_schoology_access_tokens(user_id, access_token, access_token_secret):
    """Save encrypted access tokens for a user (three-legged OAuth)"""
    conn = sqlite3.connect(Config.MAIN_DB_PATH)
    cursor = conn.cursor()

    # Check if a row exists for this user
    cursor.execute("SELECT id FROM schoology_tokens WHERE user_id = ? ORDER BY rowid DESC LIMIT 1", (user_id,))
    existing = cursor.fetchone()

    if existing:
        # Update existing row and clear request tokens
        cursor.execute(
            """UPDATE schoology_tokens
               SET access_token = ?, access_token_secret = ?,
                   request_token = NULL, request_token_secret = NULL,
                   updated_at = ?
               WHERE id = ?""",
            (encrypt_token(access_token), encrypt_token(access_token_secret), datetime.now().isoformat(), existing[0])
        )
    else:
        # Insert new row
        cursor.execute(
            """INSERT INTO schoology_tokens (user_id, access_token, access_token_secret, updated_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, encrypt_token(access_token), encrypt_token(access_token_secret), datetime.now().isoformat())
        )

    conn.commit()
    conn.close()


def get_schoology_tokens(user_id):
    """Get stored tokens/credentials for a user (most recent row)"""
    conn = sqlite3.connect(Config.MAIN_DB_PATH)
    cursor = conn.cursor()
    cursor.execute(
        """SELECT consumer_key, consumer_secret, request_token, request_token_secret, access_token, access_token_secret
           FROM schoology_tokens WHERE user_id = ? ORDER BY rowid DESC LIMIT 1""",
        (user_id,)
    )
    result = cursor.fetchone()
    conn.close()

    if not result:
        return None

    return {
        'consumer_key': result[0],
        'consumer_secret': result[1],
        'request_token': result[2],
        'request_token_secret': result[3],
        'access_token': result[4],
        'access_token_secret': result[5]
    }


def delete_schoology_tokens(user_id):
    """Delete all tokens for a user"""
    conn = sqlite3.connect(Config.MAIN_DB_PATH)
    cursor = conn.cursor()
    cursor.execute("DELETE FROM schoology_tokens WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

