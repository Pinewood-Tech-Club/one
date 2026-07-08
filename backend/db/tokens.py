"""
Schoology token database operations
"""
import hashlib
from datetime import datetime

from config import Config
from db.encryption import encrypt_token
from db.pool import get_conn


def hash_schoology_request_token(request_token: str) -> str:
    return hashlib.sha256(request_token.encode("utf-8")).hexdigest()


def _get_schoology_tokens_row_id(conn, user_id):
    cursor = conn.execute("SELECT id FROM schoology_tokens WHERE user_id = ?", (user_id,))
    existing = cursor.fetchone()
    return existing[0] if existing else None


def save_schoology_credentials(user_id, consumer_key, consumer_secret):
    """Save encrypted API credentials for a user (two-legged OAuth)"""
    conn = get_conn(Config.MAIN_DB_PATH)
    row_id = _get_schoology_tokens_row_id(conn, user_id)
    updated_at = datetime.now().isoformat()
    encrypted_consumer_key = encrypt_token(consumer_key)
    encrypted_consumer_secret = encrypt_token(consumer_secret)

    if row_id is not None:
        conn.execute(
            """
            UPDATE schoology_tokens
            SET consumer_key = ?, consumer_secret = ?,
                request_token = NULL, request_token_secret = NULL, request_token_hash = NULL,
                access_token = NULL, access_token_secret = NULL,
                updated_at = ?
            WHERE id = ?
            """,
            (
                encrypted_consumer_key,
                encrypted_consumer_secret,
                updated_at,
                row_id,
            ),
        )
    else:
        cursor = conn.execute(
            """INSERT INTO schoology_tokens
               (user_id, consumer_key, consumer_secret, updated_at)
               VALUES (?, ?, ?, ?)""",
            (user_id, encrypted_consumer_key, encrypted_consumer_secret, updated_at),
        )
        row_id = cursor.lastrowid

    conn.commit()
    return row_id


def delete_schoology_tokens_row(row_id: int):
    """Delete a single schoology_tokens row by id"""
    conn = get_conn(Config.MAIN_DB_PATH)
    conn.execute("DELETE FROM schoology_tokens WHERE id = ?", (row_id,))
    conn.commit()


def save_schoology_request_tokens(user_id, request_token, request_token_secret):
    """Temporarily save request tokens for three-legged OAuth"""
    conn = get_conn(Config.MAIN_DB_PATH)
    row_id = _get_schoology_tokens_row_id(conn, user_id)
    updated_at = datetime.now().isoformat()
    encrypted_request_token = encrypt_token(request_token)
    encrypted_request_token_secret = encrypt_token(request_token_secret)
    request_token_hash = hash_schoology_request_token(request_token)

    if row_id is not None:
        conn.execute(
            """UPDATE schoology_tokens
               SET request_token = ?, request_token_secret = ?, request_token_hash = ?, updated_at = ?
               WHERE id = ?""",
            (
                encrypted_request_token,
                encrypted_request_token_secret,
                request_token_hash,
                updated_at,
                row_id,
            ),
        )
    else:
        conn.execute(
            """
            INSERT INTO schoology_tokens
            (user_id, request_token, request_token_secret, request_token_hash, updated_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                user_id,
                encrypted_request_token,
                encrypted_request_token_secret,
                request_token_hash,
                updated_at,
            ),
        )

    conn.commit()


def save_schoology_access_tokens(user_id, access_token, access_token_secret):
    """Save encrypted access tokens for a user (three-legged OAuth)"""
    conn = get_conn(Config.MAIN_DB_PATH)
    row_id = _get_schoology_tokens_row_id(conn, user_id)
    updated_at = datetime.now().isoformat()

    if row_id is not None:
        conn.execute(
            """UPDATE schoology_tokens
               SET access_token = ?, access_token_secret = ?,
                   consumer_key = NULL, consumer_secret = NULL,
                   request_token = NULL, request_token_secret = NULL, request_token_hash = NULL,
                   updated_at = ?
               WHERE id = ?""",
            (
                encrypt_token(access_token),
                encrypt_token(access_token_secret),
                updated_at,
                row_id,
            ),
        )
    else:
        conn.execute(
            """INSERT INTO schoology_tokens
               (user_id, consumer_key, consumer_secret, request_token_hash, access_token, access_token_secret, updated_at)
               VALUES (?, NULL, NULL, NULL, ?, ?, ?)""",
            (
                user_id,
                encrypt_token(access_token),
                encrypt_token(access_token_secret),
                updated_at,
            ),
        )

    conn.commit()


def get_schoology_tokens(user_id):
    """Get stored tokens/credentials for a user."""
    conn = get_conn(Config.MAIN_DB_PATH)
    cursor = conn.execute(
        """
        SELECT consumer_key, consumer_secret, request_token, request_token_secret, access_token, access_token_secret
        FROM schoology_tokens
        WHERE user_id = ?
        """,
        (user_id,),
    )
    result = cursor.fetchone()

    if not result:
        return None

    return {
        "consumer_key": result[0],
        "consumer_secret": result[1],
        "request_token": result[2],
        "request_token_secret": result[3],
        "access_token": result[4],
        "access_token_secret": result[5],
    }


def get_schoology_request_token_record(request_token: str):
    """Look up a pending Schoology OAuth request token by its hashed value."""
    conn = get_conn(Config.MAIN_DB_PATH)
    cursor = conn.execute(
        """
        SELECT user_id, request_token_secret
        FROM schoology_tokens
        WHERE request_token_hash = ?
        """,
        (hash_schoology_request_token(request_token),),
    )
    result = cursor.fetchone()

    if not result:
        return None

    return {
        "user_id": result[0],
        "request_token_secret": result[1],
    }


def delete_schoology_tokens(user_id):
    """Delete all tokens for a user"""
    conn = get_conn(Config.MAIN_DB_PATH)
    conn.execute("DELETE FROM schoology_tokens WHERE user_id = ?", (user_id,))
    conn.commit()
