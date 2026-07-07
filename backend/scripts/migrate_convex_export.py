"""One-time migration of a Convex snapshot export into SQLite.

Run this ONCE, before the first deploy of the Convex-free backend, and after
``init_db()`` has created the new tables (chat.db chat tables + main.db user
app-state columns / user_preferences). See MIGRATION_SPEC.md §6/§7 P7.

Source of truth is a Convex snapshot export produced with::

    cd frontend && npx convex export --path ./convex-export.zip

The zip contains one ``<table>/documents.jsonl`` per table (plus internal
``_tables``/``_storage`` entries which we ignore). Each JSONL line is one
document carrying its Convex ``_id``, ``_creationTime`` and the schema fields.

Usage::

    python -m scripts.migrate_convex_export /path/to/convex-export.zip [--dry-run]
    python -m scripts.migrate_convex_export export.zip --main-db /tmp/main.db --chat-db /tmp/chat.db

Per-table policy (see --help for the full rationale):

  MIGRATED
    chatThreads, chatMessages, chatGenerations, chatToolCalls  -> chat.db
    users (into new columns on main.db users), userPreferences -> main.db

  NOT MIGRATED (pure Flask-populated cache; self-heals on first login via
  POST /api/schoology/refresh and the scraper scheduler)
    schoologyCourses, schoologyCourseMemberships,
    schoologyAssignments, schoologyAssignmentUserState

The migration preserves Convex ``_id`` strings verbatim as the TEXT primary
keys of the chat tables, so old ``/chat/#<threadId>`` URLs keep working. It is
idempotent (INSERT OR REPLACE keyed on the preserved ids / natural keys) and
safe to re-run. ``userId`` strings are converted to ``int`` and matched against
main.db ``users.id``; rows whose user id is unknown are warned about and
skipped. Any generation still ``queued``/``streaming`` in the export is written
as ``failed`` with ``error_code='stale_generation'`` because it can never
resume in the new backend.
"""
import argparse
import json
import logging
import sqlite3
import time
import zipfile
from pathlib import Path

logger = logging.getLogger("migrate_convex_export")

BACKEND_ROOT = Path(__file__).resolve().parent.parent

# Cache tables are deliberately not migrated (see module docstring / --help).
SKIPPED_CACHE_TABLES = (
    "schoologyCourses",
    "schoologyCourseMemberships",
    "schoologyAssignments",
    "schoologyAssignmentUserState",
)

STALE_ERROR_MESSAGE = "Generation timed out waiting for backend progress"
ACTIVE_GENERATION_STATUSES = {"queued", "streaming"}


def _json_or_none(value):
    """Serialize a v.any()/object field to JSON TEXT, or None when absent."""
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _bool_int(value) -> int:
    return 1 if value else 0


def load_documents(zf: zipfile.ZipFile, table: str) -> list[dict]:
    """Parse ``<table>/documents.jsonl`` from the export, tolerating absence."""
    target = f"{table}/documents.jsonl"
    name = next((n for n in zf.namelist() if n == target or n.endswith("/" + target)), None)
    if name is None:
        return []
    docs: list[dict] = []
    with zf.open(name) as handle:
        for raw in handle.read().decode("utf-8").splitlines():
            line = raw.strip()
            if line:
                docs.append(json.loads(line))
    return docs


def _valid_user_ids(main_conn: sqlite3.Connection) -> set[int]:
    return {row[0] for row in main_conn.execute("SELECT id FROM users")}


def _coerce_user_id(doc: dict, valid_ids: set[int]) -> int | None:
    """Return the int user id if known, else None (caller warns + skips)."""
    raw = doc.get("userId")
    try:
        uid = int(raw)
    except (TypeError, ValueError):
        logger.warning("skipping row %s: unparseable userId %r", doc.get("_id"), raw)
        return None
    if uid not in valid_ids:
        logger.warning("skipping row %s: unknown userId %s", doc.get("_id"), uid)
        return None
    return uid


class _Counts:
    def __init__(self):
        self.migrated = 0
        self.skipped = 0
        self.rewritten = 0  # generations flipped queued/streaming -> failed


