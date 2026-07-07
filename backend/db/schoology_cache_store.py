"""
SQLite-backed Schoology cache: shared course/assignment rows plus per-user
memberships and assignment user-state.

Ports frontend/convex/schoologyCache.ts verbatim — including parseDueToMs,
the USER_STATE_KEYS subset extraction, tombstoning of unseen rows, and
mergeAssignmentRecord's fallback chains. Mutations publish schoology.updated
app events after commit.
"""
import json
import math
import time
from datetime import datetime, timezone
from typing import Any

from config import Config
from db.pool import get_conn
from services import events

USER_STATE_KEYS = (
    "completed",
    "completion_status",
    "completion_code",
    "grade",
    "grade_comment",
    "collected_only",
    "dropbox_locked",
)

# Number.MAX_SAFE_INTEGER, the getUpcoming sort sentinel for undated rows
_MAX_SAFE_INTEGER = 2**53 - 1


def _now_ms() -> int:
    return int(time.time() * 1000)


def _conn():
    return get_conn(Config.MAIN_DB_PATH)


def _js_str(value: Any) -> str:
    # JS String() coercion for the values Schoology actually sends
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    return str(value)


def parse_due_to_ms(due_raw: Any) -> int | None:
    """parseDueToMs from schoologyCache.ts, including the >1e11 sec/ms heuristic
    and the 'YYYY-MM-DD HH:MM' → 'T' ISO coercion. Naive ISO strings parse as
    UTC (Date.parse parity in Convex's runtime)."""
    if due_raw is None:
        return None

    if isinstance(due_raw, (int, float)) and not isinstance(due_raw, bool):
        if not math.isfinite(due_raw):
            return None
        return int(due_raw) if due_raw > 1e11 else int(due_raw * 1000)

    due_str = str(due_raw).strip()
    if not due_str:
        return None

    try:
        numeric = float(due_str)
    except ValueError:
        numeric = None
    if numeric is not None and math.isfinite(numeric):
        return int(numeric) if numeric > 1e11 else int(numeric * 1000)

    if "T" in due_str:
        iso_candidate = due_str
    elif " " in due_str:
        iso_candidate = due_str.replace(" ", "T", 1)
    else:
        iso_candidate = due_str

    try:
        parsed = datetime.fromisoformat(iso_candidate)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def extract_assignment_user_state(assignment: dict) -> dict:
    state_data: dict[str, Any] = {}
    for key in USER_STATE_KEYS:
        if key in assignment:
            state_data[key] = assignment[key]

    completed_raw = assignment.get("completed")
    completed: bool | None = None
    if completed_raw is not None:
        if isinstance(completed_raw, bool):
            completed = completed_raw
        elif isinstance(completed_raw, (int, float)):
            completed = completed_raw != 0
        elif isinstance(completed_raw, str):
            normalized = completed_raw.strip().lower()
            if normalized in ("1", "true"):
                completed = True
            elif normalized in ("0", "false"):
                completed = False

    return {
        "completed": completed,
        "completionStatus": (
            _js_str(assignment["completion_status"])
            if "completion_status" in assignment
            else None
        ),
        "grade": _js_str(assignment["grade"]) if "grade" in assignment else None,
        "data": state_data if state_data else None,
    }


def _assignment_id(assignment: dict) -> str:
    raw = assignment.get("id") or assignment.get("grade_item_id") or ""
    return str(raw) if raw else ""


