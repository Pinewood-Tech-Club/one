"""
Live chat stream persistence in Redis Streams.
"""
import json
import threading
from typing import Any

import redis

from config import Config

_client_lock = threading.Lock()
_client: redis.Redis | None = None

ACTIVE_STATUSES = {"queued", "streaming"}


class LiveStreamConfigurationError(RuntimeError):
    """Raised when Redis-backed live streaming is not configured."""


def get_live_state(generation_id: str) -> dict[str, Any] | None:
    client = _get_client()
    payload = client.get(_state_key(generation_id))
    if not payload:
        return None
    return json.loads(payload)


def initialize_live_state(
    generation_id: str,
    *,
    status: str,
    content: str,
    provider: str,
    model: str,
    updated_at: int,
    user_id: str | None = None,
) -> dict[str, Any]:
    state = {
        "generationId": generation_id,
        "status": status,
        "content": content,
        "provider": provider,
        "model": model,
        "updatedAt": updated_at,
        "latestEventId": None,
    }
    if user_id:
        state["userId"] = user_id
    _write_state(generation_id, state, active=True)
    _get_client().expire(_events_key(generation_id), Config.CHAT_REDIS_ACTIVE_TTL_SECONDS)
    return state


def append_delta(
    generation_id: str,
    *,
    delta: str,
    status: str,
    updated_at: int,
) -> str:
    return append_event(
        generation_id,
        event_type="delta",
        status=status,
        updated_at=updated_at,
        delta=delta,
    )


def append_event(
    generation_id: str,
    *,
    event_type: str,
    status: str | None,
    updated_at: int,
    **fields: Any,
) -> str:
    payload: dict[str, str] = {
        "type": event_type,
        "updatedAt": str(updated_at),
    }
    if status is not None:
        payload["status"] = status
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, (dict, list)):
            payload[key] = json.dumps(value)
        else:
            payload[key] = str(value)

    return _get_client().xadd(_events_key(generation_id), payload)


def publish_terminal(
    generation_id: str,
    *,
    status: str,
    content: str,
    updated_at: int,
    provider_message_id: str | None = None,
    usage: dict[str, Any] | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
) -> tuple[str, dict[str, Any]]:
    client = _get_client()
    event_payload = {
        "type": "terminal",
        "status": status,
        "content": content,
        "updatedAt": str(updated_at),
    }
    if provider_message_id:
        event_payload["providerMessageId"] = provider_message_id
    if usage is not None:
        event_payload["usage"] = json.dumps(usage)
    if error_code:
        event_payload["errorCode"] = error_code
    if error_message:
        event_payload["errorMessage"] = error_message

    event_id = client.xadd(_events_key(generation_id), event_payload)

    state = get_live_state(generation_id) or {"generationId": generation_id}
    state.update(
        {
            "status": status,
            "content": content,
            "updatedAt": updated_at,
            "latestEventId": event_id,
            "providerMessageId": provider_message_id,
            "usage": usage,
            "errorCode": error_code,
            "errorMessage": error_message,
        }
    )
    _write_state(generation_id, state, active=False)
    return event_id, state


def replay_events_after(generation_id: str, last_event_id: str) -> list[dict[str, Any]]:
    client = _get_client()
    entries = client.xrange(_events_key(generation_id), min=f"({last_event_id}", max="+")
    return [_decode_stream_entry(entry_id, payload) for entry_id, payload in entries]


def block_for_new_events(
    generation_id: str,
    last_event_id: str,
    *,
    block_ms: int,
    count: int = 100,
) -> list[dict[str, Any]]:
    client = _get_client()
    response = client.xread(
        streams={_events_key(generation_id): last_event_id},
        block=block_ms,
        count=count,
    )
    if not response:
        return []

    _, entries = response[0]
    return [_decode_stream_entry(entry_id, payload) for entry_id, payload in entries]


def touch_live_state(
    generation_id: str,
    *,
    active: bool,
    content: str | None = None,
    updated_at: int | None = None,
) -> None:
    client = _get_client()
    ttl = Config.CHAT_REDIS_ACTIVE_TTL_SECONDS if active else Config.CHAT_REDIS_FINAL_TTL_SECONDS
    state_key = _state_key(generation_id)
    events_key = _events_key(generation_id)

    if content is not None:
        # Update snapshot so reconnecting clients get current content
        raw = client.get(state_key)
        state = json.loads(raw) if raw else {"generationId": generation_id}
        state["content"] = content
        if updated_at is not None:
            state["updatedAt"] = updated_at

        pipe = client.pipeline(transaction=False)
        pipe.set(state_key, json.dumps(state), ex=ttl)
        pipe.expire(events_key, ttl)
        pipe.execute()
    else:
        pipe = client.pipeline(transaction=False)
        pipe.expire(state_key, ttl)
        pipe.expire(events_key, ttl)
        pipe.execute()


def _write_state(generation_id: str, state: dict[str, Any], *, active: bool) -> None:
    client = _get_client()
    ttl = Config.CHAT_REDIS_ACTIVE_TTL_SECONDS if active else Config.CHAT_REDIS_FINAL_TTL_SECONDS
    client.set(_state_key(generation_id), json.dumps(state), ex=ttl)
    client.expire(_events_key(generation_id), ttl)


def _decode_stream_entry(event_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    decoded: dict[str, Any] = {
        "id": event_id,
        "type": payload.get("type", "delta"),
        "status": payload.get("status"),
        "updatedAt": _coerce_int(payload.get("updatedAt")),
    }
    if "delta" in payload:
        decoded["delta"] = payload["delta"]
    if "content" in payload:
        decoded["content"] = payload["content"]
    if "providerMessageId" in payload:
        decoded["providerMessageId"] = payload["providerMessageId"]
    if "errorCode" in payload:
        decoded["errorCode"] = payload["errorCode"]
    if "errorMessage" in payload:
        decoded["errorMessage"] = payload["errorMessage"]
    if "usage" in payload and payload["usage"]:
        decoded["usage"] = json.loads(payload["usage"])
    for key, value in payload.items():
        if key in decoded or key in {"type", "status", "updatedAt", "delta", "content", "providerMessageId", "errorCode", "errorMessage", "usage"}:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    decoded[key] = json.loads(stripped)
                    continue
                except ValueError:
                    pass
        decoded[key] = value
    return decoded


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _get_client() -> redis.Redis:
    global _client
    if not Config.UPSTASH_REDIS_URL:
        raise LiveStreamConfigurationError("UPSTASH_REDIS_URL is not configured")
    if _client is None:
        with _client_lock:
            if _client is None:
                _client = redis.from_url(
                    Config.UPSTASH_REDIS_URL,
                    decode_responses=True,
                    health_check_interval=30,
                )
    return _client


def _state_key(generation_id: str) -> str:
    return f"chat:live:{generation_id}:state"


def _events_key(generation_id: str) -> str:
    return f"chat:live:{generation_id}:events"