def migrate_chat_threads(zf, chat_conn, valid_ids, counts, dry_run):
    for doc in load_documents(zf, "chatThreads"):
        uid = _coerce_user_id(doc, valid_ids)
        if uid is None:
            counts.skipped += 1
            continue
        if not dry_run:
            chat_conn.execute(
                """INSERT OR REPLACE INTO chat_threads
                   (id, user_id, title, created_at, updated_at, last_message_at, archived_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc["_id"],
                    uid,
                    doc["title"],
                    doc["createdAt"],
                    doc["updatedAt"],
                    doc["lastMessageAt"],
                    doc.get("archivedAt"),
                ),
            )
        counts.migrated += 1


def migrate_chat_messages(zf, chat_conn, valid_ids, counts, dry_run):
    for doc in load_documents(zf, "chatMessages"):
        uid = _coerce_user_id(doc, valid_ids)
        if uid is None:
            counts.skipped += 1
            continue
        if not dry_run:
            chat_conn.execute(
                """INSERT OR REPLACE INTO chat_messages
                   (id, thread_id, user_id, role, content, status, chunk_sequence,
                    provider_message_id, error, created_at, updated_at, completed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc["_id"],
                    doc["threadId"],
                    uid,
                    doc["role"],
                    doc.get("content", ""),
                    doc["status"],
                    doc.get("chunkSequence"),
                    doc.get("providerMessageId"),
                    doc.get("error"),
                    doc["createdAt"],
                    doc["updatedAt"],
                    doc.get("completedAt"),
                ),
            )
        counts.migrated += 1


def migrate_chat_generations(zf, chat_conn, valid_ids, counts, dry_run):
    for doc in load_documents(zf, "chatGenerations"):
        uid = _coerce_user_id(doc, valid_ids)
        if uid is None:
            counts.skipped += 1
            continue

        status = doc["status"]
        activity = doc.get("activity")
        error_code = doc.get("errorCode")
        error_message = doc.get("errorMessage")
        completed_at = doc.get("completedAt")
        if status in ACTIVE_GENERATION_STATUSES:
            # An in-flight generation cannot resume in the new backend; retire it.
            status = "failed"
            activity = None
            error_code = "stale_generation"
            error_message = STALE_ERROR_MESSAGE
            completed_at = completed_at or doc["updatedAt"]
            counts.rewritten += 1

        if not dry_run:
            chat_conn.execute(
                """INSERT OR REPLACE INTO chat_generations
                   (id, thread_id, user_id, user_message_id, assistant_message_id,
                    client_request_id, status, activity, provider, model, cancel_requested,
                    error_code, error_message, provider_message_id, usage_json,
                    tool_trace_summary, tool_trace_stats_json, created_at, started_at,
                    updated_at, completed_at, last_text_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc["_id"],
                    doc["threadId"],
                    uid,
                    doc["userMessageId"],
                    doc["assistantMessageId"],
                    doc["clientRequestId"],
                    status,
                    activity,
                    doc.get("provider", ""),
                    doc.get("model", ""),
                    _bool_int(doc.get("cancelRequested")),
                    error_code,
                    error_message,
                    doc.get("providerMessageId"),
                    _json_or_none(doc.get("usage")),
                    doc.get("toolTraceSummary"),
                    _json_or_none(doc.get("toolTraceStats")),
                    doc["createdAt"],
                    doc.get("startedAt"),
                    doc["updatedAt"],
                    completed_at,
                    doc.get("lastTextAt"),
                ),
            )
        counts.migrated += 1


def migrate_chat_tool_calls(zf, chat_conn, valid_ids, counts, dry_run):
    for doc in load_documents(zf, "chatToolCalls"):
        uid = _coerce_user_id(doc, valid_ids)
        if uid is None:
            counts.skipped += 1
            continue
        # chat_tool_calls.id is autoincrement; INSERT OR REPLACE dedupes on the
        # UNIQUE(generation_id, call_id) constraint, matching the Convex upsert key.
        if not dry_run:
            chat_conn.execute(
                """INSERT OR REPLACE INTO chat_tool_calls
                   (generation_id, thread_id, user_id, sequence, call_id, tool_name, status,
                    arguments_text, output_text, summary_text, error_text,
                    started_at, completed_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    doc["generationId"],
                    doc["threadId"],
                    uid,
                    doc["sequence"],
                    doc["callId"],
                    doc["toolName"],
                    doc["status"],
                    doc.get("argumentsText"),
                    doc.get("outputText"),
                    doc.get("summaryText"),
                    doc.get("errorText"),
                    doc.get("startedAt"),
                    doc.get("completedAt"),
                    doc["createdAt"],
                    doc["updatedAt"],
                ),
            )
        counts.migrated += 1


