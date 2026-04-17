"""
SQLite-backed state store for the shared Schoology scraper.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from typing import Any

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


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def get_scraper_conn():
    return get_conn(Config.SCRAPER_DB_PATH)


def upsert_scraper_user(
    user_id: int,
    *,
    eligible: bool,
    schoology_connected: bool,
    smart_features_enabled: bool,
    has_valid_credentials: bool,
    last_convex_check_at: datetime,
    last_sections_refresh_at: datetime | None = None,
    last_credential_error: str | None = None,
) -> None:
    conn = get_scraper_conn()
    conn.execute(
        """
        INSERT INTO scraper_users (
            user_id,
            eligible,
            schoology_connected,
            smart_features_enabled,
            has_valid_credentials,
            last_convex_check_at,
            last_sections_refresh_at,
            last_credential_error
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            eligible = excluded.eligible,
            schoology_connected = excluded.schoology_connected,
            smart_features_enabled = excluded.smart_features_enabled,
            has_valid_credentials = excluded.has_valid_credentials,
            last_convex_check_at = excluded.last_convex_check_at,
            last_sections_refresh_at = COALESCE(excluded.last_sections_refresh_at, scraper_users.last_sections_refresh_at),
            last_credential_error = excluded.last_credential_error
        """,
        (
            user_id,
            int(eligible),
            int(schoology_connected),
            int(smart_features_enabled),
            int(has_valid_credentials),
            to_db_time(last_convex_check_at),
            to_db_time(last_sections_refresh_at) if last_sections_refresh_at else None,
            last_credential_error,
        ),
    )
    conn.commit()


def mark_user_sections_refreshed(user_id: int, refreshed_at: datetime) -> None:
    conn = get_scraper_conn()
    conn.execute(
        """
        UPDATE scraper_users
        SET last_sections_refresh_at = ?, last_credential_error = NULL
        WHERE user_id = ?
        """,
        (to_db_time(refreshed_at), user_id),
    )
    conn.commit()


def list_active_eligible_users() -> list[dict]:
    conn = get_scraper_conn()
    rows = conn.execute(
        """
        SELECT user_id, last_sections_refresh_at
        FROM scraper_users
        WHERE eligible = 1 AND has_valid_credentials = 1
        ORDER BY COALESCE(last_sections_refresh_at, last_convex_check_at) DESC
        """
    ).fetchall()
    return [dict(row) for row in rows]


def refresh_user_section_memberships(user_id: int, sections: list[dict], seen_at: datetime) -> None:
    conn = get_scraper_conn()
    seen_ids: set[str] = set()
    seen_at_value = to_db_time(seen_at)

    for section in sections:
        section_id_value = section.get("id")
        if section_id_value is None:
            continue
        section_id = str(section_id_value)
        seen_ids.add(section_id)
        role = section.get("role") or section.get("type")
        conn.execute(
            """
            INSERT INTO section_memberships (user_id, section_id, role, is_active, last_seen_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(user_id, section_id) DO UPDATE SET
                role = excluded.role,
                is_active = 1,
                last_seen_at = excluded.last_seen_at
            """,
            (user_id, section_id, role, seen_at_value),
        )

        raw_json = canonical_json(section)
        raw_hash = hashlib.sha256(raw_json.encode("utf-8")).hexdigest()
        title = str(
            section.get("section_title")
            or section.get("course_title")
            or section.get("title")
            or section_id
        )
        course_title = str(section.get("course_title") or section.get("title") or title)
        conn.execute(
            """
            INSERT INTO sections (
                section_id, title, course_title, raw_json, raw_hash, last_discovered_at, deleted_at
            )
            VALUES (?, ?, ?, ?, ?, ?, NULL)
            ON CONFLICT(section_id) DO UPDATE SET
                title = excluded.title,
                course_title = excluded.course_title,
                raw_json = excluded.raw_json,
                raw_hash = excluded.raw_hash,
                last_discovered_at = excluded.last_discovered_at,
                deleted_at = NULL
            """,
            (section_id, title, course_title, raw_json, raw_hash, seen_at_value),
        )

    existing_rows = conn.execute(
        "SELECT section_id FROM section_memberships WHERE user_id = ?",
        (user_id,),
    ).fetchall()
    for row in existing_rows:
        section_id = row["section_id"]
        if section_id not in seen_ids:
            conn.execute(
                """
                UPDATE section_memberships
                SET is_active = 0
                WHERE user_id = ? AND section_id = ?
                """,
                (user_id, section_id),
            )

    conn.commit()


def choose_credential_user_for_section(section_id: str) -> int | None:
    conn = get_scraper_conn()
    row = conn.execute(
        """
        SELECT sm.user_id
        FROM section_memberships sm
        INNER JOIN scraper_users su ON su.user_id = sm.user_id
        WHERE sm.section_id = ? AND sm.is_active = 1 AND su.eligible = 1 AND su.has_valid_credentials = 1
        ORDER BY COALESCE(su.last_sections_refresh_at, su.last_convex_check_at) DESC
        LIMIT 1
        """,
        (section_id,),
    ).fetchone()
    return int(row["user_id"]) if row else None


def count_active_section_runs(now: datetime, stale_seconds: int) -> int:
    conn = get_scraper_conn()
    cutoff = to_db_time(now - timedelta(seconds=stale_seconds))
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM section_sync_runs
        WHERE status = 'running' AND heartbeat_at >= ?
        """,
        (cutoff,),
    ).fetchone()
    return int(row["count"]) if row else 0


def list_due_sections(now: datetime, sync_interval_minutes: int, stale_seconds: int, limit: int) -> list[str]:
    if limit <= 0:
        return []
    conn = get_scraper_conn()
    cutoff = to_db_time(now - timedelta(minutes=sync_interval_minutes))
    rows = conn.execute(
        """
        SELECT section_id, last_successful_sync_at
        FROM sections
        WHERE deleted_at IS NULL
          AND (last_successful_sync_at IS NULL OR last_successful_sync_at < ?)
        ORDER BY CASE WHEN last_successful_sync_at IS NULL THEN 0 ELSE 1 END,
                 last_successful_sync_at ASC
        """,
        (cutoff,),
    ).fetchall()

    due: list[str] = []
    for row in rows:
        section_id = row["section_id"]
        if choose_credential_user_for_section(section_id) is None:
            continue
        if has_fresh_running_section(section_id, now, stale_seconds):
            continue
        due.append(section_id)
        if len(due) >= limit:
            break
    return due


def has_fresh_running_section(section_id: str, now: datetime, stale_seconds: int) -> bool:
    conn = get_scraper_conn()
    row = conn.execute(
        """
        SELECT heartbeat_at
        FROM section_sync_runs
        WHERE section_id = ? AND status = 'running'
        ORDER BY id DESC
        LIMIT 1
        """,
        (section_id,),
    ).fetchone()
    if not row:
        return False
    heartbeat_at = parse_db_time(row["heartbeat_at"])
    return bool(heartbeat_at and heartbeat_at >= now - timedelta(seconds=stale_seconds))


def acquire_section_run(
    section_id: str,
    credential_user_id: int,
    owner_token: str,
    now: datetime,
    stale_seconds: int,
) -> int | None:
    conn = get_scraper_conn()
    cursor = conn.cursor()

    try:
        cursor.execute("BEGIN IMMEDIATE")
        row = cursor.execute(
            """
            SELECT id, heartbeat_at
            FROM section_sync_runs
            WHERE section_id = ? AND status = 'running'
            ORDER BY id DESC
            LIMIT 1
            """,
            (section_id,),
        ).fetchone()
        if row:
            heartbeat_at = parse_db_time(row["heartbeat_at"])
            if heartbeat_at and heartbeat_at >= now - timedelta(seconds=stale_seconds):
                conn.commit()
                return None
            cursor.execute(
                """
                UPDATE section_sync_runs
                SET status = 'failed', finished_at = ?, last_error = COALESCE(last_error, 'stale lease replaced')
                WHERE id = ?
                """,
                (to_db_time(now), row["id"]),
            )

        attempt_row = cursor.execute(
            "SELECT COALESCE(MAX(attempt_count), 0) AS max_attempt FROM section_sync_runs WHERE section_id = ?",
            (section_id,),
        ).fetchone()
        attempt_count = int(attempt_row["max_attempt"]) + 1 if attempt_row else 1
        cursor.execute(
            """
            INSERT INTO section_sync_runs (
                section_id,
                credential_user_id,
                owner_token,
                status,
                run_started_at,
                heartbeat_at,
                attempt_count
            )
            VALUES (?, ?, ?, 'running', ?, ?, ?)
            """,
            (
                section_id,
                credential_user_id,
                owner_token,
                to_db_time(now),
                to_db_time(now),
                attempt_count,
            ),
        )
        run_id = int(cursor.lastrowid)
        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise


def heartbeat_section_run(section_id: str, owner_token: str, now: datetime) -> None:
    conn = get_scraper_conn()
    conn.execute(
        """
        UPDATE section_sync_runs
        SET heartbeat_at = ?
        WHERE section_id = ? AND owner_token = ? AND status = 'running'
        """,
        (to_db_time(now), section_id, owner_token),
    )
    conn.commit()


def complete_section_run(section_id: str, owner_token: str, now: datetime) -> None:
    conn = get_scraper_conn()
    conn.execute(
        """
        UPDATE section_sync_runs
        SET status = 'completed', heartbeat_at = ?, finished_at = ?
        WHERE section_id = ? AND owner_token = ? AND status = 'running'
        """,
        (to_db_time(now), to_db_time(now), section_id, owner_token),
    )
    conn.execute(
        """
        UPDATE sections
        SET last_scraped_at = ?, last_successful_sync_at = ?
        WHERE section_id = ?
        """,
        (to_db_time(now), to_db_time(now), section_id),
    )
    conn.commit()


def fail_section_run(section_id: str, owner_token: str, now: datetime, error: str) -> None:
    conn = get_scraper_conn()
    conn.execute(
        """
        UPDATE section_sync_runs
        SET status = 'failed', heartbeat_at = ?, finished_at = ?, last_error = ?
        WHERE section_id = ? AND owner_token = ? AND status = 'running'
        """,
        (to_db_time(now), to_db_time(now), error[:1000], section_id, owner_token),
    )
    conn.commit()


def _bool_to_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(bool(value))


def upsert_resource(
    *,
    section_id: str,
    schoology_id: str,
    resource_type: str,
    title: str | None,
    description_preview: str | None,
    published: bool | None,
    available: bool | None,
    due_at: str | None,
    raw_payload: dict,
    raw_hash: str,
    attachment_manifest_hash: str,
    now: datetime,
) -> dict:
    conn = get_scraper_conn()
    raw_json = canonical_json(raw_payload)
    now_value = to_db_time(now)
    row = conn.execute(
        """
        SELECT resource_id, raw_hash, attachment_manifest_hash, deleted_at
        FROM section_resources
        WHERE section_id = ? AND resource_type = ? AND schoology_id = ?
        """,
        (section_id, resource_type, schoology_id),
    ).fetchone()
    changed = True
    if row:
        changed = (
            row["raw_hash"] != raw_hash
            or row["attachment_manifest_hash"] != attachment_manifest_hash
            or row["deleted_at"] is not None
        )
        conn.execute(
            """
            UPDATE section_resources
            SET title = ?, description_preview = ?, published = ?, available = ?,
                due_at = ?, raw_json = ?, raw_hash = ?, attachment_manifest_hash = ?,
                last_seen_at = ?, deleted_at = NULL
            WHERE resource_id = ?
            """,
            (
                title,
                description_preview,
                _bool_to_int(published),
                _bool_to_int(available),
                due_at,
                raw_json,
                raw_hash,
                attachment_manifest_hash,
                now_value,
                row["resource_id"],
            ),
        )
        resource_id = int(row["resource_id"])
    else:
        cursor = conn.execute(
            """
            INSERT INTO section_resources (
                section_id, schoology_id, resource_type, title, description_preview,
                published, available, due_at, raw_json, raw_hash,
                attachment_manifest_hash, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                section_id,
                schoology_id,
                resource_type,
                title,
                description_preview,
                _bool_to_int(published),
                _bool_to_int(available),
                due_at,
                raw_json,
                raw_hash,
                attachment_manifest_hash,
                now_value,
                now_value,
            ),
        )
        resource_id = int(cursor.lastrowid)

    conn.commit()
    return {"resource_id": resource_id, "changed": changed}


def tombstone_missing_resources(
    section_id: str,
    resource_type: str,
    seen_schoology_ids: set[str],
    now: datetime,
) -> None:
    conn = get_scraper_conn()
    if seen_schoology_ids:
        placeholders = ",".join("?" for _ in seen_schoology_ids)
        params = [to_db_time(now), section_id, resource_type, *sorted(seen_schoology_ids)]
        conn.execute(
            f"""
            UPDATE section_resources
            SET deleted_at = ?
            WHERE section_id = ? AND resource_type = ? AND deleted_at IS NULL
              AND schoology_id NOT IN ({placeholders})
            """,
            params,
        )
    else:
        conn.execute(
            """
            UPDATE section_resources
            SET deleted_at = ?
            WHERE section_id = ? AND resource_type = ? AND deleted_at IS NULL
            """,
            (to_db_time(now), section_id, resource_type),
        )
    conn.commit()


def upsert_attachment(
    *,
    canonical_key: str,
    attachment_id: str | None,
    resource_id: int,
    section_id: str,
    parent_schoology_id: str,
    parent_resource_type: str,
    attachment_kind: str,
    title: str | None,
    filename: str | None,
    url: str | None,
    mime_type: str | None,
    filesize: int | None,
    metadata_payload: dict,
    metadata_hash: str,
    now: datetime,
) -> dict:
    conn = get_scraper_conn()
    metadata_json = canonical_json(metadata_payload)
    now_value = to_db_time(now)
    row = conn.execute(
        """
        SELECT downloaded_path, metadata_hash, deleted_at
        FROM attachments
        WHERE canonical_key = ?
        """,
        (canonical_key,),
    ).fetchone()
    changed = True
    if row:
        changed = row["metadata_hash"] != metadata_hash or row["deleted_at"] is not None
        conn.execute(
            """
            UPDATE attachments
            SET attachment_id = ?, resource_id = ?, section_id = ?, parent_schoology_id = ?,
                parent_resource_type = ?, attachment_kind = ?, title = ?, filename = ?, url = ?,
                mime_type = ?, filesize = ?, metadata_json = ?, metadata_hash = ?,
                last_seen_at = ?, deleted_at = NULL
            WHERE canonical_key = ?
            """,
            (
                attachment_id,
                resource_id,
                section_id,
                parent_schoology_id,
                parent_resource_type,
                attachment_kind,
                title,
                filename,
                url,
                mime_type,
                filesize,
                metadata_json,
                metadata_hash,
                now_value,
                canonical_key,
            ),
        )
        downloaded_path = row["downloaded_path"]
    else:
        conn.execute(
            """
            INSERT INTO attachments (
                canonical_key, attachment_id, resource_id, section_id, parent_schoology_id,
                parent_resource_type, attachment_kind, title, filename, url, mime_type,
                filesize, metadata_json, metadata_hash, first_seen_at, last_seen_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                canonical_key,
                attachment_id,
                resource_id,
                section_id,
                parent_schoology_id,
                parent_resource_type,
                attachment_kind,
                title,
                filename,
                url,
                mime_type,
                filesize,
                metadata_json,
                metadata_hash,
                now_value,
                now_value,
            ),
        )
        downloaded_path = None
    conn.commit()
    return {"changed": changed, "downloaded_path": downloaded_path}


def update_attachment_download(
    canonical_key: str,
    *,
    downloaded_path: str | None,
    download_hash: str | None,
) -> None:
    conn = get_scraper_conn()
    conn.execute(
        """
        UPDATE attachments
        SET downloaded_path = ?, download_hash = ?
        WHERE canonical_key = ?
        """,
        (downloaded_path, download_hash, canonical_key),
    )
    conn.commit()


def tombstone_missing_attachments(section_id: str, seen_keys: set[str], now: datetime) -> None:
    conn = get_scraper_conn()
    if seen_keys:
        placeholders = ",".join("?" for _ in seen_keys)
        params = [to_db_time(now), section_id, *sorted(seen_keys)]
        conn.execute(
            f"""
            UPDATE attachments
            SET deleted_at = ?
            WHERE section_id = ? AND deleted_at IS NULL
              AND canonical_key NOT IN ({placeholders})
            """,
            params,
        )
    else:
        conn.execute(
            """
            UPDATE attachments
            SET deleted_at = ?
            WHERE section_id = ? AND deleted_at IS NULL
            """,
            (to_db_time(now), section_id),
        )
    conn.commit()
