"""
Store-level smoke tests for the SQLite chat/user/schoology stores.

The closest thing the backend has to a unit test for the chat state machine:
runs every store transition against throwaway temp databases and asserts the
Convex-parity semantics (idempotency, transition guards, COALESCE fallbacks,
reaper cutoffs, cache tombstones, entitlement). Redis is not required —
event publishes are captured in-process.

Usage (from backend/):
    python3 -m scripts.smoke_stores
"""
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent


def _setup_environment() -> None:
    tmp = tempfile.mkdtemp(prefix="smoke_stores_")
    os.environ["CHAT_DB_PATH"] = os.path.join(tmp, "chat.db")
    os.environ["SCRAPER_DB_PATH"] = os.path.join(tmp, "scraper.db")
    os.environ.pop("UPSTASH_REDIS_URL", None)
    sys.path.insert(0, str(BACKEND_ROOT))

    from config import Config

    Config.MAIN_DB_PATH = os.path.join(tmp, "main.db")
    Config.SESSIONS_DB_PATH = os.path.join(tmp, "api_sessions.db")
    Config.UPSTASH_REDIS_URL = None

    import logging

    logging.getLogger("services.events").setLevel(logging.CRITICAL)


class _Check:
    def __init__(self) -> None:
        self.passed = 0

    def ok(self, condition: bool, label: str) -> None:
        if not condition:
            raise AssertionError(f"FAILED: {label}")
        self.passed += 1
        print(f"  ok - {label}")


