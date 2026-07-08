# Migration Spec: Remove Convex — Flask + SQLite + SSE becomes the only backend

Status: ready for implementation.
Scope: delete Convex entirely (frontend/convex/, convex npm dep, Convex Python bridges, aud=convex JWT surface). Flask + SQLite becomes the system of record. **The app must feel exactly as live as Convex does today** — every read that updates reactively today becomes event-driven via a new app-wide per-user SSE channel; polling exists only as a degraded fallback.

Out of scope (DO NOT TOUCH): the scraper subsystem (`backend/services/scraper/`, scraper.db) except the single one-line call-site swap noted in §4.6; mobile auth JWTs (aud=mobile_api, refresh rotation, PKCE); Redis/SSE chat-token streaming internals (`services/chat/live_stream.py`, the `/api/chat/generations/<id>/events` route) — verified below that no change is needed there.

Verified facts this spec relies on (do not re-derive):
- Live chat tokens already flow worker → Redis Streams → Flask SSE (`api/routes.py:150`), never through Convex.
- `live_stream.publish_terminal` (live_stream.py:97) **already includes the full final `content`**, plus `status`, `providerMessageId`, `usage`, `errorCode`, `errorMessage` in the terminal SSE event. The SSE route already replays via `Last-Event-ID` and sends a `snapshot` event with accumulated content. **No backend SSE change is required for the completion handoff** — only the client must stop discarding streamed text on terminal.
- Convex is a Flask-populated read-replica. Frontend reactive reads: `chat.listThreads/listMessages/getActiveGeneration` (chat page), `users.getUser` (OnboardingController + AppLayout), `schoologyCache.getUpcoming` (upcoming page + carousel). Client mutations: `chat.sendMessage`, `chat.requestCancel`. `userPreferences.getSidebarCollapsed/setSidebarCollapsed` have **zero frontend consumers** (verified by grep) — carried forward minimally.
- Exactly one Convex cron: `failStaleGenerations` every 1 min (crons.ts), cutoff `CHAT_STALE_AFTER_MS` = `CHAT_STALE_AFTER_SECONDS` env (default 120) × 1000. Flask config already has `CHAT_STALE_AFTER_SECONDS = 120` (config.py:125).
- Chat entitlement (chatModel.ts:38-55): user record exists AND `onboardingStep === 'completed'` AND `smartFeaturesConsent.enabled === true`. Moves server-side into Flask.
- Today's kickoff chain: `chat.sendMessage` (Convex mutation, inserts thread/messages/generation) → `ctx.scheduler.runAfter(0, kickoffBackendGeneration)` → Convex action POSTs `/api/internal/chat/generate` with `X-Internal-Chat-Secret` → Flask spawns `python -m services.chat.worker <id>` subprocess. New chain: `POST /api/chat/messages` (Flask, session cookie) → entitlement check → SQLite inserts → init Redis live state → spawn the same worker subprocess directly. One hop instead of three.

---

## 0. Realtime architecture (the load-bearing design decision)

### 0.1 One app-wide per-user SSE event channel

New endpoint **`GET /api/events`** (session-cookie auth via `@auth_required`, `stream_with_context`, `text/event-stream`) — the same primitive as the existing chat-token SSE. Backed by **one Upstash Redis Stream per user**: key `user:{user_id}:events`. Redis Streams (not pub/sub) because the codebase already uses streams with blocking `xread` + `xrange` replay for chat tokens (`live_stream.py`), and streams give `Last-Event-ID` replay for free.

Semantics:
- Publisher: `XADD user:{id}:events` with `MAXLEN ~ 512` approximate trim; `EXPIRE` refreshed to `APP_EVENTS_TTL_SECONDS` (default 3600) on every publish.
- SSE route: if `Last-Event-ID` header present → replay via `XRANGE (last_id +` first, then blocking `XREAD` loop (block = `APP_EVENTS_SSE_HEARTBEAT_SECONDS`, default 15s; emit `: heartbeat\n\n` comment on timeout). Every event framed with `id:` so reconnects resume. Unlike the chat-token SSE, this stream never terminates server-side.
- If `UPSTASH_REDIS_URL` is unconfigured, `/api/events` returns 503 `{"error":"events_not_configured"}` and the client degrades to polling (§5.1).
- Multi-tab: each tab holds its own `/api/events` connection. That is fine at this app's scale; Redis fan-out is a per-connection independent `XREAD` cursor on the same stream (exactly how multiple chat-SSE consumers already work).
- Publishing is done **inside the store modules** (chat_store, app_users, schoology_cache_store), not at route level, so no call site can forget to emit — this closes the classic "forgot to invalidate → stale UI" hole. Publishes are wrapped in `try/except` (non-fatal, logged): a Redis blip degrades liveness, never correctness.

### 0.2 Event catalogue (exact names + payloads)

All events are JSON. SSE `event:` name = `type` below. Payloads carry the updated entity wherever cheap so the client applies without a refetch; invalidation-style otherwise. All timestamps epoch ms.

| type | payload | published by (store function) | replaces which Convex reactivity |
|---|---|---|---|
| `user.updated` | full user object, identical shape to `GET /api/user` response (§3.2) | `app_users.update_onboarding_step`, `set_schoology_connected`, `set_profile_picture_url`, `save_consent`, `ensure_app_state` (on first create) | `users.getUser` push → onboarding auto-advance, profile pic |
| `preferences.updated` | `{"sidebar_collapsed": bool}` | `app_users.set_sidebar_collapsed` | `userPreferences.getSidebarCollapsed` |
| `chat.thread.created` | `{"thread": {"_id","title","createdAt","updatedAt","lastMessageAt"}}` | `chat_store.create_generation` (when it creates a thread) | `chat.listThreads` |
| `chat.thread.updated` | same `{"thread": ...}` | `chat_store.update_thread_title`, terminal marks (lastMessageAt bump) | `chat.listThreads` (title rename ~2s after first send) |
| `chat.message.created` | `{"threadId", "message": {"_id","threadId","role","content","status","error","createdAt","updatedAt","completedAt"}}` | `chat_store.create_generation` (user message), `mark_generation_completed/failed/cancelled` (final assistant message) | `chat.listMessages` push → cross-tab message visibility + completion handoff for non-streaming tabs |
| `chat.generation.updated` | `{"threadId", "generation": {"_id","threadId","status","activity","cancelRequested","createdAt","startedAt","updatedAt","completedAt","errorCode","errorMessage"}}` | `create_generation` (queued), `mark_generation_streaming`, `heartbeat_generation` **only when `activity` actually changes** (guard: compare previous row value; do NOT publish every 5s heartbeat), `request_cancel`, all three terminal marks, `fail_stale_generations` | `chat.getActiveGeneration` push → resume-in-other-tab, cancel-button state, composer disabled state |
| `schoology.updated` | `{"scope": "courses" \| "assignments", "courseId": str \| null}` — invalidation-style (the upcoming feed is a computed merge; carrying it would be awkward) | `schoology_cache_store.update_courses`, `update_course_assignments`, `clear_user_cache` | `schoologyCache.getUpcoming` push. Client debounces refetch 500ms since a full refresh emits one event per course |

### 0.3 Client data layer

Two new frontend modules (spec in §5.1): `useAppEvents` — one module-singleton SSE reader per tab with reconnect/backoff + `Last-Event-ID`, exposing `subscribe(type, handler)` and connection-state; and `useLiveQuery` — fetch-on-mount + apply/refetch-on-event + refetch-on-focus + refetch-on-reconnect, with a slow backstop poll (60s) and a 30s degraded-mode poll when the event channel is down. **Push is primary; polls are backstops.** No TanStack Query: the app already hand-rolls SSE parsing and `fetch` everywhere, there are only ~6 query sites, and the event channel (not a cache library) is what carries the liveness requirement; a dependency + provider + cache-semantics surface buys nothing here.

---

## 1. New SQLite schema

### 1.1 Which DB file