def update_courses(user_id: int, courses: list[dict], now_ms: int) -> int:
    conn = _conn()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        existing = {
            row["course_id"]
            for row in cursor.execute(
                "SELECT course_id FROM schoology_course_memberships WHERE user_id = ?",
                (user_id,),
            ).fetchall()
        }

        seen: set[str] = set()
        for course in courses:
            raw_id = course.get("id")
            course_id = str(raw_id) if raw_id else ""
            if not course_id:
                continue
            seen.add(course_id)

            cursor.execute(
                """
                INSERT INTO schoology_courses (course_id, data_json, last_synced_at)
                VALUES (?, ?, ?)
                ON CONFLICT(course_id) DO UPDATE SET
                    data_json = excluded.data_json,
                    last_synced_at = excluded.last_synced_at
                """,
                (course_id, json.dumps(course), now_ms),
            )

            role = str(course["role"]) if "role" in course else None
            cursor.execute(
                """
                INSERT INTO schoology_course_memberships
                    (user_id, course_id, role, is_active, last_synced_at)
                VALUES (?, ?, ?, 1, ?)
                ON CONFLICT(user_id, course_id) DO UPDATE SET
                    role = excluded.role,
                    is_active = 1,
                    last_synced_at = excluded.last_synced_at
                """,
                (user_id, course_id, role, now_ms),
            )

        for course_id in existing - seen:
            cursor.execute(
                "DELETE FROM schoology_course_memberships"
                " WHERE user_id = ? AND course_id = ?",
                (user_id, course_id),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    events.publish_user_event(
        user_id, "schoology.updated", {"scope": "courses", "courseId": None}
    )
    return len(seen)


def update_course_assignments(
    user_id: int, course_id: str, assignments: list[dict], now_ms: int
) -> int:
    conn = _conn()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")

        # Shared assignment rows (upsertSharedAssignmentsForCourse)
        existing_shared = {
            row["assignment_id"]
            for row in cursor.execute(
                "SELECT assignment_id FROM schoology_assignments WHERE course_id = ?",
                (course_id,),
            ).fetchall()
        }
        seen: set[str] = set()
        for assignment in assignments:
            assignment_id = _assignment_id(assignment)
            if not assignment_id:
                continue
            seen.add(assignment_id)

            due = assignment.get("due")
            due_raw = str(due) if due is not None else None
            due_at_ms = parse_due_to_ms(due)

            cursor.execute(
                """
                INSERT INTO schoology_assignments
                    (course_id, assignment_id, due_at_ms, due_raw, data_json, last_synced_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(course_id, assignment_id) DO UPDATE SET
                    due_at_ms = excluded.due_at_ms,
                    due_raw = excluded.due_raw,
                    data_json = excluded.data_json,
                    last_synced_at = excluded.last_synced_at
                """,
                (course_id, assignment_id, due_at_ms, due_raw, json.dumps(assignment), now_ms),
            )

        for assignment_id in existing_shared - seen:
            cursor.execute(
                "DELETE FROM schoology_assignments"
                " WHERE course_id = ? AND assignment_id = ?",
                (course_id, assignment_id),
            )

        # Per-user state rows (upsertAssignmentUserStateForCourse)
        existing_state = {
            row["assignment_id"]
            for row in cursor.execute(
                "SELECT assignment_id FROM schoology_assignment_user_state"
                " WHERE user_id = ? AND course_id = ?",
                (user_id, course_id),
            ).fetchall()
        }
        seen_state: set[str] = set()
        for assignment in assignments:
            assignment_id = _assignment_id(assignment)
            if not assignment_id:
                continue
            seen_state.add(assignment_id)

            state = extract_assignment_user_state(assignment)
            completed = state["completed"]
            cursor.execute(
                """
                INSERT INTO schoology_assignment_user_state
                    (user_id, course_id, assignment_id, completed, completion_status,
                     grade, data_json, last_synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id, course_id, assignment_id) DO UPDATE SET
                    completed = excluded.completed,
                    completion_status = excluded.completion_status,
                    grade = excluded.grade,
                    data_json = excluded.data_json,
                    last_synced_at = excluded.last_synced_at
                """,
                (
                    user_id,
                    course_id,
                    assignment_id,
                    None if completed is None else int(completed),
                    state["completionStatus"],
                    state["grade"],
                    json.dumps(state["data"]) if state["data"] is not None else None,
                    now_ms,
                ),
            )

        for assignment_id in existing_state - seen_state:
            cursor.execute(
                "DELETE FROM schoology_assignment_user_state"
                " WHERE user_id = ? AND course_id = ? AND assignment_id = ?",
                (user_id, course_id, assignment_id),
            )

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    events.publish_user_event(
        user_id, "schoology.updated", {"scope": "assignments", "courseId": course_id}
    )
    return len(seen)


def clear_user_cache(user_id: int) -> None:
    """Delete the user's memberships and assignment state; shared rows stay."""
    conn = _conn()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        cursor.execute(
            "DELETE FROM schoology_course_memberships WHERE user_id = ?", (user_id,)
        )
        cursor.execute(
            "DELETE FROM schoology_assignment_user_state WHERE user_id = ?", (user_id,)
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise

    events.publish_user_event(
        user_id, "schoology.updated", {"scope": "courses", "courseId": None}
    )


def _active_course_ids(conn, user_id: int) -> list[str]:
    rows = conn.execute(
        "SELECT course_id FROM schoology_course_memberships"
        " WHERE user_id = ? AND is_active = 1 ORDER BY course_id",
        (user_id,),
    ).fetchall()
    seen: set[str] = set()
    ordered: list[str] = []
    for row in rows:
        course_id = row["course_id"]
        if course_id not in seen:
            seen.add(course_id)
            ordered.append(course_id)
    return ordered


def _course_dict(row) -> dict:
    return {
        "courseId": row["course_id"],
        "data": json.loads(row["data_json"]),
        "lastSyncedAt": row["last_synced_at"],
    }


def _assignment_dict(row) -> dict:
    return {
        "courseId": row["course_id"],
        "assignmentId": row["assignment_id"],
        "dueAtMs": row["due_at_ms"],
        "dueRaw": row["due_raw"],
        "data": json.loads(row["data_json"]),
        "lastSyncedAt": row["last_synced_at"],
    }


def _user_state_dict(row) -> dict:
    return {
        "completed": None if row["completed"] is None else bool(row["completed"]),
        "completionStatus": row["completion_status"],
        "grade": row["grade"],
        "data": json.loads(row["data_json"]) if row["data_json"] else None,
    }


def merge_assignment_record(
    assignment_row: dict, course_row: dict | None, user_state: dict | None
) -> dict:
    data = assignment_row["data"] or {}
    course_data = (course_row or {}).get("data") or {}
    last_synced = assignment_row.get("lastSyncedAt")

    merged = {
        **data,
        **((user_state or {}).get("data") or {}),
        "section_id": assignment_row["courseId"],
        "course_title": (
            data.get("course_title")
            or course_data.get("course_title")
            or course_data.get("title")
            or ""
        ),
        "section_title": data.get("section_title") or course_data.get("section_title") or "",
        "_courseId": assignment_row["courseId"],
        "_lastUpdated": last_synced if last_synced is not None else _now_ms(),
    }

    if user_state:
        if user_state.get("completed") is not None:
            merged["completed"] = user_state["completed"]
        if user_state.get("completionStatus") is not None:
            merged["completion_status"] = user_state["completionStatus"]
        if user_state.get("grade") is not None:
            merged["grade"] = user_state["grade"]

    return merged


def get_upcoming(user_id: int, now_ms: int) -> list[dict]:
    conn = _conn()
    course_ids = _active_course_ids(conn, user_id)

    course_map: dict[str, dict] = {}
    for course_id in course_ids:
        row = conn.execute(
            "SELECT * FROM schoology_courses WHERE course_id = ?", (course_id,)
        ).fetchone()
        if row:
            course_map[course_id] = _course_dict(row)

    state_map: dict[tuple[str, str], dict] = {}
    for row in conn.execute(
        "SELECT * FROM schoology_assignment_user_state WHERE user_id = ?", (user_id,)
    ).fetchall():
        state_map[(row["course_id"], row["assignment_id"])] = _user_state_dict(row)

    upcoming: list[dict] = []
    for course_id in course_ids:
        for row in conn.execute(
            "SELECT * FROM schoology_assignments"
            " WHERE course_id = ? AND due_at_ms >= ?",
            (course_id, now_ms),
        ).fetchall():
            upcoming.append(_assignment_dict(row))

    def effective_due(record: dict) -> int:
        due = record["dueAtMs"]
        if due is None:
            raw = record["dueRaw"]
            if raw is None:
                raw = (record["data"] or {}).get("due")
            due = parse_due_to_ms(raw)
        return due if due is not None else _MAX_SAFE_INTEGER

    upcoming.sort(key=effective_due)

    return [
        merge_assignment_record(
            record,
            course_map.get(record["courseId"]),
            state_map.get((record["courseId"], record["assignmentId"])),
        )
        for record in upcoming
    ]


def get_courses_for_user(user_id: int) -> list[dict]:
    """Course list for the chat generation context (chatInternal.ts:124-140)."""
    conn = _conn()
    courses: list[dict] = []
    for course_id in _active_course_ids(conn, user_id):
        row = conn.execute(
            "SELECT * FROM schoology_courses WHERE course_id = ?", (course_id,)
        ).fetchone()
        if not row:
            continue
        data = json.loads(row["data_json"]) or {}

        course_title = data.get("course_title")
        title = data.get("title")
        if isinstance(course_title, str) and course_title.strip():
            resolved_course_title = course_title.strip()
        elif isinstance(title, str) and title.strip():
            resolved_course_title = title.strip()
        else:
            resolved_course_title = course_id

        section_title = data.get("section_title")
        if isinstance(section_title, str) and section_title.strip():
            resolved_section_title = section_title.strip()
        elif isinstance(title, str) and title.strip():
            resolved_section_title = title.strip()
        else:
            resolved_section_title = None

        courses.append(
            {
                "courseId": course_id,
                "courseTitle": resolved_course_title,
                "sectionTitle": resolved_section_title,
            }
        )
    return courses
