"""
Database operations for mobile auth, refresh tokens, devices, and web session tickets.
"""
import json
import sqlite3
from datetime import datetime, timezone

from config import Config
from db.pool import get_conn


def _connect() -> sqlite3.Connection:
    return get_conn(Config.MAIN_DB_PATH)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_db_time(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse_db_time(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def insert_mobile_auth_code(
    code_hash: str,
    user_id: int,
    expires_at: datetime,
    provider: str,
    redirect_uri: str,
    state_nonce: str,
):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO mobile_auth_codes
        (code_hash, user_id, expires_at, consumed_at, provider, redirect_uri, state_nonce)
        VALUES (?, ?, ?, NULL, ?, ?, ?)
        """,
        (code_hash, user_id, to_db_time(expires_at), provider, redirect_uri, state_nonce),
    )
    conn.commit()


def consume_mobile_auth_code(code_hash: str, now: datetime) -> tuple[str, dict | None]:
    conn = _connect()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            SELECT id, user_id, expires_at, consumed_at, provider, redirect_uri, state_nonce
            FROM mobile_auth_codes
            WHERE code_hash = ?
            """,
            (code_hash,),
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return "invalid", None

        row_dict = dict(row)
        if row_dict["consumed_at"] is not None:
            conn.commit()
            return "consumed", row_dict

        expires_at = parse_db_time(row_dict["expires_at"])
        if not expires_at or expires_at <= now:
            conn.commit()
            return "expired", row_dict

        cursor.execute(
            "UPDATE mobile_auth_codes SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL",
            (to_db_time(now), row_dict["id"]),
        )
        if cursor.rowcount != 1:
            conn.commit()
            return "consumed", row_dict

        conn.commit()
        return "ok", row_dict
    except Exception:
        conn.rollback()
        raise


def insert_mobile_refresh_token(
    user_id: int,
    token_hash: str,
    device_id: str,
    issued_at: datetime,
    expires_at: datetime,
):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO mobile_refresh_tokens
        (user_id, token_hash, device_id, issued_at, expires_at, revoked_at, last_used_at)
        VALUES (?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            user_id,
            token_hash,
            device_id,
            to_db_time(issued_at),
            to_db_time(expires_at),
            to_db_time(issued_at),
        ),
    )
    conn.commit()


def _revoke_active_for_user_device(cursor: sqlite3.Cursor, user_id: int, device_id: str, now: datetime):
    cursor.execute(
        """
        UPDATE mobile_refresh_tokens
        SET revoked_at = ?, last_used_at = ?
        WHERE user_id = ? AND device_id = ? AND revoked_at IS NULL
        """,
        (to_db_time(now), to_db_time(now), user_id, device_id),
    )


def rotate_mobile_refresh_token(
    token_hash: str,
    new_token_hash: str,
    request_device_id: str,
    now: datetime,
    new_expires_at: datetime,
) -> tuple[str, dict | None]:
    conn = _connect()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            SELECT id, user_id, device_id, expires_at, revoked_at
            FROM mobile_refresh_tokens
            WHERE token_hash = ?
            """,
            (token_hash,),
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return "invalid", None

        row_dict = dict(row)
        expires_at = parse_db_time(row_dict["expires_at"])
        revoked_at = parse_db_time(row_dict["revoked_at"])

        if revoked_at is not None:
            if expires_at and expires_at > now:
                _revoke_active_for_user_device(cursor, row_dict["user_id"], row_dict["device_id"], now)
                conn.commit()
                return "reuse_detected", row_dict
            conn.commit()
            return "invalid", row_dict

        if not expires_at or expires_at <= now:
            cursor.execute(
                "UPDATE mobile_refresh_tokens SET revoked_at = ?, last_used_at = ? WHERE id = ?",
                (to_db_time(now), to_db_time(now), row_dict["id"]),
            )
            conn.commit()
            return "invalid", row_dict

        if row_dict["device_id"] != request_device_id:
            conn.commit()
            return "device_mismatch", row_dict

        cursor.execute(
            """
            UPDATE mobile_refresh_tokens
            SET revoked_at = ?, last_used_at = ?
            WHERE id = ? AND revoked_at IS NULL
            """,
            (to_db_time(now), to_db_time(now), row_dict["id"]),
        )
        if cursor.rowcount != 1:
            conn.commit()
            return "invalid", row_dict

        cursor.execute(
            """
            INSERT INTO mobile_refresh_tokens
            (user_id, token_hash, device_id, issued_at, expires_at, revoked_at, last_used_at)
            VALUES (?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                row_dict["user_id"],
                new_token_hash,
                row_dict["device_id"],
                to_db_time(now),
                to_db_time(new_expires_at),
                to_db_time(now),
            ),
        )

        conn.commit()
        return "ok", {
            "user_id": row_dict["user_id"],
            "device_id": row_dict["device_id"],
        }
    except Exception:
        conn.rollback()
        raise