def migrate_users(zf, main_conn, valid_ids, counts, dry_run):
    for doc in load_documents(zf, "users"):
        uid = _coerce_user_id(doc, valid_ids)
        if uid is None:
            counts.skipped += 1
            continue
        if not dry_run:
            main_conn.execute(
                """UPDATE users
                      SET onboarding_step = ?,
                          schoology_connected = ?,
                          smart_features_consent_json = ?,
                          profile_picture_url = ?,
                          app_state_updated_at = ?
                    WHERE id = ?""",
                (
                    doc["onboardingStep"],
                    _bool_int(doc.get("schoologyConnected")),
                    _json_or_none(doc.get("smartFeaturesConsent")),
                    doc.get("profilePictureUrl"),
                    doc.get("updatedAt"),
                    uid,
                ),
            )
        counts.migrated += 1


def migrate_user_preferences(zf, main_conn, valid_ids, counts, dry_run, now_ms):
    for doc in load_documents(zf, "userPreferences"):
        uid = _coerce_user_id(doc, valid_ids)
        if uid is None:
            counts.skipped += 1
            continue
        if not dry_run:
            main_conn.execute(
                """INSERT OR REPLACE INTO user_preferences
                   (user_id, sidebar_collapsed, updated_at)
                   VALUES (?, ?, ?)""",
                (uid, _bool_int(doc.get("sidebarCollapsed")), now_ms),
            )
        counts.migrated += 1


def run_migration(export_zip: Path, main_db: Path, chat_db: Path, dry_run: bool) -> dict:
    now_ms = int(time.time() * 1000)
    main_conn = sqlite3.connect(str(main_db))
    chat_conn = sqlite3.connect(str(chat_db))
    try:
        valid_ids = _valid_user_ids(main_conn)
        logger.info("found %d known users in %s", len(valid_ids), main_db)

        results: dict[str, _Counts] = {}

        for table, fn in (
            ("chatThreads", lambda c: migrate_chat_threads(zf, chat_conn, valid_ids, c, dry_run)),
            ("chatMessages", lambda c: migrate_chat_messages(zf, chat_conn, valid_ids, c, dry_run)),
            ("chatGenerations", lambda c: migrate_chat_generations(zf, chat_conn, valid_ids, c, dry_run)),
            ("chatToolCalls", lambda c: migrate_chat_tool_calls(zf, chat_conn, valid_ids, c, dry_run)),
            ("users", lambda c: migrate_users(zf, main_conn, valid_ids, c, dry_run)),
            ("userPreferences", lambda c: migrate_user_preferences(zf, main_conn, valid_ids, c, dry_run, now_ms)),
        ):
            with zipfile.ZipFile(export_zip) as zf:
                counts = _Counts()
                fn(counts)
                results[table] = counts

        if dry_run:
            main_conn.rollback()
            chat_conn.rollback()
        else:
            main_conn.commit()
            chat_conn.commit()
    finally:
        main_conn.close()
        chat_conn.close()

    return results


def _print_summary(results: dict, dry_run: bool) -> None:
    header = "DRY RUN — no rows written" if dry_run else "migration complete"
    logger.info("=== %s ===", header)
    for table, counts in results.items():
        extra = f", rewritten={counts.rewritten}" if counts.rewritten else ""
        logger.info(
            "%-20s migrated=%d skipped=%d%s",
            table,
            counts.migrated,
            counts.skipped,
            extra,
        )
    logger.info(
        "skipped cache tables (self-heal on first login): %s",
        ", ".join(SKIPPED_CACHE_TABLES),
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="migrate_convex_export",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("export_zip", type=Path, help="path to convex-export.zip")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="parse the export and print per-table counts without writing",
    )
    parser.add_argument(
        "--main-db",
        type=Path,
        default=BACKEND_ROOT / "main.db",
        help="path to main.db (users + user_preferences)",
    )
    parser.add_argument(
        "--chat-db",
        type=Path,
        default=BACKEND_ROOT / "chat.db",
        help="path to chat.db (chat_threads/messages/generations/tool_calls)",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if not args.export_zip.exists():
        parser.error(f"export zip not found: {args.export_zip}")

    results = run_migration(args.export_zip, args.main_db, args.chat_db, args.dry_run)
    _print_summary(results, args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
