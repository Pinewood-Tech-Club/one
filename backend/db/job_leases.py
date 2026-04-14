"""
Database-backed job leases for cross-process deduplication.
"""
from datetime import datetime, timedelta, timezone

from config import Config
from db.pool import get_conn


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


def acquire_schoology_refresh_lease(
    user_id: int,
    owner_token: str,
    now: datetime,
    lease_ttl_seconds: int,
) -> bool:
    conn = get_conn(Config.MAIN_DB_PATH)
    cursor = conn.cursor()

    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            """
            SELECT lease_expires_at
            FROM schoology_refresh_leases
            WHERE user_id = ?
            """,
            (user_id,),
        )
        row = cursor.fetchone()
        expires_at = parse_db_time(row[0]) if row else None
        if expires_at and expires_at > now:
            conn.commit()
            return False

        started_at = to_db_time(now)
        lease_expires_at = to_db_time(now + timedelta(seconds=lease_ttl_seconds))
        if row:
            cursor.execute(
                """
                UPDATE schoology_refresh_leases
                SET owner_token = ?, started_at = ?, lease_expires_at = ?
                WHERE user_id = ?
                """,
                (owner_token, started_at, lease_expires_at, user_id),
            )
        else:
            cursor.execute(
                """
                INSERT INTO schoology_refresh_leases
                (user_id, owner_token, started_at, lease_expires_at)
                VALUES (?, ?, ?, ?)
                """,
                (user_id, owner_token, started_at, lease_expires_at),
            )

        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def release_schoology_refresh_lease(user_id: int, owner_token: str) -> None:
    conn = get_conn(Config.MAIN_DB_PATH)
    conn.execute(
        """
        DELETE FROM schoology_refresh_leases
        WHERE user_id = ? AND owner_token = ?
        """,
        (user_id, owner_token),
    )
    conn.commit()
