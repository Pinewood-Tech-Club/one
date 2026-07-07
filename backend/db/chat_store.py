"""
SQLite-backed chat store: threads, messages, generations, tool calls.

Ports the Convex chat state machine (chat.ts / chatInternal.ts / chatModel.ts)
1:1 — the same transition guards, clientRequestId idempotency, and
??-fallback-to-existing patch semantics. Multi-statement transitions run
inside BEGIN IMMEDIATE transactions (the SQLite replacement for Convex OCC).
Returns are camelCase dicts shaped like the Convex documents (ids under
"_id") so services/chat/types.py parses them unchanged. Every mutation
publishes its app events after commit; publishing never raises.
"""
import json
import secrets
import time

from config import Config
from db import app_users, schoology_cache_store
from db.pool import get_conn
from services import events

ACTIVE_STATUSES = {"queued", "streaming"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

STALE_GENERATION_MESSAGE = "Generation timed out waiting for backend progress"


class ChatStateError(RuntimeError):
    """Raised where the Convex mutations threw."""


def new_id() -> str:
    return secrets.token_hex(16)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _conn():
    return get_conn(Config.CHAT_DB_PATH)


def _thread_doc(row) -> dict:
    return {
        "_id": row["id"],
        "userId": str(row["user_id"]),
        "title": row["title"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "lastMessageAt": row["last_message_at"],
        "archivedAt": row["archived_at"],
    }


def _message_doc(row) -> dict:
    return {
        "_id": row["id"],
        "threadId": row["thread_id"],
        "userId": str(row["user_id"]),
        "role": row["role"],
        "content": row["content"],
        "status": row["status"],
        "chunkSequence": row["chunk_sequence"],
        "providerMessageId": row["provider_message_id"],
        "error": row["error"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "completedAt": row["completed_at"],
    }


def _generation_doc(row) -> dict:
    return {
        "_id": row["id"],
        "threadId": row["thread_id"],
        "userId": str(row["user_id"]),
        "userMessageId": row["user_message_id"],
        "assistantMessageId": row["assistant_message_id"],
        "clientRequestId": row["client_request_id"],
        "status": row["status"],
        "activity": row["activity"],
        "provider": row["provider"],
        "model": row["model"],
        "cancelRequested": bool(row["cancel_requested"]),
        "errorCode": row["error_code"],
        "errorMessage": row["error_message"],
        "providerMessageId": row["provider_message_id"],
        "usage": json.loads(row["usage_json"]) if row["usage_json"] else None,
        "toolTraceSummary": row["tool_trace_summary"],
        "toolTraceStats": (
            json.loads(row["tool_trace_stats_json"])
            if row["tool_trace_stats_json"]
            else None
        ),
        "createdAt": row["created_at"],
        "startedAt": row["started_at"],
        "updatedAt": row["updated_at"],
        "completedAt": row["completed_at"],
        "lastTextAt": row["last_text_at"],
    }


def _tool_call_doc(row) -> dict:
    return {
        "_id": str(row["id"]),
        "generationId": row["generation_id"],
        "threadId": row["thread_id"],
        "userId": str(row["user_id"]),
        "sequence": row["sequence"],
        "callId": row["call_id"],
        "toolName": row["tool_name"],
        "status": row["status"],
        "argumentsText": row["arguments_text"],
        "outputText": row["output_text"],
        "summaryText": row["summary_text"],
        "errorText": row["error_text"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
    }


def _thread_event(thread_doc: dict) -> dict:
    return {
        "thread": {
            "_id": thread_doc["_id"],
            "title": thread_doc["title"],
            "createdAt": thread_doc["createdAt"],
            "updatedAt": thread_doc["updatedAt"],
            "lastMessageAt": thread_doc["lastMessageAt"],
        }
    }


def _message_event(message_doc: dict) -> dict:
    return {
        "threadId": message_doc["threadId"],
        "message": {
            "_id": message_doc["_id"],
            "threadId": message_doc["threadId"],
            "role": message_doc["role"],
            "content": message_doc["content"],
            "status": message_doc["status"],
            "error": message_doc["error"],
            "createdAt": message_doc["createdAt"],
            "updatedAt": message_doc["updatedAt"],
            "completedAt": message_doc["completedAt"],
        },
    }


def _generation_event(generation_doc: dict) -> dict:
    return {
        "threadId": generation_doc["threadId"],
        "generation": {
            "_id": generation_doc["_id"],
            "threadId": generation_doc["threadId"],
            "status": generation_doc["status"],
            "activity": generation_doc["activity"],
            "cancelRequested": generation_doc["cancelRequested"],
            "createdAt": generation_doc["createdAt"],
            "startedAt": generation_doc["startedAt"],
            "updatedAt": generation_doc["updatedAt"],
            "completedAt": generation_doc["completedAt"],
            "errorCode": generation_doc["errorCode"],
            "errorMessage": generation_doc["errorMessage"],
        },
    }


def _publish(user_id: int, pending_events: list[tuple[str, dict]]) -> None:
    for event_type, payload in pending_events:
        events.publish_user_event(user_id, event_type, payload)


def _fetch_thread(executor, thread_id: str):
    return executor.execute(
        "SELECT * FROM chat_threads WHERE id = ?", (thread_id,)
    ).fetchone()


def _fetch_message(executor, message_id: str):
    return executor.execute(
        "SELECT * FROM chat_messages WHERE id = ?", (message_id,)
    ).fetchone()


def _fetch_generation(executor, generation_id: str):
    return executor.execute(
        "SELECT * FROM chat_generations WHERE id = ?", (generation_id,)
    ).fetchone()


def get_thread(thread_id: str) -> dict | None:
    row = _fetch_thread(_conn(), thread_id)
    return _thread_doc(row) if row else None


def get_owned_thread(thread_id: str, user_id: int) -> dict | None:
    row = _conn().execute(
        "SELECT * FROM chat_threads WHERE id = ? AND user_id = ?",
        (thread_id, user_id),
    ).fetchone()
    return _thread_doc(row) if row else None


def list_threads(user_id: int) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM chat_threads"
        " WHERE user_id = ? AND archived_at IS NULL"
        " ORDER BY updated_at DESC",
        (user_id,),
    ).fetchall()
    return [_thread_doc(row) for row in rows]


def list_messages(thread_id: str) -> list[dict]:
    rows = _conn().execute(
        "SELECT * FROM chat_messages WHERE thread_id = ? ORDER BY created_at ASC, id",
        (thread_id,),
    ).fetchall()
    return [_message_doc(row) for row in rows]


def get_message(message_id: str) -> dict | None:
    row = _fetch_message(_conn(), message_id)
    return _message_doc(row) if row else None


def get_generation(generation_id: str) -> dict | None:
    row = _fetch_generation(_conn(), generation_id)
    return _generation_doc(row) if row else None


def get_generation_by_client_request_id(
    user_id: int, client_request_id: str
) -> dict | None:
    row = _conn().execute(
        "SELECT * FROM chat_generations WHERE user_id = ? AND client_request_id = ?",
        (user_id, client_request_id),
    ).fetchone()
    return _generation_doc(row) if row else None


def get_active_generation_for_thread(thread_id: str) -> dict | None:
    row = _conn().execute(
        "SELECT * FROM chat_generations"
        " WHERE thread_id = ? AND status IN ('queued','streaming')"
        " ORDER BY updated_at DESC LIMIT 1",
        (thread_id,),
    ).fetchone()
    return _generation_doc(row) if row else None


def create_generation(
    user_id: int,
    *,
    thread_id: str | None,
    client_request_id: str,
    content: str,
    now_ms: int,
) -> dict:
    """The whole Convex chat.sendMessage mutation body in one transaction."""
    conn = _conn()
    cursor = conn.cursor()
    pending: list[tuple[str, dict]] = []
    try:
        cursor.execute("BEGIN IMMEDIATE")

        existing = cursor.execute(
            "SELECT * FROM chat_generations"
            " WHERE user_id = ? AND client_request_id = ?",
            (user_id, client_request_id),
        ).fetchone()
        if existing:
            conn.commit()
            return {
                "threadId": existing["thread_id"],
                "userMessageId": existing["user_message_id"],
                "assistantMessageId": existing["assistant_message_id"],
                "generationId": existing["id"],
                "createdThread": False,
            }

        created_thread = False
        if thread_id:
            thread_row = _fetch_thread(cursor, thread_id)
            if not thread_row or thread_row["user_id"] != user_id:
                conn.commit()
                raise ChatStateError("thread_not_found")
        else:
            created_thread = True
            thread_id = new_id()
            cursor.execute(
                """
                INSERT INTO chat_threads
                    (id, user_id, title, created_at, updated_at, last_message_at)
                VALUES (?, ?, 'New chat', ?, ?, ?)
                """,
                (thread_id, user_id, now_ms, now_ms, now_ms),
            )

        active = cursor.execute(
            "SELECT id FROM chat_generations"
            " WHERE thread_id = ? AND status IN ('queued','streaming') LIMIT 1",
            (thread_id,),
        ).fetchone()
        if active:
            conn.commit()
            raise ChatStateError("thread_busy")

        user_message_id = new_id()
        cursor.execute(
            """
            INSERT INTO chat_messages
                (id, thread_id, user_id, role, content, status,
                 created_at, updated_at, completed_at)
            VALUES (?, ?, ?, 'user', ?, 'completed', ?, ?, ?)
            """,
            (user_message_id, thread_id, user_id, content, now_ms, now_ms, now_ms),
        )

        assistant_message_id = new_id()
        cursor.execute(
            """
            INSERT INTO chat_messages
                (id, thread_id, user_id, role, content, status, chunk_sequence,
                 created_at, updated_at)
            VALUES (?, ?, ?, 'assistant', '', 'queued', 0, ?, ?)
            """,
            (assistant_message_id, thread_id, user_id, now_ms, now_ms),
        )

        generation_id = new_id()
        cursor.execute(
            """
            INSERT INTO chat_generations
                (id, thread_id, user_id, user_message_id, assistant_message_id,
                 client_request_id, status, provider, model, cancel_requested,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'queued', '', '', 0, ?, ?)
            """,
            (
                generation_id,
                thread_id,
                user_id,
                user_message_id,
                assistant_message_id,
                client_request_id,
                now_ms,
                now_ms,
            ),
        )

        cursor.execute(
            "UPDATE chat_threads SET updated_at = ?, last_message_at = ? WHERE id = ?",
            (now_ms, now_ms, thread_id),
        )

        thread_row = _fetch_thread(cursor, thread_id)
        user_message_row = _fetch_message(cursor, user_message_id)
        generation_row = _fetch_generation(cursor, generation_id)

        conn.commit()
    except ChatStateError:
        raise
    except Exception:
        conn.rollback()
        raise

    if created_thread:
        pending.append(("chat.thread.created", _thread_event(_thread_doc(thread_row))))
    pending.append(("chat.message.created", _message_event(_message_doc(user_message_row))))
    pending.append(
        ("chat.generation.updated", _generation_event(_generation_doc(generation_row)))
    )
    _publish(user_id, pending)

    return {
        "threadId": thread_id,
        "userMessageId": user_message_id,
        "assistantMessageId": assistant_message_id,
        "generationId": generation_id,
        "createdThread": created_thread,
    }


def get_generation_context(generation_id: str) -> dict | None:
    """chatInternal.getGenerationContext, byte-compatible payload shape."""
    conn = _conn()
    generation_row = _fetch_generation(conn, generation_id)
    if not generation_row:
        return None

    thread_row = _fetch_thread(conn, generation_row["thread_id"])
    user_message_row = _fetch_message(conn, generation_row["user_message_id"])
    assistant_message_row = _fetch_message(conn, generation_row["assistant_message_id"])
    if not thread_row or not user_message_row or not assistant_message_row:
        raise ChatStateError("Generation context is missing linked records")

    # rowid tie-break preserves insertion order at equal created_at
    # (user message before its assistant placeholder), matching Convex's
    # stable index-order sort.
    transcript_rows = conn.execute(
        "SELECT * FROM chat_messages WHERE thread_id = ?"
        " ORDER BY created_at ASC, rowid ASC",
        (generation_row["thread_id"],),
    ).fetchall()

    tool_call_rows = conn.execute(
        "SELECT * FROM chat_tool_calls WHERE thread_id = ?"
        " ORDER BY created_at ASC, sequence ASC",
        (generation_row["thread_id"],),
    ).fetchall()

    user_id = generation_row["user_id"]
    state = app_users.get_user_app_state(user_id)
    user_record = (
        {
            "userId": state["userId"],
            "onboardingStep": state["onboardingStep"],
            "schoologyConnected": state["schoologyConnected"],
        }
        if state
        else None
    )

    return {
        "generation": _generation_doc(generation_row),
        "thread": _thread_doc(thread_row),
        "userId": str(user_id),
        "userMessage": _message_doc(user_message_row),
        "assistantMessage": _message_doc(assistant_message_row),
        "transcript": [_message_doc(row) for row in transcript_rows],
        "userRecord": user_record,
        "courses": schoology_cache_store.get_courses_for_user(user_id),
        "toolCalls": [_tool_call_doc(row) for row in tool_call_rows],
    }


def is_cancel_requested(generation_id: str) -> bool:
    row = _conn().execute(
        "SELECT cancel_requested FROM chat_generations WHERE id = ?",
        (generation_id,),
    ).fetchone()
    return bool(row["cancel_requested"]) if row else False


def request_cancel(generation_id: str, user_id: int) -> dict:
    conn = _conn()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        row = _fetch_generation(cursor, generation_id)
        if not row or row["user_id"] != user_id:
            conn.commit()
            raise ChatStateError("Generation not found")

        if row["status"] in TERMINAL_STATUSES:
            conn.commit()
            return {"success": False}

        if row["cancel_requested"]:
            conn.commit()
            return {"success": True}

        cursor.execute(
            "UPDATE chat_generations SET cancel_requested = 1, updated_at = ?"
            " WHERE id = ?",
            (_now_ms(), generation_id),
        )
        updated_row = _fetch_generation(cursor, generation_id)
        conn.commit()
    except ChatStateError:
        raise
    except Exception:
        conn.rollback()
        raise

    _publish(
        user_id,
        [("chat.generation.updated", _generation_event(_generation_doc(updated_row)))],
    )
    return {"success": True}


def mark_generation_streaming(
    generation_id: str,
    started_at: int,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """Only queued→streaming is accepted (chatInternal.markGenerationStreaming)."""
    conn = _conn()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        row = _fetch_generation(cursor, generation_id)
        if not row:
            conn.commit()
            raise ChatStateError("Generation not found")

        if row["status"] != "queued":
            conn.commit()
            return {
                "accepted": False,
                "status": row["status"],
                "generation": _generation_doc(row),
            }

        new_provider = provider.strip() if provider and provider.strip() else row["provider"]
        new_model = model.strip() if model and model.strip() else row["model"]
        cursor.execute(
            """
            UPDATE chat_generations
            SET status = 'streaming', activity = 'thinking',
                started_at = COALESCE(started_at, ?), updated_at = ?,
                provider = ?, model = ?
            WHERE id = ? AND status = 'queued'
            """,
            (started_at, started_at, new_provider, new_model, generation_id),
        )
        updated_row = _fetch_generation(cursor, generation_id)
        conn.commit()
    except ChatStateError:
        raise
    except Exception:
        conn.rollback()
        raise

    generation = _generation_doc(updated_row)
    _publish(
        updated_row["user_id"],
        [("chat.generation.updated", _generation_event(generation))],
    )
    return {"accepted": True, "status": "streaming", "generation": generation}


def heartbeat_generation(
    generation_id: str,
    updated_at: int,
    *,
    last_text_at: int | None = None,
    activity: str | None = None,
) -> dict:
    conn = _conn()
    cursor = conn.cursor()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        row = _fetch_generation(cursor, generation_id)
        if not row:
            conn.commit()
            raise ChatStateError("Generation not found")
        if row["status"] in TERMINAL_STATUSES:
            conn.commit()
            raise ChatStateError(f"Cannot patch terminal generation {row['status']}")

        previous_activity = row["activity"]
        new_activity = activity or previous_activity or "thinking"
        cursor.execute(
            """
            UPDATE chat_generations
            SET status = 'streaming', activity = ?, updated_at = ?,
                last_text_at = COALESCE(?, last_text_at)
            WHERE id = ?
            """,
            (new_activity, updated_at, last_text_at, generation_id),
        )
        updated_row = _fetch_generation(cursor, generation_id)
        conn.commit()
    except ChatStateError:
        raise
    except Exception:
        conn.rollback()
        raise

    generation = _generation_doc(updated_row)
    # Heartbeats fire every ~5s; only an actual activity change is worth an event.
    if new_activity != previous_activity:
        _publish(
            updated_row["user_id"],
            [("chat.generation.updated", _generation_event(generation))],
        )
    return generation


def _finalize_generation(
    generation_id: str,
    *,
    new_status: str,
    completed_at: int,
    content: str | None,
    message_error: str | None,
    error_code: str | None,
    error_message: str | None,
    clear_error_fields: bool,
    provider_message_id: str | None,
    usage: dict | None,
    tool_trace_summary: str | None,
    tool_trace_stats: dict | None,
    bump_thread: bool,
) -> dict:
    """Shared terminal-transition body for completed/failed/cancelled.

    Terminal-state guard: any already-terminal generation is returned
    unchanged (idempotent no-op, no events)."""
    conn = _conn()
    cursor = conn.cursor()
    pending: list[tuple[str, dict]] = []
    try:
        cursor.execute("BEGIN IMMEDIATE")
        row = _fetch_generation(cursor, generation_id)
        if not row:
            conn.commit()
            raise ChatStateError("Generation not found")
        if row["status"] in TERMINAL_STATUSES:
            conn.commit()
            return _generation_doc(row)

        message_row = _fetch_message(cursor, row["assistant_message_id"])
        if not message_row:
            conn.commit()
            raise ChatStateError("Assistant message not found")

        new_content = content if content is not None else message_row["content"]
        cursor.execute(
            """
            UPDATE chat_messages
            SET content = ?, status = ?, provider_message_id = ?,
                error = ?, updated_at = ?, completed_at = ?
            WHERE id = ?
            """,
            (
                new_content,
                new_status,
                provider_message_id
                if provider_message_id is not None
                else message_row["provider_message_id"],
                message_error,
                completed_at,
                completed_at,
                message_row["id"],
            ),
        )

        cursor.execute(
            """
            UPDATE chat_generations
            SET status = ?, activity = NULL,
                provider_message_id = ?,
                usage_json = ?,
                tool_trace_summary = ?,
                tool_trace_stats_json = ?,
                error_code = ?,
                error_message = ?,
                updated_at = ?, completed_at = ?,
                last_text_at = ?
            WHERE id = ?
            """,
            (
                new_status,
                provider_message_id
                if provider_message_id is not None
                else row["provider_message_id"],
                json.dumps(usage) if usage is not None else row["usage_json"],
                tool_trace_summary
                if tool_trace_summary is not None
                else row["tool_trace_summary"],
                json.dumps(tool_trace_stats)
                if tool_trace_stats is not None
                else row["tool_trace_stats_json"],
                error_code if not clear_error_fields else None,
                error_message if not clear_error_fields else None,
                completed_at,
                completed_at,
                completed_at if content else row["last_text_at"],
                generation_id,
            ),
        )

        thread_row = None
        if bump_thread:
            cursor.execute(
                "UPDATE chat_threads SET updated_at = ?, last_message_at = ?"
                " WHERE id = ?",
                (completed_at, completed_at, row["thread_id"]),
            )
            thread_row = _fetch_thread(cursor, row["thread_id"])

        updated_generation_row = _fetch_generation(cursor, generation_id)
        updated_message_row = _fetch_message(cursor, row["assistant_message_id"])
        conn.commit()
    except ChatStateError:
        raise
    except Exception:
        conn.rollback()
        raise

    generation = _generation_doc(updated_generation_row)
    pending.append(("chat.generation.updated", _generation_event(generation)))
    pending.append(
        ("chat.message.created", _message_event(_message_doc(updated_message_row)))
    )
    if thread_row:
        pending.append(("chat.thread.updated", _thread_event(_thread_doc(thread_row))))
    _publish(updated_generation_row["user_id"], pending)
    return generation


def mark_generation_completed(
    generation_id: str,
    content: str,
    completed_at: int,
    *,
    provider_message_id: str | None = None,
    usage: dict | None = None,
    tool_trace_summary: str | None = None,
    tool_trace_stats: dict | None = None,
) -> dict:
    return _finalize_generation(
        generation_id,
        new_status="completed",
        completed_at=completed_at,
        content=content,
        message_error=None,
        error_code=None,
        error_message=None,
        clear_error_fields=True,
        provider_message_id=provider_message_id,
        usage=usage,
        tool_trace_summary=tool_trace_summary,
        tool_trace_stats=tool_trace_stats,
        bump_thread=True,
    )


def mark_generation_failed(
    generation_id: str,
    error_code: str,
    error_message: str,
    completed_at: int,
    *,
    content: str | None = None,
    tool_trace_summary: str | None = None,
    tool_trace_stats: dict | None = None,
) -> dict:
    # Convex parity: failure does NOT bump the thread.
    return _finalize_generation(
        generation_id,
        new_status="failed",
        completed_at=completed_at,
        content=content,
        message_error=error_message,
        error_code=error_code,
        error_message=error_message,
        clear_error_fields=False,
        provider_message_id=None,
        usage=None,
        tool_trace_summary=tool_trace_summary,
        tool_trace_stats=tool_trace_stats,
        bump_thread=False,
    )


def mark_generation_cancelled(
    generation_id: str,
    completed_at: int,
    *,
    content: str | None = None,
    tool_trace_summary: str | None = None,
    tool_trace_stats: dict | None = None,
) -> dict:
    return _finalize_generation(
        generation_id,
        new_status="cancelled",
        completed_at=completed_at,
        content=content,
        message_error=None,
        error_code=None,
        error_message=None,
        clear_error_fields=True,
        provider_message_id=None,
        usage=None,
        tool_trace_summary=tool_trace_summary,
        tool_trace_stats=tool_trace_stats,
        bump_thread=False,
    )


def upsert_tool_call(
    generation_id: str,
    *,
    sequence: int,
    call_id: str,
    tool_name: str,
    status: str,
    arguments_text: str | None = None,
    output_text: str | None = None,
    summary_text: str | None = None,
    error_text: str | None = None,
    started_at: int | None = None,
    completed_at: int | None = None,
) -> dict:
    """Upsert keyed on (generation_id, call_id); optional fields keep existing
    values when the new value is None (Convex `?? existing` semantics). No app
    event — tool detail rides the per-generation token SSE."""
    conn = _conn()
    cursor = conn.cursor()
    now = _now_ms()
    try:
        cursor.execute("BEGIN IMMEDIATE")
        generation_row = _fetch_generation(cursor, generation_id)
        if not generation_row:
            conn.commit()
            raise ChatStateError("Generation not found")

        cursor.execute(
            """
            INSERT INTO chat_tool_calls
                (generation_id, thread_id, user_id, sequence, call_id, tool_name,
                 status, arguments_text, output_text, summary_text, error_text,
                 started_at, completed_at, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(generation_id, call_id) DO UPDATE SET
                sequence = excluded.sequence,
                tool_name = excluded.tool_name,
                status = excluded.status,
                arguments_text = COALESCE(excluded.arguments_text, arguments_text),
                output_text = COALESCE(excluded.output_text, output_text),
                summary_text = COALESCE(excluded.summary_text, summary_text),
                error_text = COALESCE(excluded.error_text, error_text),
                started_at = COALESCE(excluded.started_at, started_at),
                completed_at = COALESCE(excluded.completed_at, completed_at),
                updated_at = excluded.updated_at
            """,
            (
                generation_id,
                generation_row["thread_id"],
                generation_row["user_id"],
                sequence,
                call_id,
                tool_name,
                status,
                arguments_text,
                output_text,
                summary_text,
                error_text,
                started_at,
                completed_at,
                now,
                now,
            ),
        )
        row = cursor.execute(
            "SELECT * FROM chat_tool_calls WHERE generation_id = ? AND call_id = ?",
            (generation_id, call_id),
        ).fetchone()
        conn.commit()
    except ChatStateError:
        raise
    except Exception:
        conn.rollback()
        raise

    return _tool_call_doc(row)


def update_thread_title(thread_id: str, title: str) -> None:
    """Trim, cap at 32 chars; empty titles and missing threads are ignored."""
    title = title.strip()[:32]
    if not title:
        return

    conn = _conn()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE chat_threads SET title = ? WHERE id = ?", (title, thread_id)
    )
    if cursor.rowcount == 0:
        conn.rollback()
        return
    row = _fetch_thread(cursor, thread_id)
    conn.commit()

    _publish(row["user_id"], [("chat.thread.updated", _thread_event(_thread_doc(row)))])


def fail_stale_generations(now_ms: int, stale_after_ms: int) -> int:
    """The reaper (Convex failStaleGenerations cron): any queued/streaming
    generation whose updated_at is older than the cutoff is failed, together
    with its assistant message. Rows with a missing assistant message are
    skipped. Also terminates any attached token-SSE stream (best-effort)."""
    cutoff = now_ms - stale_after_ms
    conn = _conn()
    cursor = conn.cursor()
    reaped_ids: list[str] = []
    try:
        cursor.execute("BEGIN IMMEDIATE")
        stale_rows = cursor.execute(
            "SELECT * FROM chat_generations"
            " WHERE status IN ('queued','streaming') AND updated_at < ?",
            (cutoff,),
        ).fetchall()

        for row in stale_rows:
            message_row = _fetch_message(cursor, row["assistant_message_id"])
            if not message_row:
                continue

            cursor.execute(
                """
                UPDATE chat_messages
                SET status = 'failed', error = ?, updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (STALE_GENERATION_MESSAGE, now_ms, now_ms, message_row["id"]),
            )
            cursor.execute(
                """
                UPDATE chat_generations
                SET status = 'failed', activity = NULL,
                    error_code = 'stale_generation', error_message = ?,
                    updated_at = ?, completed_at = ?
                WHERE id = ?
                """,
                (STALE_GENERATION_MESSAGE, now_ms, now_ms, row["id"]),
            )
            reaped_ids.append(row["id"])

        conn.commit()
    except Exception:
        conn.rollback()
        raise

    for generation_id in reaped_ids:
        generation_row = _fetch_generation(conn, generation_id)
        message_row = _fetch_message(conn, generation_row["assistant_message_id"])
        _publish(
            generation_row["user_id"],
            [
                (
                    "chat.generation.updated",
                    _generation_event(_generation_doc(generation_row)),
                ),
                ("chat.message.created", _message_event(_message_doc(message_row))),
            ],
        )
        try:
            from services.chat import live_stream

            live_stream.publish_terminal(
                generation_id,
                status="failed",
                content="",
                updated_at=now_ms,
                error_code="stale_generation",
                error_message=STALE_GENERATION_MESSAGE,
            )
        except Exception:
            pass

    return len(reaped_ids)
