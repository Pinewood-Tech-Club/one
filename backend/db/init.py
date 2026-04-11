"""
Database initialization
"""
import sqlite3
from config import Config


def init_db():
    """Initialize both databases"""
    init_sessions_db()
    init_main_db()


def init_sessions_db():
    """Initialize sessions database for Flask session management"""
    conn = sqlite3.connect(Config.SESSIONS_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cursor = conn.cursor()

    # Drop old tables if they exist
    cursor.execute("DROP TABLE IF EXISTS schoology_tokens")

    # Recreate sessions table with correct structure
    cursor.execute("DROP TABLE IF EXISTS sessions")
    cursor.execute(
        """
    CREATE TABLE sessions (
        session_id TEXT PRIMARY KEY,
        user_id INTEGER,  -- References users.id in main.db
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP
    )
    """
    )

    conn.commit()
    conn.close()


def init_main_db():
    """Initialize main database for persistent user data"""
    conn = sqlite3.connect(Config.MAIN_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cursor = conn.cursor()

    # Users table - persistent user accounts
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        google_user_id TEXT UNIQUE NOT NULL,
        email TEXT UNIQUE NOT NULL,
        name TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        last_login TIMESTAMP
    )
    """
    )

    # Schoology OAuth tokens table
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS schoology_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        consumer_key TEXT,
        consumer_secret TEXT,
        request_token TEXT,
        request_token_secret TEXT,
        access_token TEXT,
        access_token_secret TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """
    )

    init_mobile_db(cursor)

    conn.commit()
    conn.close()


def init_mobile_db(cursor):
    """Initialize mobile auth/session/device tables in main.db."""
    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS mobile_refresh_tokens (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        device_id TEXT NOT NULL,
        issued_at TIMESTAMP NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        revoked_at TIMESTAMP,
        last_used_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """
    )

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS mobile_auth_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code_hash TEXT NOT NULL UNIQUE,
        user_id INTEGER NOT NULL,
        expires_at TIMESTAMP NOT NULL,
        consumed_at TIMESTAMP,
        provider TEXT NOT NULL,
        redirect_uri TEXT NOT NULL,
        state_nonce TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """
    )

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS mobile_devices (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        device_id TEXT NOT NULL,
        platform TEXT NOT NULL,
        app_version TEXT NOT NULL,
        push_token TEXT,
        push_env TEXT,
        locale TEXT,
        timezone TEXT,
        created_at TIMESTAMP NOT NULL,
        updated_at TIMESTAMP NOT NULL,
        last_seen_at TIMESTAMP NOT NULL,
        revoked_at TIMESTAMP,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        UNIQUE (user_id, device_id)
    )
    """
    )

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS mobile_web_session_tickets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        ticket_hash TEXT NOT NULL UNIQUE,
        expires_at TIMESTAMP NOT NULL,
        consumed_at TIMESTAMP,
        device_id TEXT NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """
    )

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS mobile_schoology_oauth_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        request_token_hash TEXT NOT NULL UNIQUE,
        request_token_secret_encrypted TEXT NOT NULL,
        device_id TEXT NOT NULL,
        redirect_uri TEXT NOT NULL,
        code_challenge TEXT NOT NULL,
        code_challenge_method TEXT NOT NULL,
        client_state TEXT,
        expires_at TIMESTAMP NOT NULL,
        consumed_at TIMESTAMP,
        created_at TIMESTAMP NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """
    )

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS mobile_notification_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        device_id TEXT,
        event_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'pending',
        created_at TIMESTAMP NOT NULL,
        available_at TIMESTAMP NOT NULL,
        processed_at TIMESTAMP,
        last_error TEXT,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE,
        CHECK (event_type IN ('assignment_due_soon', 'grade_posted', 'schoology_sync_failed')),
        CHECK (status IN ('pending', 'processing', 'processed', 'failed'))
    )
    """
    )

    # Indexes
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_refresh_user_revoked_exp "
        "ON mobile_refresh_tokens (user_id, revoked_at, expires_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_refresh_device_revoked "
        "ON mobile_refresh_tokens (device_id, revoked_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_codes_exp "
        "ON mobile_auth_codes (expires_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_codes_user_consumed "
        "ON mobile_auth_codes (user_id, consumed_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_devices_device ON mobile_devices (device_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_devices_user_revoked "
        "ON mobile_devices (user_id, revoked_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_tickets_exp ON mobile_web_session_tickets (expires_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_tickets_user_consumed "
        "ON mobile_web_session_tickets (user_id, consumed_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_schoology_req_exp "
        "ON mobile_schoology_oauth_requests (expires_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_schoology_req_user_consumed "
        "ON mobile_schoology_oauth_requests (user_id, consumed_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_notification_status_available "
        "ON mobile_notification_events (status, available_at)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_mobile_notification_user_created "
        "ON mobile_notification_events (user_id, created_at)"
    )