def main() -> int:
    _setup_environment()

    from config import Config
    from db.init import init_db
    from db.pool import get_conn
    from services import events

    init_db()

    captured: list[tuple[int, str, dict]] = []
    original_publish = events.publish_user_event
    events.publish_user_event = lambda user_id, event_type, payload: captured.append(
        (user_id, event_type, payload)
    )

    from db import app_users, chat_store, schoology_cache_store
    from db.chat_store import ChatStateError

    main_conn = get_conn(Config.MAIN_DB_PATH)
    main_conn.execute(
        "INSERT INTO users (google_user_id, email, name) VALUES ('g-1', 'a@b.c', 'Ada')"
    )
    main_conn.commit()
    uid = main_conn.execute("SELECT id FROM users WHERE google_user_id = 'g-1'").fetchone()[0]

    c = _Check()
    now = 1_800_000_000_000  # fixed epoch-ms base for determinism

    # --- chat: create_generation idempotency -------------------------------
    r1 = chat_store.create_generation(
        uid, thread_id=None, client_request_id="req-1", content="hello", now_ms=now
    )
    c.ok(r1["createdThread"] is True, "create_generation creates a thread")
    r2 = chat_store.create_generation(
        uid, thread_id=None, client_request_id="req-1", content="hello", now_ms=now + 5
    )
    c.ok(
        r2["generationId"] == r1["generationId"]
        and r2["threadId"] == r1["threadId"]
        and r2["userMessageId"] == r1["userMessageId"]
        and r2["assistantMessageId"] == r1["assistantMessageId"]
        and r2["createdThread"] is False,
        "same clientRequestId twice returns the same ids (idempotent)",
    )

    # --- chat: thread_busy rejection ----------------------------------------
    try:
        chat_store.create_generation(
            uid,
            thread_id=r1["threadId"],
            client_request_id="req-2",
            content="again",
            now_ms=now + 10,
        )
        c.ok(False, "thread_busy raised")
    except ChatStateError as exc:
        c.ok(str(exc) == "thread_busy", "active generation rejects new sends (thread_busy)")

    gid = r1["generationId"]

    # --- chat: queued -> streaming accepted exactly once --------------------
    s1 = chat_store.mark_generation_streaming(
        gid, now + 100, provider="openrouter", model="test-model"
    )
    c.ok(
        s1["accepted"] is True
        and s1["generation"]["status"] == "streaming"
        and s1["generation"]["activity"] == "thinking"
        and s1["generation"]["provider"] == "openrouter"
        and s1["generation"]["startedAt"] == now + 100,
        "queued->streaming accepted with provider/model/startedAt",
    )
    s2 = chat_store.mark_generation_streaming(gid, now + 200)
    c.ok(
        s2["accepted"] is False
        and s2["status"] == "streaming"
        and s2["generation"]["startedAt"] == now + 100,
        "second mark_generation_streaming rejected (accepted exactly once)",
    )

    # --- chat: heartbeat event only on activity change ----------------------
    captured.clear()
    chat_store.heartbeat_generation(gid, now + 300, activity="tool_running")
    c.ok(
        len(captured) == 1 and captured[0][1] == "chat.generation.updated",
        "heartbeat publishes chat.generation.updated when activity changes",
    )
    captured.clear()
    h2 = chat_store.heartbeat_generation(gid, now + 400, last_text_at=now + 350)
    c.ok(
        len(captured) == 0
        and h2["activity"] == "tool_running"
        and h2["lastTextAt"] == now + 350
        and h2["updatedAt"] == now + 400,
        "heartbeat without activity change publishes nothing and keeps activity",
    )

    # --- chat: upsert_tool_call COALESCE semantics ---------------------------
    chat_store.upsert_tool_call(
        gid,
        sequence=0,
        call_id="call-1",
        tool_name="search",
        status="running",
        arguments_text='{"q":"x"}',
        started_at=now + 300,
    )
    t2 = chat_store.upsert_tool_call(
        gid,
        sequence=0,
        call_id="call-1",
        tool_name="search",
        status="completed",
        output_text="result",
        completed_at=now + 500,
    )
    c.ok(
        t2["status"] == "completed"
        and t2["argumentsText"] == '{"q":"x"}'
        and t2["outputText"] == "result"
        and t2["startedAt"] == now + 300
        and t2["completedAt"] == now + 500,
        "upsert_tool_call insert-then-patch keeps COALESCE(new, old) semantics",
    )

    # --- chat: generation context shape --------------------------------------
    ctx = chat_store.get_generation_context(gid)
    c.ok(
        ctx is not None
        and ctx["userId"] == str(uid)
        and ctx["generation"]["_id"] == gid
        and ctx["thread"]["_id"] == r1["threadId"]
        and ctx["userMessage"]["role"] == "user"
        and ctx["assistantMessage"]["_id"] == r1["assistantMessageId"]
        and [m["role"] for m in ctx["transcript"]] == ["user", "assistant"]
        and len(ctx["toolCalls"]) == 1
        and ctx["userRecord"]["onboardingStep"] == "welcome",
        "get_generation_context payload shape (userId str, transcript order, toolCalls)",
    )

    # --- chat: request_cancel ------------------------------------------------
    rc = chat_store.request_cancel(gid, uid)
    c.ok(
        rc == {"success": True} and chat_store.is_cancel_requested(gid) is True,
        "request_cancel flags an active generation",
    )
    c.ok(
        chat_store.request_cancel(gid, uid) == {"success": True},
        "request_cancel is idempotent while active",
    )

    # --- chat: completion, then terminal guards ------------------------------
    done = chat_store.mark_generation_completed(
        gid,
        "final answer",
        now + 900,
        provider_message_id="pm-1",
        usage={"total_tokens": 42},
    )
    c.ok(
        done["status"] == "completed"
        and done["usage"] == {"total_tokens": 42}
        and done["completedAt"] == now + 900
        and done["errorCode"] is None,
        "mark_generation_completed patches generation",
    )
    messages = chat_store.list_messages(r1["threadId"])
    assistant = [m for m in messages if m["role"] == "assistant"][0]
    c.ok(
        assistant["content"] == "final answer"
        and assistant["status"] == "completed"
        and assistant["providerMessageId"] == "pm-1",
        "completion patches the assistant message content",
    )
    thread = chat_store.get_thread(r1["threadId"])
    c.ok(
        thread["updatedAt"] == now + 900 and thread["lastMessageAt"] == now + 900,
        "completion bumps the thread",
    )

    try:
        chat_store.heartbeat_generation(gid, now + 1000)
        c.ok(False, "heartbeat raised on terminal")
    except ChatStateError as exc:
        c.ok(
            "Cannot patch terminal generation completed" in str(exc),
            "heartbeat raises ChatStateError on terminal generation",
        )

    cancelled = chat_store.mark_generation_cancelled(gid, now + 1100)
    c.ok(
        cancelled["status"] == "completed" and cancelled["completedAt"] == now + 900,
        "completed->cancelled is a no-op returning the completed row",
    )
    c.ok(
        chat_store.request_cancel(gid, uid) == {"success": False},
        "request_cancel on a terminal generation returns success False",
    )

    # --- chat: thread title ---------------------------------------------------
    chat_store.update_thread_title(r1["threadId"], "  " + "x" * 40 + "  ")
    c.ok(
        chat_store.get_thread(r1["threadId"])["title"] == "x" * 32,
        "update_thread_title trims and caps at 32 chars",
    )

    # --- chat: reaper ---------------------------------------------------------
    wall_now = int(__import__("time").time() * 1000)
    stale = chat_store.create_generation(
        uid,
        thread_id=None,
        client_request_id="req-stale",
        content="stale",
        now_ms=wall_now - 121_000,
    )
    chat_store.mark_generation_streaming(stale["generationId"], wall_now - 121_000)
    fresh = chat_store.create_generation(
        uid,
        thread_id=None,
        client_request_id="req-fresh",
        content="fresh",
        now_ms=wall_now - 60_000,
    )
    chat_store.mark_generation_streaming(fresh["generationId"], wall_now - 60_000)

    reaped = chat_store.fail_stale_generations(wall_now, 120_000)
    stale_gen = chat_store.get_generation(stale["generationId"])
    stale_msg = chat_store.get_message(stale["assistantMessageId"])
    c.ok(
        reaped == 1
        and stale_gen["status"] == "failed"
        and stale_gen["errorCode"] == "stale_generation"
        and stale_msg["status"] == "failed"
        and stale_msg["error"] == chat_store.STALE_GENERATION_MESSAGE,
        "fail_stale_generations reaps a 121s-stale streaming row",
    )
    c.ok(
        chat_store.get_generation(fresh["generationId"])["status"] == "streaming",
        "fail_stale_generations leaves a 60s-old streaming row alone",
    )
    chat_store.mark_generation_cancelled(fresh["generationId"], wall_now)

    # --- app_users: onboarding + entitlement ----------------------------------
    state = app_users.ensure_app_state(uid)
    c.ok(
        state["onboardingStep"] == "welcome" and state["schoologyConnected"] is False,
        "ensure_app_state returns default state",
    )
    c.ok(app_users.is_chat_entitled(uid) is False, "not entitled before consent")

    app_users.update_onboarding_step(uid, "connect_lms")
    c.ok(
        app_users.get_user_app_state(uid)["onboardingStep"] == "connect_lms",
        "update_onboarding_step persists",
    )
    try:
        app_users.update_onboarding_step(uid, "bogus")
        c.ok(False, "invalid step raised")
    except ValueError:
        c.ok(True, "update_onboarding_step rejects unknown steps")

    app_users.set_schoology_connected(uid, True)
    captured.clear()
    consent = {"enabled": True, "timestamp": wall_now, "version": "1.0"}
    user = app_users.save_consent(uid, consent)
    c.ok(
        user["onboarding_step"] == "completed"
        and user["smart_features_consent"] == consent
        and app_users.is_chat_entitled(uid) is True,
        "save_consent completes onboarding and grants chat entitlement",
    )
    c.ok(
        captured and captured[-1][1] == "user.updated"
        and captured[-1][2]["onboarding_step"] == "completed",
        "save_consent publishes user.updated with the full API-user payload",
    )

    eligible = app_users.list_eligible_scraper_users()
    c.ok(
        eligible == [
            {
                "userId": str(uid),
                "schoologyConnected": True,
                "smartFeaturesConsent": consent,
                "updatedAt": eligible[0]["updatedAt"] if eligible else None,
            }
        ],
        "list_eligible_scraper_users returns the Convex-shaped row",
    )

    app_users.set_profile_picture_url(uid, "https://x/pic.png")
    c.ok(
        app_users.get_api_user(uid)["profile_picture_url"] == "https://x/pic.png",
        "set_profile_picture_url persists",
    )
    app_users.set_sidebar_collapsed(uid, True)
    c.ok(
        app_users.get_sidebar_collapsed(uid) is True,
        "sidebar preference round-trips",
    )

    # --- schoology cache: courses + tombstones --------------------------------
    courses = [
        {"id": 101, "course_title": "Math", "section_title": "Period 1", "role": "student"},
        {"id": 102, "title": "Biology"},
    ]
    c.ok(
        schoology_cache_store.update_courses(uid, courses, wall_now) == 2,
        "update_courses upserts courses and memberships",
    )
    c.ok(
        schoology_cache_store.update_courses(uid, courses[:1], wall_now + 1) == 1
        and [x["courseId"] for x in schoology_cache_store.get_courses_for_user(uid)]
        == ["101"],
        "update_courses tombstones a removed membership",
    )
    c.ok(
        schoology_cache_store.get_courses_for_user(uid)[0]["courseTitle"] == "Math",
        "get_courses_for_user applies the course_title fallback chain",
    )

    # --- schoology cache: assignments, get_upcoming merge ---------------------
    future_due = "2099-01-02 23:59"
    assignments = [
        {"id": 9001, "title": "HW 1", "due": future_due, "grade": 95, "completed": "1"},
        {"id": 9002, "title": "Old HW", "due": "2000-01-01 00:00"},
        {"grade_item_id": 9003, "title": "Quiz", "due": "2099-06-01 08:00"},
    ]
    schoology_cache_store.update_course_assignments(uid, "101", assignments, wall_now)

    expected_ms = int(
        datetime(2099, 1, 2, 23, 59, tzinfo=timezone.utc).timestamp() * 1000
    )
    c.ok(
        schoology_cache_store.parse_due_to_ms(future_due) == expected_ms,
        "parse_due_to_ms treats naive 'YYYY-MM-DD HH:MM' as UTC",
    )

    upcoming = schoology_cache_store.get_upcoming(uid, wall_now)
    c.ok(
        [a.get("id") or a.get("grade_item_id") for a in upcoming] == [9001, 9003],
        "get_upcoming excludes past-due and sorts by due ascending",
    )
    first = upcoming[0]
    c.ok(
        first["completed"] is True
        and first["grade"] == "95"
        and first["course_title"] == "Math"
        and first["section_id"] == "101"
        and first["_courseId"] == "101",
        "get_upcoming merges grade/completed from user state with course fallbacks",
    )

    # --- schoology cache: clear keeps shared rows ------------------------------
    schoology_cache_store.clear_user_cache(uid)
    shared_left = main_conn.execute(
        "SELECT COUNT(*) FROM schoology_assignments WHERE course_id = '101'"
    ).fetchone()[0]
    c.ok(
        schoology_cache_store.get_upcoming(uid, wall_now) == [] and shared_left == 3,
        "clear_user_cache removes user links but keeps shared assignment rows",
    )

    events.publish_user_event = original_publish
    print(f"\nsmoke_stores: all {c.passed} assertions passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