def revoke_mobile_refresh_token_for_user(token_hash: str, user_id: int, now: datetime) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE mobile_refresh_tokens
        SET revoked_at = COALESCE(revoked_at, ?), last_used_at = ?
        WHERE token_hash = ? AND user_id = ?
        """,
        (to_db_time(now), to_db_time(now), token_hash, user_id),
    )
    affected = cursor.rowcount
    conn.commit()
    return affected


def revoke_mobile_refresh_tokens_for_user(user_id: int, now: datetime):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE mobile_refresh_tokens
        SET revoked_at = COALESCE(revoked_at, ?), last_used_at = ?
        WHERE user_id = ?
        """,
        (to_db_time(now), to_db_time(now), user_id),
    )
    conn.commit()


def revoke_mobile_refresh_tokens_for_device(user_id: int, device_id: str, now: datetime):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE mobile_refresh_tokens
        SET revoked_at = COALESCE(revoked_at, ?), last_used_at = ?
        WHERE user_id = ? AND device_id = ?
        """,
        (to_db_time(now), to_db_time(now), user_id, device_id),
    )
    conn.commit()


def upsert_mobile_device(
    user_id: int,
    device_id: str,
    platform: str,
    app_version: str,
    push_token: str | None,
    push_env: str | None,
    locale: str | None,
    timezone_value: str | None,
    now: datetime,
):
    now_str = to_db_time(now)
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO mobile_devices
        (user_id, device_id, platform, app_version, push_token, push_env, locale, timezone,
         created_at, updated_at, last_seen_at, revoked_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(user_id, device_id) DO UPDATE SET
            platform = excluded.platform,
            app_version = excluded.app_version,
            push_token = excluded.push_token,
            push_env = excluded.push_env,
            locale = excluded.locale,
            timezone = excluded.timezone,
            updated_at = excluded.updated_at,
            last_seen_at = excluded.last_seen_at,
            revoked_at = NULL
        """,
        (
            user_id,
            device_id,
            platform,
            app_version,
            push_token,
            push_env,
            locale,
            timezone_value,
            now_str,
            now_str,
            now_str,
        ),
    )
    conn.commit()


def revoke_mobile_device(user_id: int, device_id: str, now: datetime) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE mobile_devices
        SET revoked_at = COALESCE(revoked_at, ?), updated_at = ?
        WHERE user_id = ? AND device_id = ?
        """,
        (to_db_time(now), to_db_time(now), user_id, device_id),
    )
    affected = cursor.rowcount
    conn.commit()
    return affected


def insert_mobile_web_ticket(
    ticket_hash: str,
    user_id: int,
    device_id: str,
    expires_at: datetime,
):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO mobile_web_session_tickets
        (user_id, ticket_hash, expires_at, consumed_at, device_id)
        VALUES (?, ?, ?, NULL, ?)
        """,
        (user_id, ticket_hash, to_db_time(expires_at), device_id),
    )
    conn.commit()


def consume_mobile_web_ticket(ticket_hash: str, now: datetime) -> tuple[str, dict | None]:
    conn = _connect()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            SELECT id, user_id, expires_at, consumed_at, device_id
            FROM mobile_web_session_tickets
            WHERE ticket_hash = ?
            """,
            (ticket_hash,),
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return "invalid", None

        row_dict = dict(row)
        if row_dict["consumed_at"] is not None:
            conn.commit()
            return "consumed", row_dict

        expires_at = parse_db_time(row_dict["expires_at"])
        if not expires_at or expires_at <= now:
            conn.commit()
            return "expired", row_dict

        cursor.execute(
            "UPDATE mobile_web_session_tickets SET consumed_at = ? WHERE id = ? AND consumed_at IS NULL",
            (to_db_time(now), row_dict["id"]),
        )
        if cursor.rowcount != 1:
            conn.commit()
            return "consumed", row_dict

        conn.commit()
        return "ok", row_dict
    except Exception:
        conn.rollback()
        raise


def insert_mobile_schoology_oauth_request(
    user_id: int,
    request_token_hash: str,
    request_token_secret_encrypted: str,
    device_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    client_state: str | None,
    expires_at: datetime,
    created_at: datetime,
):
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO mobile_schoology_oauth_requests
        (user_id, request_token_hash, request_token_secret_encrypted, device_id, redirect_uri,
         code_challenge, code_challenge_method, client_state, expires_at, consumed_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?)
        """,
        (
            user_id,
            request_token_hash,
            request_token_secret_encrypted,
            device_id,
            redirect_uri,
            code_challenge,
            code_challenge_method,
            client_state,
            to_db_time(expires_at),
            to_db_time(created_at),
        ),
    )
    conn.commit()