- **`chat.db` (new file, `Config.CHAT_DB_PATH`, default `backend/chat.db`)**: the four chat tables. Justification: chat is the write-hot path (5s heartbeats per active generation, tool-call upserts, all from *separate worker subprocesses*, plus the reaper thread and Flask readers). WAL allows one writer per DB file; isolating chat churn keeps write locks away from auth-critical `main.db` (sessions/tokens/mobile). Precedent exists: `scraper.db` is already a dedicated file for exactly this reason.
- **`main.db`**: user app-state columns (merged into the existing `users` table), `user_preferences`, and the four schoology cache tables. Justification: user state is 1:1 with `users` (native FK instead of today's stringly `userId`); schoology cache writes are already serialized by the refresh lease (`schoology_refresh_leases`) so contention is a non-issue, and `get_generation_context` / `/api/user` / upcoming all join user↔cache conceptually.
- `api_sessions.db`, `scraper.db`: untouched.

User app-state as **columns on `users`**, not a satellite table: 1:1 cardinality, four small fields, `ALTER TABLE ADD COLUMN` is the idempotent migration pattern `init.py` already uses, and it makes `GET /api/user` a single-row read.

### 1.2 DDL — chat.db (new `init_chat_db()` in `backend/db/init.py`, called from `init_db()`)

All timestamps INTEGER epoch-ms (matching Convex numbers 1:1). Chat PKs are TEXT (hex `secrets.token_hex(16)`), so the migration can preserve existing Convex `_id` strings verbatim (old `/chat/#<threadId>` URLs keep working) and API payloads keep `_id` string semantics.

```sql
PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;  -- same as other DBs

CREATE TABLE IF NOT EXISTS chat_threads (
    id              TEXT PRIMARY KEY,
    user_id         INTEGER NOT NULL,           -- main.db users.id (cross-file: no FK enforcement, app-level)
    title           TEXT NOT NULL,
    created_at      INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    last_message_at INTEGER NOT NULL,
    archived_at     INTEGER                      -- NULL = not archived
);
CREATE INDEX IF NOT EXISTS idx_chat_threads_user_updated ON chat_threads (user_id, updated_at);
-- mirrors Convex by_user + by_user_updated (by_user is a prefix of by_user_updated)

CREATE TABLE IF NOT EXISTS chat_messages (
    id                  TEXT PRIMARY KEY,
    thread_id           TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    user_id             INTEGER NOT NULL,
    role                TEXT NOT NULL CHECK (role IN ('user','assistant','system')),
    content             TEXT NOT NULL DEFAULT '',
    status              TEXT NOT NULL CHECK (status IN ('queued','streaming','completed','failed','cancelled')),
    chunk_sequence      INTEGER,                 -- dead field in Convex too; kept for migration fidelity
    provider_message_id TEXT,
    error               TEXT,
    created_at          INTEGER NOT NULL,
    updated_at          INTEGER NOT NULL,
    completed_at        INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chat_messages_thread_created ON chat_messages (thread_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_user_thread   ON chat_messages (user_id, thread_id);

CREATE TABLE IF NOT EXISTS chat_generations (
    id                   TEXT PRIMARY KEY,
    thread_id            TEXT NOT NULL REFERENCES chat_threads(id) ON DELETE CASCADE,
    user_id              INTEGER NOT NULL,
    user_message_id      TEXT NOT NULL,
    assistant_message_id TEXT NOT NULL,
    client_request_id    TEXT NOT NULL,
    status               TEXT NOT NULL CHECK (status IN ('queued','streaming','completed','failed','cancelled')),
    activity             TEXT CHECK (activity IN ('thinking','streaming_text','tool_running','post_tool_reasoning')),
    provider             TEXT NOT NULL DEFAULT '',
    model                TEXT NOT NULL DEFAULT '',
    cancel_requested     INTEGER NOT NULL DEFAULT 0,
    error_code           TEXT,
    error_message        TEXT,
    provider_message_id  TEXT,
    usage_json           TEXT,                   -- v.any() → JSON TEXT
    tool_trace_summary   TEXT,
    tool_trace_stats_json TEXT,                  -- {"toolCallsCount",...} → JSON TEXT
    created_at           INTEGER NOT NULL,
    started_at           INTEGER,
    updated_at           INTEGER NOT NULL,
    completed_at         INTEGER,
    last_text_at         INTEGER
);
CREATE INDEX IF NOT EXISTS idx_chat_generations_thread_status   ON chat_generations (thread_id, status);
CREATE INDEX IF NOT EXISTS idx_chat_generations_user            ON chat_generations (user_id);
CREATE INDEX IF NOT EXISTS idx_chat_generations_status_updated  ON chat_generations (status, updated_at);
CREATE INDEX IF NOT EXISTS idx_chat_generations_assistant_msg   ON chat_generations (assistant_message_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_chat_generations_user_request ON chat_generations (user_id, client_request_id);
-- UNIQUE strengthens Convex's by_user_request first()-based idempotency into a real constraint

CREATE TABLE IF NOT EXISTS chat_tool_calls (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    generation_id  TEXT NOT NULL REFERENCES chat_generations(id) ON DELETE CASCADE,
    thread_id      TEXT NOT NULL,
    user_id        INTEGER NOT NULL,
    sequence       INTEGER NOT NULL,
    call_id        TEXT NOT NULL,
    tool_name      TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('pending','running','completed','failed')),
    arguments_text TEXT,
    output_text    TEXT,
    summary_text   TEXT,
    error_text     TEXT,
    started_at     INTEGER,
    completed_at   INTEGER,
    created_at     INTEGER NOT NULL,
    updated_at     INTEGER NOT NULL,
    UNIQUE (generation_id, call_id)              -- mirrors by_generation_call upsert key
);
CREATE INDEX IF NOT EXISTS idx_chat_tool_calls_generation_seq  ON chat_tool_calls (generation_id, sequence);
CREATE INDEX IF NOT EXISTS idx_chat_tool_calls_thread_created  ON chat_tool_calls (thread_id, created_at);
```

### 1.3 DDL — main.db additions (in `init_main_db()`)

```sql
-- users table: idempotent column adds (same PRAGMA table_info pattern as migrate_schoology_tokens)
ALTER TABLE users ADD COLUMN onboarding_step TEXT NOT NULL DEFAULT 'welcome';
        -- values: welcome | connect_lms | smart_consent | completed
ALTER TABLE users ADD COLUMN schoology_connected INTEGER NOT NULL DEFAULT 0;
ALTER TABLE users ADD COLUMN smart_features_consent_json TEXT;   -- {"enabled":bool,"timestamp":ms,"version":str} or NULL
ALTER TABLE users ADD COLUMN profile_picture_url TEXT;
ALTER TABLE users ADD COLUMN app_state_updated_at INTEGER;       -- ms; replaces Convex users.updatedAt

CREATE TABLE IF NOT EXISTS user_preferences (
    user_id           INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    sidebar_collapsed INTEGER NOT NULL DEFAULT 0,
    updated_at        INTEGER NOT NULL
);

-- Schoology cache (normalized, mirrors schema.ts). Natural composite PKs replace Convex row _ids;
-- the diff/tombstone upserts key on exactly these tuples.
CREATE TABLE IF NOT EXISTS schoology_courses (
    course_id      TEXT PRIMARY KEY,             -- Schoology section ID; PK also replaces by_course index + dedupe logic
    data_json      TEXT NOT NULL,                -- full section object (v.any())
    last_synced_at INTEGER
);

CREATE TABLE IF NOT EXISTS schoology_course_memberships (
    user_id        INTEGER NOT NULL,
    course_id      TEXT NOT NULL,
    role           TEXT,
    is_active      INTEGER NOT NULL DEFAULT 1,
    last_synced_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, course_id)             -- mirrors by_user_and_course
);
CREATE INDEX IF NOT EXISTS idx_sch_memberships_course ON schoology_course_memberships (course_id);
-- by_user is the PK prefix

CREATE TABLE IF NOT EXISTS schoology_assignments (
    course_id      TEXT NOT NULL,
    assignment_id  TEXT NOT NULL,
    due_at_ms      INTEGER,
    due_raw        TEXT,
    data_json      TEXT NOT NULL,                -- full assignment object (v.any())
    last_synced_at INTEGER,
    PRIMARY KEY (course_id, assignment_id)       -- mirrors by_course_and_assignment; by_course = prefix
);
CREATE INDEX IF NOT EXISTS idx_sch_assignments_course_due ON schoology_assignments (course_id, due_at_ms);

CREATE TABLE IF NOT EXISTS schoology_assignment_user_state (
    user_id        INTEGER NOT NULL,
    course_id      TEXT NOT NULL,
    assignment_id  TEXT NOT NULL,
    completed      INTEGER,                      -- NULL = unknown (Convex optional boolean)
    completion_status TEXT,
    grade          TEXT,
    data_json      TEXT,                         -- USER_STATE_KEYS subset object or NULL
    last_synced_at INTEGER NOT NULL,
    PRIMARY KEY (user_id, course_id, assignment_id)
);
CREATE INDEX IF NOT EXISTS idx_sch_aus_user_assignment ON schoology_assignment_user_state (user_id, assignment_id);
-- by_user = PK prefix; by_user_and_course = PK prefix
```

### 1.4 Python store modules

All stores use `db.pool.get_conn` (existing thread-local pool). State transitions use `BEGIN IMMEDIATE` transactions (pattern already proven in `db/job_leases.py` / scraper store) — this is the SQLite replacement for Convex OCC. Every mutating function publishes its §0.2 event *after* commit.

**`backend/db/chat_store.py`** (new) — mirrors chat.ts + chatInternal.ts + chatModel.ts. Signatures (all `*_at` ms ints; returns are camelCase dicts shaped like the Convex documents with `_id`, so `services/chat/types.py` parses unchanged):

```python
class ChatStateError(RuntimeError): ...   # raised where Convex threw

ACTIVE_STATUSES = {"queued", "streaming"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

def new_id() -> str                                   # secrets.token_hex(16)
def get_thread(thread_id: str) -> dict | None
def get_owned_thread(thread_id: str, user_id: int) -> dict | None
def list_threads(user_id: int) -> list[dict]          # archived_at IS NULL, ORDER BY updated_at DESC
def list_messages(thread_id: str) -> list[dict]       # ORDER BY created_at ASC, id
def get_message(message_id: str) -> dict | None
def get_generation(generation_id: str) -> dict | None
def get_generation_by_client_request_id(user_id: int, client_request_id: str) -> dict | None
def get_active_generation_for_thread(thread_id: str) -> dict | None
    # SELECT ... WHERE thread_id=? AND status IN ('queued','streaming') ORDER BY updated_at DESC LIMIT 1

def create_generation(user_id: int, *, thread_id: str | None, client_request_id: str,
                      content: str, now_ms: int) -> dict
    # The whole sendMessage mutation body in ONE BEGIN IMMEDIATE txn:
    #  1. idempotency: return existing generation for (user_id, client_request_id) if present
    #  2. thread: validate ownership, or insert new thread (title 'New chat' — buildUntitledThreadTitle)
    #  3. reject if get_active_generation_for_thread → ChatStateError("thread_busy")
    #  4. insert user message (status completed), assistant message (status queued, content '', chunk_sequence 0)
    #  5. insert generation (status queued, provider='', model='', cancel_requested=0)
    #  6. patch thread updated_at/last_message_at
    # returns {"threadId","userMessageId","assistantMessageId","generationId","createdThread"}
    # events after commit: chat.thread.created (if created), chat.message.created (user msg),
    #                      chat.generation.updated (queued)

def get_generation_context(generation_id: str) -> dict | None
    # Reassembles chatInternal.getGenerationContext EXACTLY:
    # {"generation": {...}, "thread": {...}, "userId": str(user_id), "userMessage": {...},
    #  "assistantMessage": {...}, "transcript": [messages by created_at],
    #  "userRecord": {"userId","onboardingStep","schoologyConnected"} | None   (from app_users),
    #  "courses": [{"courseId","courseTitle","sectionTitle"}]  (active memberships joined to
    #      schoology_courses; title fallbacks: course_title → title → courseId, section fallback
    #      section_title → title → None — copy chatInternal.ts:124-140 verbatim),
    #  "toolCalls": [by created_at, sequence]}
    # camelCase keys; ids under "_id". Reads main.db via app_users + schoology_cache_store.

def is_cancel_requested(generation_id: str) -> bool
def request_cancel(generation_id: str, user_id: int) -> dict
    # ownership check; {"success": False} if terminal; idempotent if already requested;
    # else set cancel_requested=1, updated_at=now. event: chat.generation.updated

def mark_generation_streaming(generation_id: str, started_at: int, *, provider=None, model=None) -> dict
    # GUARD (chatInternal.ts:174-218): only queued→streaming.
    # BEGIN IMMEDIATE; UPDATE ... SET status='streaming', activity='thinking',
    #   started_at=COALESCE(started_at, ?), updated_at=?, provider=<trimmed or keep>, model=<...>
    #   WHERE id=? AND status='queued'
    # rowcount==0 → {"accepted": False, "status": current, "generation": row} (no event)
    # rowcount==1 → {"accepted": True, "status": "streaming", "generation": row}; event chat.generation.updated

def heartbeat_generation(generation_id: str, updated_at: int, *, last_text_at=None, activity=None) -> dict
    # GUARD (chatInternal.ts:220-253): raise ChatStateError(f"Cannot patch terminal generation {status}")
    # if status in TERMINAL. Else UPDATE status='streaming', activity=COALESCE(new, old, 'thinking'),
    # updated_at, last_text_at=COALESCE(new, old). Missing row → ChatStateError("Generation not found").
    # event chat.generation.updated ONLY if activity changed vs previous row value (no 5s spam).

def mark_generation_completed(generation_id: str, content: str, completed_at: int, *,
                              provider_message_id=None, usage=None,
                              tool_trace_summary=None, tool_trace_stats=None) -> dict
    # GUARD (chatInternal.ts:255-323): if status already terminal → return row unchanged (idempotent no-op).
    # Else in one txn: patch assistant message (content, status='completed', provider_message_id
    # fallback-to-existing, error=NULL, updated/completed_at) + patch generation (status='completed',
    # activity=NULL, usage_json, trace fields with ?? fallback-to-existing, error fields NULL,
    # last_text_at=completed_at if content else keep) + bump thread updated_at/last_message_at.
    # events: chat.generation.updated (completed) + chat.message.created (final assistant message)
    #         + chat.thread.updated

def mark_generation_failed(generation_id: str, error_code: str, error_message: str, completed_at: int, *,
                           content=None, tool_trace_summary=None, tool_trace_stats=None) -> dict
    # GUARD: no-op returning row if already terminal. Assistant message: content = content ?? existing,
    # status='failed', error=error_message. Generation: status='failed', error_code/message set,
    # activity=NULL. NOTE: does NOT bump the thread (Convex parity). Same events as completed
    # (message event carries status='failed').

def mark_generation_cancelled(generation_id: str, completed_at: int, *, content=None,
                              tool_trace_summary=None, tool_trace_stats=None) -> dict
    # GUARD: no-op if already terminal. Message status='cancelled', error=NULL. Same event pattern.

def upsert_tool_call(generation_id: str, *, sequence: int, call_id: str, tool_name: str, status: str,
                     arguments_text=None, output_text=None, summary_text=None, error_text=None,
                     started_at=None, completed_at=None) -> dict
    # INSERT ... ON CONFLICT(generation_id, call_id) DO UPDATE with COALESCE(new, old) for the
    # optional text/time fields (Convex `?? existing` semantics); sequence/tool_name/status always
    # overwritten; created_at/updated_at = now on insert, updated_at = now on update.
    # thread_id/user_id denormalized from the generation row. No app event (tool detail rides the
    # per-generation token SSE, as today).

def update_thread_title(thread_id: str, title: str) -> None
    # trim, slice to 32 chars, ignore empty / missing thread (chatInternal.ts:589-601).
    # event chat.thread.updated

def fail_stale_generations(now_ms: int, stale_after_ms: int) -> int
    # Reaper (chatInternal.ts:542-587): SELECT id FROM chat_generations
    #   WHERE status IN ('queued','streaming') AND updated_at < now_ms - stale_after_ms
    # For each: patch assistant message (status='failed',
    #   error='Generation timed out waiting for backend progress', updated/completed_at=now)
    #   and generation (status='failed', activity=NULL, error_code='stale_generation',
    #   error_message=same string, updated/completed_at=now). Skip rows whose assistant message is
    #   missing. Returns count. Events per reaped row: chat.generation.updated + chat.message.created.
    # IMPROVEMENT over Convex (allowed): also call live_stream.publish_terminal(generation_id,
    #   status='failed', content='', error_code='stale_generation', ...) best-effort so an attached
    #   SSE client terminates instead of hanging.
```

Heartbeat semantics preserved end-to-end: the worker heartbeats every `CHAT_HEARTBEAT_MS` (renamed from `CHAT_CONVEX_HEARTBEAT_MS`, default 5000; read both env names) via `heartbeat_generation`, which keeps `updated_at` fresh; the reaper kills anything whose `updated_at` is older than `CHAT_STALE_AFTER_SECONDS * 1000` (default **120_000 ms**, matching Convex `CHAT_STALE_AFTER_MS`).

**`backend/db/app_users.py`** (new) — replaces `frontend/convex/users.ts` + userPreferences.ts + `onboarding/convex_sync.py`:

```python
def ensure_app_state(user_id: int) -> dict            # getOrCreate: users row already exists from Google
                                                      # login; defaults are column defaults; returns state;
                                                      # publishes user.updated only on first-touch (no-op otherwise)
def get_user_app_state(user_id: int) -> dict | None   # {"userId": str(id), "onboardingStep",
                                                      #  "schoologyConnected": bool, "smartFeaturesConsent":
                                                      #  dict|None, "profilePictureUrl": str|None,
                                                      #  "updatedAt": int|None}
def update_onboarding_step(user_id: int, step: str) -> dict     # validates step ∈ 4 literals; raises on unknown user
def set_schoology_connected(user_id: int, connected: bool) -> dict
def set_profile_picture_url(user_id: int, url: str) -> dict
def save_consent(user_id: int, consent: dict) -> dict # sets consent json AND onboarding_step='completed' (users.ts:198)
def is_chat_entitled(user_id: int) -> bool            # chatModel.ts logic: row exists, step=='completed',
                                                      # consent.enabled is True
def list_eligible_scraper_users() -> list[dict]       # schoology_connected=1 AND consent enabled, ORDER BY
                                                      # app_state_updated_at DESC; rows shaped like the Convex
                                                      # return: {"userId": str, "schoologyConnected",
                                                      # "smartFeaturesConsent", "updatedAt"}
def get_sidebar_collapsed(user_id: int) -> bool
def set_sidebar_collapsed(user_id: int, collapsed: bool) -> None
```
All mutators set `app_state_updated_at = now_ms` and publish `user.updated` (or `preferences.updated`) with the full fresh state.

**`backend/db/schoology_cache_store.py`** (new) — ports `frontend/convex/schoologyCache.ts` logic verbatim:

```python
def parse_due_to_ms(due_raw) -> int | None            # copy parseDueToMs incl. >1e11 sec/ms heuristic and
                                                      # 'YYYY-MM-DD HH:MM' → 'T' ISO coercion (drop the no-op
                                                      # Z-ternary at line 43; Date.parse of a naive ISO string
                                                      # in Convex's runtime is UTC — use
                                                      # datetime.fromisoformat(...).replace(tzinfo=utc) parity)
def extract_assignment_user_state(assignment: dict) -> dict   # USER_STATE_KEYS subset + completed coercion
def update_courses(user_id: int, courses: list[dict], now_ms: int) -> int
    # One txn, mirrors updateCourses: upsert schoology_courses by course_id (str(course['id'])),
    # upsert membership (role=str(course['role']) if present, is_active=1), delete memberships for
    # course_ids not seen. event: schoology.updated {"scope":"courses"}
def update_course_assignments(user_id: int, course_id: str, assignments: list[dict], now_ms: int) -> int
    # mirrors updateCourseAssignments = upsertSharedAssignmentsForCourse (upsert by (course_id,
    # assignment_id) where assignment_id = str(a.get('id') or a.get('grade_item_id') or '') — skip empty;
    # delete unseen) + upsertAssignmentUserStateForCourse (same for the user-state table).
    # event: schoology.updated {"scope":"assignments","courseId":course_id}
def clear_user_cache(user_id: int) -> None            # delete memberships + user state; keep shared rows
                                                      # (clearCache). event: schoology.updated {"scope":"courses"}
def get_upcoming(user_id: int, now_ms: int) -> list[dict]
    # mirrors getUpcoming: active membership course_ids → assignments WHERE course_id=? AND
    # due_at_ms >= now (index idx_sch_assignments_course_due) → sort by effective due asc →
    # merge_assignment_record per row.
def merge_assignment_record(assignment_row, course_row, user_state) -> dict
    # copy mergeAssignmentRecord: {**data, **(state.data or {}), section_id, course_title fallback
    # chain, section_title fallback chain, _courseId, _lastUpdated, completed/completion_status/grade
    # overrides}
def get_courses_for_user(user_id: int) -> list[{"courseId","courseTitle","sectionTitle"}]
    # helper for chat_store.get_generation_context
```
`getCourses` / `getAssignments` / `getAssignmentsByCourse` Convex queries have no frontend consumers — do not port their REST endpoints; `get_upcoming` and `get_courses_for_user` cover all real readers.

**`backend/services/events.py`** (new) — the publisher + stream reader for §0:

```python
def publish_user_event(user_id: int, event_type: str, payload: dict) -> None   # XADD + MAXLEN~512 + EXPIRE; never raises
def replay_events_after(user_id: int, last_event_id: str) -> list[dict]
def block_for_new_events(user_id: int, last_event_id: str, *, block_ms: int) -> list[dict]
def events_configured() -> bool
```
Same lazy singleton Redis client pattern as `live_stream.py` (may share it via a small internal helper, but do not modify live_stream.py).

---

## 2. Chat streaming / handoff changes

- **Token SSE unchanged.** `GET /api/chat/generations/<id>/events` (api/routes.py:150-195) and `services/chat/live_stream.py` stay byte-for-byte as-is. The terminal event already carries `content`, `status`, `providerMessageId`, `usage`, `errorCode`, `errorMessage` (verified, live_stream.py:97-124), and the route already ends the stream after `terminal`.
- **Completion handoff:** the client keeps the streamed text. New `onTerminal` behavior (§5.3): construct the final assistant message locally from `terminal.content` + `terminal.status`, append it to local messages, clear `streaming`, then background-refetch `GET .../messages` to reconcile real `_id`s. No reactive round-trip, zero flicker.
- **Resume-after-refresh / resume-in-other-tab** replaces `getActiveGeneration`:
  1. On mount / thread select: `GET /api/chat/threads/<id>/active-generation`. If non-null → set `streaming = {content:'', generationId, status, activity}` and open the token SSE. The SSE `snapshot` event (already emitted) delivers all content so far.
  2. Live: `chat.generation.updated` events on the app channel flip streaming on/off and sync `cancelRequested` across tabs near-instantly.
  3. The rewritten token-SSE client tracks the last `id:` it saw and, on transport error, retries once with `Last-Event-ID: <id>` (the route already replays via `replay_events_after`); if that fails, it falls back to re-fetching active-generation + reopening with snapshot.
- **Worker → store:** `services/chat/service.py` / `worker.py` swap every `convex_sync.X(...)` call for the `chat_store` equivalent (exact map in §4.1). The double-write (Redis + Convex-HTTP) becomes Redis + local-SQLite — same call sites, cheaper writes. `types.GenerationContext.from_convex` keeps working because `chat_store.get_generation_context` emits the identical payload shape; rename the classmethod to `from_payload` (mechanical).

---

## 3. REST API contract

All endpoints `@auth_required` (existing session cookie; the decorator injects `user` with `id`, `email`, `name`, `created_at`, `last_login`). All request/response bodies JSON. Errors: `{"error": "<code>"}` with appropriate status. New chat endpoints live in a new blueprint **`backend/chat/routes.py`** (`chat_api_bp`, url_prefix `/api/chat`) so file ownership stays disjoint from `api/routes.py`.

### 3.1 Events channel

| | |
|---|---|
| `GET /api/events` | SSE per §0.1. Emits `id:`/`event:`/`data:` frames using the exact `_format_sse` helper pattern. 503 `events_not_configured` if Redis absent. |

### 3.2 User / onboarding / preferences (owned by `api/routes.py`)

**`GET /api/user`** — rewritten to read SQLite (no Convex call). Response (superset of today; existing consumers `LayoutWrapper`, `AppLayout`, `RootPage` read `name`, `email`, `onboarding_step`):
```json
{
  "user_id": 1, "email": "...", "name": "...",
  "created_at": "...", "last_login": "...",
  "onboarding_step": "welcome|connect_lms|smart_consent|completed",
  "schoology_connected": false,
  "profile_picture_url": "https://... or null",
  "smart_features_consent": {"enabled": true, "timestamp": 1234, "version": "1.0"}  // or null
}
```
This exact object is also the `user.updated` event payload and the `"user"` field of the mutation responses below.

**`POST /api/user/onboarding/start`** → `app_users.update_onboarding_step(id, "connect_lms")`. Response: `{"success": true, "step": "connect_lms", "user": {…full §3.2 object…}}`.

**`POST /api/user/consent`** body `{"enabled": bool, "version": "1.0"}` → builds consent `{enabled, timestamp: now_ms, version}` → `app_users.save_consent`. Response `{"success": true, "step": "completed", "consent": {...}, "user": {…}}`.

**`GET /api/user/preferences`** → `{"sidebar_collapsed": false}`. **`PUT /api/user/preferences`** body `{"sidebar_collapsed": bool}` → `{"success": true, "sidebar_collapsed": bool}`. (No current frontend consumer — verified; implement anyway, it is ~15 lines and preserves the migrated data's usefulness.)

### 3.3 Chat (owned by `backend/chat/routes.py`)

Entitlement: every endpoint below first checks `app_users.is_chat_entitled(user["id"])`; failure → **403 `{"error": "chat_not_entitled"}`** (replaces Convex `listThreads → null` / thrown errors; client maps 403 to the not-entitled screen).

**`GET /api/chat/threads`** → `{"threads": [{"_id","title","createdAt","updatedAt","lastMessageAt"}, …]}` (non-archived, updated_at desc — parity with listThreads).

**`GET /api/chat/threads/<thread_id>/messages`** → 404 `thread_not_found` if not owned; else `{"messages": [{"_id","threadId","role","content","status","error","createdAt","updatedAt","completedAt"}, …]}` ordered by createdAt (the Convex query returned index order; frontend keys on `_id`, reads `role`, `content`, `status`).

**`GET /api/chat/threads/<thread_id>/active-generation`** → `{"generation": null}` or `{"generation": {"_id","threadId","status","activity","cancelRequested","createdAt","startedAt","updatedAt"}}`.

**`POST /api/chat/messages`** body `{"threadId": "…"|null, "clientRequestId": "uuid", "content": "…"}`. Flow (replaces sendMessage → scheduler.runAfter → kickoffBackendGeneration → POST /api/internal/chat/generate → subprocess):
1. Validate non-empty trimmed `clientRequestId`/`content` (400 `invalid_request`).
2. Entitlement check (403) — the chatModel.ts gate, now server-side.
3. `chat_store.create_generation(...)` — idempotent replay returns the existing ids with `"createdThread": false` and does **not** respawn a worker; `thread_busy` ChatStateError → 409 `{"error": "thread_busy"}`.
4. Launch (extracted from `internal_chat/routes.py` into **`services/chat/launcher.py`**): dedupe via the in-memory `_active_generations` set/lock, `live_stream.initialize_live_state(generation_id, status="queued", content="", provider="pending", model="pending", user_id=str(user["id"]))`, `subprocess.Popen([sys.executable, "-m", "services.chat.worker", generation_id], cwd=BACKEND_ROOT)`, watcher thread. Launch failure → mark generation failed (`error_code="spawn_failed"`) and return 500.
5. Response 200: `{"threadId","userMessageId","assistantMessageId","generationId","createdThread"}` — same keys the mutation returned.

**`POST /api/chat/generations/<generation_id>/cancel`** → `chat_store.request_cancel` → `{"success": true|false}` (false when already terminal; 404 if not owned — parity with requestCancel).

### 3.4 Schoology (owned by `schoology/routes.py`, mostly existing)

- `GET /api/schoology/upcoming` (**new**) → `{"assignments": [merged records from schoology_cache_store.get_upcoming]}` — replaces `schoologyCache.getUpcoming`. Merged record fields consumed by the carousel: `id`, `title`, `due`, `course_title`, `section_title`, `description` (all present in the merged `data` + fallbacks).
- `POST /api/schoology/refresh`, `/api/schoology/status`, OAuth start/callback, developer-override: unchanged routes; their Convex sync calls swap per §4. **`POST /api/schoology/developer-override`** success response additionally returns `"user": {…§3.2 object…}` so the onboarding step advances instantly from the response (the `user.updated` event also fires).

### 3.5 Deleted routes

- `GET /api/convex-token` (api/routes.py:52) and `GET /api/.well-known/jwks.json` (api/routes.py:61) — JWKS was consumed only by Convex `auth.config.ts` (mobile tokens are verified locally with the public key, not via JWKS).
- `GET /mobile/convex/token` (mobile/routes.py:250) + `services/mobile/service.py.create_mobile_convex_token`.
- The whole `backend/internal_chat/` blueprint (`/api/internal/chat/generate`) — its spawn logic moves to `services/chat/launcher.py`.

---

## 4. Backend write-path swap (every call site)

### 4.1 `services/chat/service.py` + `worker.py` (import `from db import chat_store` replacing `from . import convex_sync`)

| today (convex_sync.*) | replacement (chat_store.*) |
|---|---|
| `get_generation_context(gid)` (service.py:113) | `get_generation_context(gid)` — same payload |
| `is_generation_cancel_requested(gid)` (182, 283) | `is_cancel_requested(gid)` |
| `mark_generation_streaming(gid, ts, provider=, model=)` (206) | same name/kwargs; same `{"accepted": …}` return contract |
| `heartbeat_generation(gid, ts, last_text_at=, activity=)` (271) | same; ChatStateError on terminal is caught by the background worker's generic `except` and logged (parity with today's HTTP-error path) |
| `mark_generation_completed(...)` (602) | same signature |
| `mark_generation_failed(...)` (306, 653; worker.py:22) | same signature |
| `mark_generation_cancelled(...)` (185) | same signature |
| `upsert_tool_call(...)` (374, 430, 485, 527, 565) | same signature |
| `update_thread_title(tid, title)` (102) | same |
| `update_generation_tool_trace_summary` | **do not port** — dead (never called; service.py uses `_ToolTraceAccumulator` inline) |

Other service.py edits: `_validate_chat_configuration` drops the `CHAT_INTERNAL_SECRET` requirement (keep LLM + Redis checks); `Config.CHAT_CONVEX_HEARTBEAT_MS` → `Config.CHAT_HEARTBEAT_MS`. `types.py`: rename `from_convex` → `from_payload`; docstring update; drop the word Convex from `ChatContractError` docstring. Worker.py: swap `convex_sync` import.

### 4.2 `services/schoology/client.py` + `runtime.py`

- client.py:263 `sync_courses(self.convex_url, self.user_id, courses)` → `schoology_cache_store.update_courses(int(self.user_id), courses, now_ms)`
- client.py:296 & 740 `sync_course_assignments(...)` → `schoology_cache_store.update_course_assignments(int(self.user_id), str(course_id), assignments, now_ms)`
- client.py:754 `sync_profile_picture(...)` → `app_users.set_profile_picture_url(int(self.user_id), url)`
- client.py:769 `clear_cache(...)` → `schoology_cache_store.clear_user_cache(int(self.user_id))`
- Remove the vestigial `convex_url` constructor param from `SchoologyService` and the `convex_url=Config.CONVEX_URL` kwargs in `runtime.py:33,49`. Delete the `from .convex_sync import …` block; delete `services/schoology/convex_sync.py`.

### 4.3 `onboarding/` package

Delete `onboarding/convex_sync.py`. Rewrite `onboarding/__init__.py` to re-export from `db.app_users` with the `convex_url` first parameter **removed**: `get_or_create_user(user_id)` → `ensure_app_state`, `get_user(user_id)` → `get_user_app_state`, `update_onboarding_step(user_id, step)`, `update_schoology_connected(user_id, connected)` → `set_schoology_connected`, `save_consent(user_id, consent)`. Call sites take a one-line edit each (drop `Config.CONVEX_URL,` and the `str(...)` wrapper — pass `int` user ids):
- `auth/routes.py:20-33` — replace `_bootstrap_convex_user_async` thread with a direct `app_users.ensure_app_state(user_id)` call (local SQLite write; no thread needed).
- `api/routes.py:34,91,131` — get_current_user / start_onboarding / save_user_consent per §3.2.
- `schoology/routes.py:60-61,159-160,287` and `sync_profile_picture` at 67/170 → `app_users` calls.
- `services/mobile/service.py:522-523` → `app_users` calls; remove `create_convex_token` import (line 17) and `create_mobile_convex_token`.
- `mobile/routes.py:12,230-234` — `/mobile/me` reads `app_users.get_user_app_state`; delete `/mobile/convex/token` route.

### 4.4 aud=convex JWT surface (`auth/jwt_utils.py`, `api/routes.py`)

Delete: `JWT_CONVEX_AUDIENCE`, `JWT_DEFAULT_CONVEX_EXPIRATION_HOURS` (and its branch in `create_token`), `create_convex_token`, `verify_convex_token`, `get_jwks`, `_int_to_base64url`; routes per §3.5. Keep: key loading/generation, `create_token`, `create_mobile_access_token`, `verify_token`, `verify_mobile_access_token` (aud=mobile_api untouched).

### 4.5 Config + glue

- Delete from `config.py`: `CONVEX_URL`, `CONVEX_ADMIN_KEY` (already dead), `CONVEX_BRIDGE_SECRET`, `CHAT_INTERNAL_SECRET`, and the `validate()` block at ~line 208 requiring the bridge secret. Rename `CHAT_CONVEX_HEARTBEAT_MS` → `CHAT_HEARTBEAT_MS` (env: read `CHAT_HEARTBEAT_MS` falling back to `CHAT_CONVEX_HEARTBEAT_MS`). Add: `CHAT_DB_PATH` (default `backend/chat.db`), `APP_EVENTS_TTL_SECONDS` (3600), `APP_EVENTS_SSE_HEARTBEAT_SECONDS` (15), `CHAT_REAPER_INTERVAL_SECONDS` (60).
- Delete files: `backend/services/convex_bridge.py`, `backend/services/chat/convex_sync.py`, `backend/services/schoology/convex_sync.py`, `backend/onboarding/convex_sync.py`, `backend/internal_chat/` (whole package).
- `requirements.txt`: remove `convex==0.7.0` (dead dep, nothing imports it).
- `app.py`: register `events_bp` + `chat_api_bp`; drop `internal_chat_bp`; start the reaper thread.

### 4.6 The reaper (replaces the Convex cron)

New **`backend/services/chat/reaper.py`**: `start_reaper()` spawns a single daemon thread (module-level `threading.Event` guard so it starts once) looping `stop.wait(Config.CHAT_REAPER_INTERVAL_SECONDS)` → `chat_store.fail_stale_generations(now_ms, Config.CHAT_STALE_AFTER_SECONDS * 1000)`, logging the reaped count, exceptions swallowed+logged. Started from `create_app()` in `app.py` (the app runs `use_reloader=False`, single process, so no double-start; this mirrors how the scraper runs its own long-lived loop).

### 4.7 Scraper (the ONE permitted touch)

`services/scraper/scheduler.py:17,35`: `from onboarding.convex_sync import list_eligible_scraper_users` → `from db.app_users import list_eligible_scraper_users`; `list_eligible_scraper_users(Config.CONVEX_URL)` → `list_eligible_scraper_users()`. Return shape is preserved (`userId` as string, `schoologyConnected`, `smartFeaturesConsent`), so `refresh_eligible_user_memberships` body is untouched. Nothing else in `services/scraper/` changes.

---

## 5. Frontend changes

### 5.1 New data-layer modules (foundation)

**`frontend/src/lib/api.ts`** — `apiFetch(path, init?)`: prefixes `NEXT_PUBLIC_BACKEND_URL`, sets `credentials: 'include'`, throws `ApiError extends Error {status: number, code?: string}` on !ok. Typed helpers: `getUser()`, `getUpcoming()`, `getChatThreads()`, `getChatMessages(threadId)`, `getActiveGeneration(threadId)`, `sendChatMessage({threadId?, clientRequestId, content})`, `cancelGeneration(generationId)`, `startOnboarding()`, `saveConsent(body)`, `postDeveloperOverride(body)`. Shared TS types: `ApiUser`, `ChatThread`, `ChatMessage`, `ChatGeneration`, `UpcomingAssignment` (replace the deleted `Id<'chatThreads'>` etc. with plain `string`).

**`frontend/src/hooks/useAppEvents.ts`** — module-singleton connection to `GET /api/events` (fetch-based SSE reader, same parsing approach as today's `useSSEStream`), shared by all subscribers in the tab:
```ts
type AppEvent = { id: string; type: string; data: Record<string, unknown> };
export function subscribeAppEvents(type: string | '*', handler: (e: AppEvent) => void): () => void;
export function useAppEvents(type: string | '*', handler: (e: AppEvent) => void): void; // effect wrapper
export function useEventChannelState(): 'connecting' | 'open' | 'down';
```
Behavior: lazy-connect on first subscriber; track last event `id`; on error/close reconnect with capped exponential backoff (1s→2s→5s→10s max) sending `Last-Event-ID`; after any successful reconnect emit a synthetic `{type:'$reconnected'}` to all subscribers; a 503 from the endpoint parks the channel in `'down'` (retry every 60s). Each tab = one connection (multi-tab liveness preserved; stated deliberately).

**`frontend/src/hooks/useLiveQuery.ts`**:
```ts
export function useLiveQuery<T>(opts: {
  fetcher: () => Promise<T>;
  events?: { type: string; apply?: (data, prev: T|undefined) => T | 'refetch' }[]; // apply payload or invalidate
  enabled?: boolean;
  deps?: unknown[];             // refetch when these change
  backstopMs?: number;          // default 60_000 while channel open
  degradedPollMs?: number;      // default 30_000 while channel 'down'
  refetchOnFocus?: boolean;     // default true (visibilitychange + focus)
}): { data: T | undefined; error: ApiError | null; isLoading: boolean; refetch: () => Promise<void>; };
```
Semantics: fetch on mount / deps change; for each configured event, run `apply` (returning `'refetch'` triggers a fetch); always refetch on `$reconnected` and on focus. `undefined` = loading (preserves existing `=== undefined` checks). 403 surfaces via `error.status`.

### 5.2 Per-file changes

- **`src/app/layout.tsx`**: remove the `ConvexClientProvider` import and unwrap children.
- **`src/components/AppLayout.tsx`**: delete `useQuery(api.users.getUser)` + the separate `/api/user` fetch effect; replace both with ONE `useLiveQuery({fetcher: getUser, events: [{type:'user.updated', apply:(d)=>d.user ?? d}]})` (event payload *is* the user object). `userName = data?.name`, `userId = data?.email`, avatar `src = data?.profile_picture_url || fallback`. Keep the schoology-refresh effect as-is.
- **`src/app/dashboard/upcoming/page.tsx`** + **`src/components/upcoming/UpcomingAssignmentsCarosuel.tsx`**: both currently run `getUpcoming` independently — consolidate: the page owns `useLiveQuery({fetcher: getUpcoming → response.assignments, events: [{type:'schoology.updated', apply: 'refetch' /* debounced 500ms inside the hook via trailing-edge timer */}]})` and passes `assignments` (or `undefined` while loading) as a prop to the carousel; the carousel drops its query and keeps its `undefined`-gated loading logic.
- **`src/components/onboarding/OnboardingController.tsx`**: replace `useQuery(api.users.getUser)` with `useLiveQuery(getUser, events: [user.updated → apply])` plus a `handleUserUpdate(user)` setter passed down. Step components (`WelcomeStep`, `SmartConsentStep`, `ConnectLmsStep`) call their existing POSTs and then `handleUserUpdate(response.user)` — **the mutation response carries the new state, so advancement is synchronous with the click**; the `user.updated` event covers the Schoology-OAuth path (full-page redirect → fresh fetch anyway) and other tabs. `currentStep` derives from `user.onboarding_step` (snake_case now). Mobile-mode branches unchanged.
- **`src/components/LayoutWrapper.tsx`**: already pure Flask fetch — reads `data.onboarding_step`; no change required.
- **Deletions**: `frontend/convex/` (entire directory incl. `_generated`), `src/components/ConvexClientProvider.tsx`, `src/hooks/useChatThreads.ts`, `src/hooks/useChatMessages.ts`, `src/hooks/useSendChatMessage.ts`, `src/hooks/useChatThreadRouteState.ts` (all verified dead or replaced), `convex` from `package.json` dependencies (then `pnpm install` to update the lockfile), `NEXT_PUBLIC_CONVEX_URL` from any `.env*` / deployment env (only code reference is ConvexClientProvider.tsx:48, which is deleted).

### 5.3 Chat page rewrite (`src/app/dashboard/chat/page.tsx`) — precision-critical

Single-SSE-source model. Local state replaces the three reactive queries:

| state | type | replaces |
|---|---|---|
| `threads` | `useLiveQuery(getChatThreads → .threads, events: chat.thread.created/updated → upsert-by-_id + resort by updatedAt desc)`; `error.status===403` ⇒ `notEntitled` | `useQuery(listThreads)` (incl. the `threads===null` entitlement signal) |
| `messages: ChatMessage[] \| undefined` | plain `useState` + `loadMessages(threadId)` on thread select; `useAppEvents('chat.message.created')` appends when `threadId` matches selected and `_id` not present | `useQuery(listMessages)` |
| `streaming: StreamingState` | unchanged shape, `generationId: string` | local + `getActiveGeneration` reconciliation |
| `cancelRequested: boolean` | local, set by cancel POST and by `chat.generation.updated` events | `activeGeneration.cancelRequested` |

Flows:
- **Send**: generate `clientRequestId`, optimistic-append the user message locally (temp `_id`), `POST /api/chat/messages`; on success set `selectedThreadId`, set `streaming = {content:'', status:'queued', activity:null, generationId, toolCalls:[]}` (opens the token SSE, unchanged `useSSEStream` + handlers); on 403 show not-entitled; on 409/error restore input + drop optimistic message.
- **Terminal (the handoff fix)**: `onTerminal(data)` now (a) appends a local assistant message `{_id: 'gen-'+generationId, role:'assistant', content: data.content, status: data.status}` **keeping the streamed text**, (b) clears `streaming` + `cancelRequested`, (c) background `loadMessages(threadId)` + `threads.refetch()` to reconcile real ids and pick up the generated title (the `chat.thread.updated` event usually beats this).
- **Resume**: on selected-thread change (and initial hash mount) fetch active-generation; if active, set `streaming` (empty content — SSE `snapshot` fills it). `chat.generation.updated` events for the selected thread: active status + no local streaming → start resume; terminal status while this tab has no token-SSE (e.g. generation ran in another tab) → clear streaming and `loadMessages` (the `chat.message.created` event already appended the final message, so this is belt-and-braces).
- **visibleMessages**: keep today's rule — while `streaming`, hide assistant rows with `status !== 'completed'`; additionally dedupe the temp terminal-appended message once the refetched list contains a completed assistant message with `completedAt >=` its timestamp (simplest: `loadMessages` result wholesale replaces local state, temp entries included-or-gone).
- **Cancel**: button shows when `streaming && !cancelRequested`; POST cancel sets `cancelRequested` locally.
- Remove: `convex/react`, `_generated` imports, `useConvexAuthReady` (gate on nothing — the session cookie is ambient; `useLiveQuery` handles 401/403 via error state), `Id<>` types → `string`.

Latency parity checklist (all push-driven): onboarding advance (response-payload + `user.updated`), thread title rename (`chat.thread.updated`), thread list new-thread (`chat.thread.created`), cross-tab in-flight generation + cancel state (`chat.generation.updated`), cross-tab new messages (`chat.message.created`), upcoming refresh (`schoology.updated`), profile picture (`user.updated`). None of these waits on a poll.

---

## 6. Data migration (one-time)

**`backend/scripts/migrate_convex_export.py`** (new; run manually once, before first deploy of the new code, after `init_db()` has created the tables).

Source of truth: a Convex snapshot export — run `npx convex export --path ./convex-export.zip` in `frontend/` against the prod deployment (uses the existing CONVEX_DEPLOYMENT credentials; avoids writing any new bridge code). The zip contains one `documents.jsonl` per table.

Usage: `python -m scripts.migrate_convex_export /path/to/convex-export.zip [--dry-run]`.

Per-table policy:
- **`chatThreads`, `chatMessages`, `chatGenerations`, `chatToolCalls` → MIGRATE** (user data, must not be lost). Preserve Convex `_id` strings as the TEXT PKs (old thread-hash URLs keep working). Field map is mechanical camelCase→snake_case; `usage`/`toolTraceStats` objects → JSON TEXT; missing optionals → NULL; `userId` string → `int` (skip rows whose user id is not in main.db `users`, with a warning). Insert order: threads → messages → generations → tool_calls. Any still-`queued`/`streaming` generation in the export is written as `failed`/`stale_generation` (it can never resume).
- **`users` → MIGRATE** into the new columns on main.db `users` (consent is a user-granted record — keep it): match on `int(userId) == users.id`; set `onboarding_step`, `schoology_connected`, `smart_features_consent_json`, `profile_picture_url`, `app_state_updated_at = updatedAt`. Unmatched → warn + skip.
- **`userPreferences` → MIGRATE** (trivial; one row per user).
- **`schoologyCourses`, `schoologyCourseMemberships`, `schoologyAssignments`, `schoologyAssignmentUserState` → DO NOT MIGRATE.** Pure cache; `AppLayout` fires `POST /api/schoology/refresh` on every load and the scraper scheduler refreshes memberships, so the cache self-heals on first login. Consequences until then (acceptable): empty upcoming carousel, chat generation context without the course list. Document this in the script's `--help`.
- Idempotent: `INSERT OR REPLACE` / upserts keyed on preserved ids, safe to re-run.

---

## 7. Execution plan (work packages, disjoint file ownership)

Waves run sequentially; packages inside a wave run in parallel and never share a file.

**Wave 1**
- **P1 — Events infrastructure** *(precision-critical: everything else leans on it)*
  Owns: `backend/services/events.py` (new), `backend/events/__init__.py` + `backend/events/routes.py` (new blueprint, `GET /api/events`), `backend/app.py` (register events_bp only in this wave), `backend/config.py` (add APP_EVENTS_* + CHAT_DB_PATH + CHAT_REAPER_INTERVAL_SECONDS + CHAT_HEARTBEAT_MS rename **only** — do not remove CONVEX_* yet so wave-1 code still runs), `frontend/src/lib/api.ts`, `frontend/src/hooks/useAppEvents.ts`, `frontend/src/hooks/useLiveQuery.ts`.
  Accept: `python -m compileall backend` clean; with Redis configured, `curl -N /api/events` (with a session cookie) shows heartbeats, and `redis-cli XADD user:<id>:events …` appears as an SSE frame; replay works with `Last-Event-ID`; frontend hooks typecheck (`pnpm build` may still fail on unrelated Convex code — gate on `tsc --noEmit` for the three new files only).
- **P7 — Migration script** (mechanical; codes against §1 DDL as written)
  Owns: `backend/scripts/migrate_convex_export.py` (+ `backend/scripts/__init__.py` if absent).
  Accept: `--dry-run` against a fixture export zip prints row counts; compileall clean.

**Wave 2**
- **P2 — Schema + stores** *(precision-critical: chat state machine)*
  Owns: `backend/db/init.py`, `backend/db/chat_store.py`, `backend/db/app_users.py`, `backend/db/schoology_cache_store.py`, `backend/onboarding/__init__.py`, delete `backend/onboarding/convex_sync.py`.
  Depends: P1 (publishes events).
  Accept: compileall; a scripted smoke (`python - <<'EOF' …`) against temp DB paths exercising: create_generation idempotency (same clientRequestId twice → same ids), thread_busy rejection, queued→streaming accepted exactly once (second call `accepted:false`), heartbeat raises on terminal, completed→cancelled is a no-op returning the completed row, upsert_tool_call insert-then-patch keeps COALESCE semantics, fail_stale_generations reaps a 121s-stale streaming row but not a 60s one, update_courses tombstones a removed membership, get_upcoming excludes past-due and merges grade/completed from user state, save_consent flips step to completed and is_chat_entitled becomes True.
- **P8 stub — none** (verification comes last).

**Wave 3** (all four in parallel — disjoint files)
- **P3 — Chat backend rewire** *(precision-critical)*
  Owns: `backend/services/chat/service.py`, `worker.py`, `types.py`, `launcher.py` (new), `reaper.py` (new), delete `services/chat/convex_sync.py`, `backend/chat/__init__.py` + `backend/chat/routes.py` (new), delete `backend/internal_chat/`, `backend/app.py` (register chat_api_bp, drop internal_chat_bp, start reaper), `backend/config.py` (remove CONVEX_*/CHAT_INTERNAL_SECRET + validate() block).
  Depends: P1, P2.
  Accept: compileall; with Redis + a fake `LLM_API_KEY`, `POST /api/chat/messages` creates rows, spawns a worker that fails with `provider_error`, the generation ends `failed`, the token SSE emits `terminal`, and `chat.generation.updated`/`chat.message.created` appear on `/api/events`; reaper log line appears each minute; `grep -rn convex backend/services/chat backend/chat` → 0.
- **P4 — Schoology/user/auth swap** (mechanical)
  Owns: `backend/api/routes.py`, `backend/schoology/routes.py`, `backend/services/schoology/client.py` + `runtime.py`, delete `services/schoology/convex_sync.py`, `backend/services/mobile/service.py`, `backend/mobile/routes.py`, `backend/auth/routes.py`, `backend/auth/jwt_utils.py`, `backend/services/scraper/scheduler.py` (§4.7 two lines only), delete `backend/services/convex_bridge.py`, `backend/requirements.txt`.
  Depends: P2.
  Accept: compileall; `GET /api/user` returns the §3.2 shape from SQLite; `POST /api/user/onboarding/start` returns `"user"` and emits `user.updated`; `/api/convex-token`, `/.well-known/jwks.json`, `/mobile/convex/token` return 404; `grep -rn "convex\|CONVEX" backend --include='*.py' | grep -v env/ | grep -v scripts/migrate` → 0 after P3+P4 both land.
- **P5 — Frontend shell** (mechanical)
  Owns: `frontend/src/app/layout.tsx`, `frontend/src/components/AppLayout.tsx`, `frontend/src/app/dashboard/upcoming/page.tsx`, `frontend/src/components/upcoming/UpcomingAssignmentsCarosuel.tsx`, `frontend/src/components/onboarding/*` (Controller + 3 steps), delete `frontend/src/components/ConvexClientProvider.tsx`, delete the 4 dead hooks, delete `frontend/convex/`, `frontend/package.json` (+ lockfile via `pnpm install`).
  Depends: P1 hooks; codes against §3 contract (can proceed while P3/P4 are in flight).
  Accept: no `convex` imports remain outside `chat/page.tsx`; app boots to upcoming page against the new backend.
- **P6 — Chat page rewrite** *(precision-critical)*
  Owns: `frontend/src/app/dashboard/chat/page.tsx` only.
  Depends: P1 hooks + §3.3/§5.3 contract.
  Accept: manual flows in §8.4 items 4–8.

**Wave 4**
- **P8 — Verification & cleanup sweep**
  Owns: `README.md`, `Makefile`, `scripts/dev.sh`, `scripts/devtools.py` (all reference Convex — strip dev-orchestration of `convex dev` etc.), any leftover `.env.example` text.
  Runs the full §8 gate.

---

## 8. Verification plan

Honest constraint: **the backend has no test suite**, and end-to-end Schoology flows can't be exercised without a real account. Verification is therefore: static gates + store-level scripted checks + a manual smoke checklist for the flows that need a browser.

1. **Static / build gates**
   - `cd backend && python -m compileall -q .` → exit 0 (excludes `env/`).
   - `cd frontend && pnpm install && pnpm build` → exit 0.
   - Grep gates (all must return nothing):
     - `grep -rn "convex" frontend/src frontend/package.json --include='*' -i`
     - `test ! -d frontend/convex`
     - `grep -rn "NEXT_PUBLIC_CONVEX_URL" frontend backend`
     - `grep -rni "convex" backend --include='*.py' | grep -v env/ | grep -v scripts/migrate_convex_export.py` (the migration script may name its source)
     - `grep -rn "from 'convex/react'\|convex/_generated\|ConvexProvider\|useConvexAuthReady" frontend/src`
     - `grep -rn "CHAT_INTERNAL_SECRET\|CONVEX_BRIDGE_SECRET\|convex-token\|jwks" backend --include='*.py' | grep -v env/` → only mobile-irrelevant hits allowed: none expected.
2. **Store smoke script** (the P2 acceptance script, kept at `backend/scripts/smoke_stores.py`, run against temp DB files): the 11 assertions listed in P2. This is the closest thing to a unit test for the state machine — treat any failure as a blocker.
3. **Events channel check**: with `UPSTASH_REDIS_URL` set, run Flask, log in, `curl -N -H "Last-Event-ID: 0-0" http://localhost:3111/api/events --cookie "session=…"`; in another shell hit `POST /api/user/onboarding/start`; assert a `user.updated` frame arrives < 1s.
4. **Manual smoke checklist** (browser, dev backend, real or dev-override Schoology optional):
   1. Google login → lands on onboarding `welcome` (fresh user) or dashboard.
   2. Welcome → "Get started" advances to connect_lms **without a page reload and instantly**.
   3. Developer-override connect (no real OAuth needed) advances to smart_consent instantly; a second open tab advances by itself (event-driven).
   4. Consent accept → dashboard; chat becomes entitled.
   5. Chat: send message → tokens stream; tool activity renders; on completion **the streamed text stays put with no flicker** and the thread title updates within ~2s.
   6. Mid-generation page refresh → resume shows accumulated content (snapshot) and continues streaming.
   7. Mid-generation second tab on same thread → shows in-flight state; cancel from tab B stops tab A.
   8. Kill the worker process mid-generation (`kill <pid>`) → within ~3 min the message flips to failed (reaper) and the SSE terminates.
   9. Upcoming: hit refresh → carousel updates without reload (schoology.updated event) — requires a connected account.
   10. Kill Redis (unset URL) → app still boots; data loads via degraded polling; chat send returns a config error rather than crashing.
   11. Mobile WebView flows untouched: `/mobile/me` returns onboarding state; token refresh works (aud=mobile_api).
5. **Migration rehearsal**: run `npx convex export` against the current deployment, run the script `--dry-run` then for real against a copy of `main.db`/fresh `chat.db`; verify thread count matches Convex dashboard and a migrated thread's URL hash opens with full history.

---

## Appendix: environment variable changes

Delete everywhere (backend env + Convex dashboard, which itself disappears): `CONVEX_URL`, `CONVEX_ADMIN_KEY`, `CONVEX_BRIDGE_SECRET`, `CHAT_INTERNAL_SECRET`, `CONVEX_DEPLOYMENT` (after the export), frontend `NEXT_PUBLIC_CONVEX_URL`, Convex-side `BACKEND_URL`/`CHAT_INTERNAL_SECRET`/`CHAT_STALE_AFTER_SECONDS`.
Add (backend): `CHAT_DB_PATH` (optional), `APP_EVENTS_TTL_SECONDS`, `APP_EVENTS_SSE_HEARTBEAT_SECONDS`, `CHAT_REAPER_INTERVAL_SECONDS`, `CHAT_HEARTBEAT_MS` (old `CHAT_CONVEX_HEARTBEAT_MS` still honored). `CHAT_STALE_AFTER_SECONDS` already exists in Flask config and keeps its 120s default.
