"""
App-wide per-user event stream persistence in Redis Streams.

Backs GET /api/events: store modules publish entity-change events here and
every connected tab replays/blocks on its own cursor over the same stream.
"""
import json
import logging
import threading
from typing import Any

import redis

from config import Config

logger = logging.getLogger(__name__)

_client_lock = threading.Lock()
_client: redis.Redis | None = None

MAX_STREAM_LENGTH = 512


class EventsConfigurationError(RuntimeError):
    """Raised when Redis-backed app events are not configured."""


def events_configured() -> bool:
    return bool(Config.UPSTASH_REDIS_URL)


def publish_user_event(user_id: int, event_type: str, payload: dict) -> None:
    """Best-effort publish: a Redis blip degrades liveness, never correctness."""
    try:
        client = _get_client()
        key = _events_key(user_id)
        pipe = client.pipeline(transaction=False)
        pipe.xadd(
            key,
            {"type": event_type, "data": json.dumps(payload, separators=(",", ":"))},
            maxlen=MAX_STREAM_LENGTH,
            approximate=True,
        )
        pipe.expire(key, Config.APP_EVENTS_TTL_SECONDS)
        pipe.execute()
    except Exception:
        logger.warning(
            "Failed to publish %s event for user %s", event_type, user_id, exc_info=True
        )


def latest_event_id(user_id: int) -> str:
    """Current tail of the stream, so fresh connections skip history without racing '$'."""
    client = _get_client()
    entries = client.xrevrange(_events_key(user_id), max="+", min="-", count=1)
    if not entries:
        return "0-0"
    entry_id, _ = entries[0]
    return entry_id


def replay_events_after(user_id: int, last_event_id: str) -> list[dict[str, Any]]:
    client = _get_client()
    entries = client.xrange(_events_key(user_id), min=f"({last_event_id}", max="+")
    return [_decode_stream_entry(entry_id, payload) for entry_id, payload in entries]


def block_for_new_events(
    user_id: int,
    last_event_id: str,
    *,
    block_ms: int,
    count: int = 100,
) -> list[dict[str, Any]]:
    client = _get_client()
    response = client.xread(
        streams={_events_key(user_id): last_event_id},
        block=block_ms,
        count=count,
    )
    if not response:
        return []

    _, entries = response[0]
    return [_decode_stream_entry(entry_id, payload) for entry_id, payload in entries]


def _decode_stream_entry(event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    data: dict[str, Any] = {}
    raw = payload.get("data")
    if raw:
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                data = decoded
        except ValueError:
            logger.warning("Malformed app event payload at %s", event_id)
    return {
        "id": event_id,
        "type": payload.get("type", "message"),
        "data": data,
    }


def _get_client() -> redis.Redis:
    global _client
    if not Config.UPSTASH_REDIS_URL:
        raise EventsConfigurationError("UPSTASH_REDIS_URL is not configured")
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = redis.from_url(
                    Config.UPSTASH_REDIS_URL,
                    decode_responses=True,
                    health_check_interval=30,
                )
    return _client


def _events_key(user_id: int) -> str:
    return f"user:{user_id}:events"