def consume_mobile_schoology_oauth_request(
    request_token_hash: str,
    now: datetime,
) -> tuple[str, dict | None]:
    conn = _connect()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            SELECT id, user_id, request_token_secret_encrypted, device_id, redirect_uri,
                   code_challenge, code_challenge_method, client_state, expires_at, consumed_at
            FROM mobile_schoology_oauth_requests
            WHERE request_token_hash = ?
            """,
            (request_token_hash,),
        )
        row = cursor.fetchone()
        if not row:
            conn.commit()
            return "invalid", None

        row_dict = dict(row)
        if row_dict["consumed_at"] is not None:
            conn.commit()
            return "consumed", row_dict

        expires_at = parse_db_time(row_dict["expires_at"])
        if not expires_at or expires_at <= now:
            conn.commit()
            return "expired", row_dict

        cursor.execute(
            """
            UPDATE mobile_schoology_oauth_requests
            SET consumed_at = ?
            WHERE id = ? AND consumed_at IS NULL
            """,
            (to_db_time(now), row_dict["id"]),
        )
        if cursor.rowcount != 1:
            conn.commit()
            return "consumed", row_dict

        conn.commit()
        return "ok", row_dict
    except Exception:
        conn.rollback()
        raise


def insert_mobile_notification_event(
    *,
    user_id: int,
    device_id: str | None,
    event_type: str,
    payload: dict,
    status: str,
    created_at: datetime,
    available_at: datetime,
) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO mobile_notification_events
        (user_id, device_id, event_type, payload_json, status, created_at, available_at, processed_at, last_error)
        VALUES (?, ?, ?, ?, ?, ?, ?, NULL, NULL)
        """,
        (
            user_id,
            device_id,
            event_type,
            json.dumps(payload, separators=(",", ":"), sort_keys=True),
            status,
            to_db_time(created_at),
            to_db_time(available_at),
        ),
    )
    event_id = int(cursor.lastrowid)
    conn.commit()
    return event_id


def fetch_pending_mobile_notification_events(now: datetime, limit: int = 100) -> list[dict]:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT id, user_id, device_id, event_type, payload_json, status, created_at, available_at, processed_at, last_error
        FROM mobile_notification_events
        WHERE status = 'pending' AND available_at <= ?
        ORDER BY available_at ASC, id ASC
        LIMIT ?
        """,
        (to_db_time(now), max(1, limit)),
    )
    rows = cursor.fetchall()

    results: list[dict] = []
    for row in rows:
        entry = dict(row)
        try:
            entry["payload"] = json.loads(entry.pop("payload_json"))
        except Exception:
            entry["payload"] = {}
        results.append(entry)
    return results


def mark_mobile_notification_event_processing(event_id: int, now: datetime) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE mobile_notification_events
        SET status = 'processing', last_error = NULL
        WHERE id = ? AND status = 'pending'
        """,
        (event_id,),
    )
    affected = cursor.rowcount
    conn.commit()
    return affected


def mark_mobile_notification_event_processed(event_id: int, now: datetime) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE mobile_notification_events
        SET status = 'processed', processed_at = ?, last_error = NULL
        WHERE id = ?
        """,
        (to_db_time(now), event_id),
    )
    affected = cursor.rowcount
    conn.commit()
    return affected


def mark_mobile_notification_event_failed(event_id: int, now: datetime, error_message: str) -> int:
    conn = _connect()
    cursor = conn.cursor()
    cursor.execute(
        """
        UPDATE mobile_notification_events
        SET status = 'failed', processed_at = ?, last_error = ?
        WHERE id = ?
        """,
        (to_db_time(now), error_message[:1000], event_id),
    )
    affected = cursor.rowcount
    conn.commit()
    return affected
