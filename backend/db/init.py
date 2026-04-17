"""
Database initialization
"""
import hashlib
import sqlite3

from config import Config
from db.encryption import decrypt_token


def init_db():
    """Initialize both databases"""
    init_sessions_db()
    init_main_db()
    init_scraper_db()


def init_sessions_db():
    """Initialize sessions database for Flask session management"""
    conn = sqlite3.connect(Config.SESSIONS_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cursor = conn.cursor()

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS sessions (
        session_id TEXT PRIMARY KEY,
        user_id INTEGER,  -- References users.id in main.db
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP
    )
    """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sessions_expires_at ON sessions (expires_at)"
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
    migrate_schoology_tokens(cursor)

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS schoology_refresh_leases (
        user_id INTEGER PRIMARY KEY,
        owner_token TEXT NOT NULL,
        started_at TIMESTAMP NOT NULL,
        lease_expires_at TIMESTAMP NOT NULL,
        FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
    )
    """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_schoology_refresh_leases_expires_at "
        "ON schoology_refresh_leases (lease_expires_at)"
    )

    init_mobile_db(cursor)

    conn.commit()
    conn.close()


def init_scraper_db():
    """Initialize the dedicated scraper database and storage root."""
    conn = sqlite3.connect(Config.SCRAPER_DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    cursor = conn.cursor()

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS scraper_users (
        user_id INTEGER PRIMARY KEY,
        eligible INTEGER NOT NULL DEFAULT 0,
        schoology_connected INTEGER NOT NULL DEFAULT 0,
        smart_features_enabled INTEGER NOT NULL DEFAULT 0,
        has_valid_credentials INTEGER NOT NULL DEFAULT 0,
        last_convex_check_at TIMESTAMP,
        last_sections_refresh_at TIMESTAMP,
        last_credential_error TEXT
    )
    """
    )

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS section_memberships (
        user_id INTEGER NOT NULL,
        section_id TEXT NOT NULL,
        role TEXT,
        is_active INTEGER NOT NULL DEFAULT 1,
        last_seen_at TIMESTAMP NOT NULL,
        PRIMARY KEY (user_id, section_id)
    )
    """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_section_memberships_section_id "
        "ON section_memberships (section_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_section_memberships_user_active "
        "ON section_memberships (user_id, is_active)"
    )

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS sections (
        section_id TEXT PRIMARY KEY,
        title TEXT,
        course_title TEXT,
        raw_json TEXT NOT NULL,
        raw_hash TEXT NOT NULL,
        last_discovered_at TIMESTAMP NOT NULL,
        last_scraped_at TIMESTAMP,
        last_successful_sync_at TIMESTAMP,
        deleted_at TIMESTAMP
    )
    """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_sections_last_successful_sync_at "
        "ON sections (last_successful_sync_at)"
    )

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS section_sync_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id TEXT NOT NULL,
        credential_user_id INTEGER NOT NULL,
        owner_token TEXT NOT NULL,
        status TEXT NOT NULL,
        run_started_at TIMESTAMP NOT NULL,
        heartbeat_at TIMESTAMP NOT NULL,
        finished_at TIMESTAMP,
        attempt_count INTEGER NOT NULL DEFAULT 1,
        last_error TEXT
    )
    """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_section_sync_runs_section_status "
        "ON section_sync_runs (section_id, status)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_section_sync_runs_status_heartbeat "
        "ON section_sync_runs (status, heartbeat_at)"
    )

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS section_resources (
        resource_id INTEGER PRIMARY KEY AUTOINCREMENT,
        section_id TEXT NOT NULL,
        schoology_id TEXT NOT NULL,
        resource_type TEXT NOT NULL,
        title TEXT,
        description_preview TEXT,
        published INTEGER,
        available INTEGER,
        due_at TIMESTAMP,
        raw_json TEXT NOT NULL,
        raw_hash TEXT NOT NULL,
        attachment_manifest_hash TEXT NOT NULL,
        first_seen_at TIMESTAMP NOT NULL,
        last_seen_at TIMESTAMP NOT NULL,
        deleted_at TIMESTAMP,
        UNIQUE (section_id, resource_type, schoology_id)
    )
    """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_section_resources_section_type "
        "ON section_resources (section_id, resource_type)"
    )

    cursor.execute(
        """
    CREATE TABLE IF NOT EXISTS attachments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        canonical_key TEXT NOT NULL UNIQUE,
        attachment_id TEXT,
        resource_id INTEGER NOT NULL,
        section_id TEXT NOT NULL,
        parent_schoology_id TEXT NOT NULL,
        parent_resource_type TEXT NOT NULL,
        attachment_kind TEXT NOT NULL,
        title TEXT,
        filename TEXT,
        url TEXT,
        mime_type TEXT,
        filesize INTEGER,
        metadata_json TEXT NOT NULL,
        metadata_hash TEXT NOT NULL,
        downloaded_path TEXT,
        download_hash TEXT,
        first_seen_at TIMESTAMP NOT NULL,
        last_seen_at TIMESTAMP NOT NULL,
        deleted_at TIMESTAMP,
        FOREIGN KEY (resource_id) REFERENCES section_resources (resource_id) ON DELETE CASCADE
    )
    """
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_attachments_section_id "
        "ON attachments (section_id)"
    )
    cursor.execute(
        "CREATE INDEX IF NOT EXISTS idx_attachments_resource_id "
        "ON attachments (resource_id)"
    )

    conn.commit()
    conn.close()

    storage_root = Config.SCRAPER_STORAGE_ROOT
    if storage_root:
        import os
        os.makedirs(storage_root, exist_ok=True)


def migrate_schoology_tokens(cursor: sqlite3.Cursor) -> None:
    """Upgrade Schoology token storage to indexed, single-row-per-user semantics."""
    columns = {
        row[1]: row for row in cursor.execute("PRAGMA table_info(schoology_tokens)").fetchall()
    }
    if "request_token_hash" not in columns:
        cursor.execute("ALTER TABLE schoology_tokens ADD COLUMN request_token_hash TEXT")

    merge_schoology_token_rows(cursor)
    backfill_schoology_request_token_hashes(cursor)

    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_schoology_tokens_user_id "
        "ON schoology_tokens (user_id)"
    )
    cursor.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_schoology_tokens_request_token_hash "
        "ON schoology_tokens (request_token_hash) WHERE request_token_hash IS NOT NULL"
    )


def merge_schoology_token_rows(cursor: sqlite3.Cursor) -> None:
    """Collapse legacy duplicate Schoology token rows down to the newest row per user.

    Preserve the newest row exactly as written. Schoology auth states are
    intentionally mutually exclusive in steady state, so merging missing fields
    forward from older rows can resurrect stale credentials from a different
    auth mode.
    """
    cursor.execute(
        """
        SELECT
            id,
            user_id,
            consumer_key,
            consumer_secret,
            request_token,
            request_token_secret,
            request_token_hash,
            access_token,
            access_token_secret,
            created_at,
            updated_at
        FROM schoology_tokens
        ORDER BY user_id ASC, id DESC
        """
    )
    rows = cursor.fetchall()
    rows_by_user: dict[int, list[sqlite3.Row | tuple]] = {}
    for row in rows:
        rows_by_user.setdefault(row[1], []).append(row)

    for user_rows in rows_by_user.values():
        if len(user_rows) <= 1:
            continue

        cursor.executemany(
            "DELETE FROM schoology_tokens WHERE id = ?",
            [(older[0],) for older in user_rows[1:]],
        )


def backfill_schoology_request_token_hashes(cursor: sqlite3.Cursor) -> None:
    """Populate request token hashes for legacy rows so callbacks can use indexed lookup."""
    cursor.execute(
        """
        SELECT id, request_token
        FROM schoology_tokens
        WHERE request_token IS NOT NULL AND request_token_hash IS NULL
        """
    )
    for row_id, encrypted_request_token in cursor.fetchall():
        request_token = decrypt_token(encrypted_request_token)
        if not request_token:
            continue
        cursor.execute(
            "UPDATE schoology_tokens SET request_token_hash = ? WHERE id = ?",
            (hash_schoology_request_token(request_token), row_id),
        )


def hash_schoology_request_token(request_token: str) -> str:
    return hashlib.sha256(request_token.encode("utf-8")).hexdigest()


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
