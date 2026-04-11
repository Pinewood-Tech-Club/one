"""
Typed chat service models.
"""
from dataclasses import dataclass
from typing import Any, Literal

MessageRole = Literal["system", "user", "assistant"]
MessageStatus = Literal["queued", "streaming", "completed", "failed", "cancelled"]
TerminalStatus = Literal["completed", "failed", "cancelled"]


@dataclass(frozen=True)
class ChatTranscriptMessage:
    role: MessageRole
    content: str
    status: MessageStatus
    created_at: int


@dataclass(frozen=True)
class GenerationContext:
    generation_id: str
    thread_id: str
    user_id: str
    assistant_message_id: str
    provider: str
    model: str
    status: MessageStatus
    cancel_requested: bool
    created_at: int
    updated_at: int
    started_at: int | None
    assistant_message_content: str
    transcript: list[ChatTranscriptMessage]

    @classmethod
    def from_convex(cls, generation_id: str, payload: dict[str, Any]) -> "GenerationContext":
        generation = _require_dict(payload, "generation")
        thread = _require_dict(payload, "thread")
        assistant_message = _require_dict(payload, "assistantMessage")
        transcript_raw = payload.get("transcript")
        if not isinstance(transcript_raw, list):
            raise ValueError("transcript must be a list")

        thread_id = _require_string(
            thread,
            "_id",
            fallback_key="threadId",
            fallback_source=generation,
        )
        user_id = _require_string(generation, "userId")
        assistant_message_id = _require_string(
            generation,
            "assistantMessageId",
            fallback_key="_id",
            fallback_source=assistant_message,
        )
        provider = _optional_string(generation, "provider") or ""
        model = _optional_string(generation, "model") or ""
        status = _require_status(generation, "status")
        transcript = [_parse_transcript_message(item) for item in transcript_raw]

        return cls(
            generation_id=generation_id,
            thread_id=thread_id,
            user_id=user_id,
            assistant_message_id=assistant_message_id,
            provider=provider,
            model=model,
            status=status,
            cancel_requested=bool(generation.get("cancelRequested", False)),
            created_at=_require_int(generation, "createdAt"),
            updated_at=_require_int(generation, "updatedAt"),
            started_at=_optional_int(generation, "startedAt"),
            assistant_message_content=_optional_string(assistant_message, "content") or "",
            transcript=sorted(transcript, key=lambda item: item.created_at),
        )


@dataclass(frozen=True)
class StreamDelta:
    content: str


@dataclass(frozen=True)
class ProviderCompletion:
    provider_message_id: str | None
    finish_reason: str | None
    usage: dict[str, Any] | None


def _require_dict(payload: dict[str, Any], key: str) -> dict[str, Any]:
    value = payload.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be an object")
    return value


def _require_string(
    payload: dict[str, Any],
    key: str,
    *,
    fallback_key: str | None = None,
    fallback_source: dict[str, Any] | None = None,
) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    if fallback_key and fallback_source:
        fallback = fallback_source.get(fallback_key)
        if isinstance(fallback, str) and fallback.strip():
            return fallback.strip()
    raise ValueError(f"{key} must be a non-empty string")


def _optional_string(payload: dict[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    raise ValueError(f"{key} must be a string when provided")


def _require_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be an integer")
    return int(value)


def _optional_int(payload: dict[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be an integer when provided")
    return int(value)


def _require_status(payload: dict[str, Any], key: str) -> MessageStatus:
    value = payload.get(key)
    allowed = {"queued", "streaming", "completed", "failed", "cancelled"}
    if value not in allowed:
        raise ValueError(f"{key} must be one of {sorted(allowed)}")
    return value


def _parse_transcript_message(value: Any) -> ChatTranscriptMessage:
    if not isinstance(value, dict):
        raise ValueError("transcript entry must be an object")

    role = value.get("role")
    if role not in {"system", "user", "assistant"}:
        raise ValueError("transcript role must be system, user, or assistant")

    content = value.get("content")
    if not isinstance(content, str):
        raise ValueError("transcript content must be a string")

    status = value.get("status", "completed")
    if status not in {"queued", "streaming", "completed", "failed", "cancelled"}:
        raise ValueError("transcript status is invalid")

    created_at = value.get("createdAt")
    if isinstance(created_at, bool) or not isinstance(created_at, (int, float)):
        raise ValueError("transcript createdAt must be an integer")

    return ChatTranscriptMessage(
        role=role,
        content=content,
        status=status,
        created_at=int(created_at),
    )
