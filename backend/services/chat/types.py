"""
Typed chat service models.
"""
from dataclasses import dataclass, field
from typing import Any, Literal

MessageRole = Literal["system", "user", "assistant"]
MessageStatus = Literal["queued", "streaming", "completed", "failed", "cancelled"]
TerminalStatus = Literal["completed", "failed", "cancelled"]
ToolCallStatus = Literal["pending", "running", "completed", "failed"]


@dataclass(frozen=True)
class ChatTranscriptMessage:
    message_id: str | None
    role: MessageRole
    content: str
    status: MessageStatus
    created_at: int


@dataclass(frozen=True)
class GenerationUserRecord:
    user_id: str
    onboarding_step: str
    schoology_connected: bool


@dataclass(frozen=True)
class GenerationCourse:
    course_id: str
    course_title: str
    section_title: str | None


@dataclass(frozen=True)
class GenerationToolCall:
    sequence: int
    call_id: str
    tool_name: str
    status: ToolCallStatus
    arguments_text: str | None
    output_text: str | None
    summary_text: str | None
    error_text: str | None
    created_at: int
    updated_at: int
    started_at: int | None
    completed_at: int | None


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
    user_record: GenerationUserRecord | None
    courses: list[GenerationCourse]
    tool_calls: list[GenerationToolCall]
    transcript: list[ChatTranscriptMessage]

    @classmethod
    def from_payload(cls, generation_id: str, payload: dict[str, Any]) -> "GenerationContext":
        """Parse the chat_store.get_generation_context payload (camelCase, ids under "_id")."""
        generation = _require_dict(payload, "generation")
        thread = _require_dict(payload, "thread")
        assistant_message = _require_dict(payload, "assistantMessage")
        transcript_raw = payload.get("transcript")
        if not isinstance(transcript_raw, list):
            raise ValueError("transcript must be a list")
        courses_raw = payload.get("courses") or []
        if not isinstance(courses_raw, list):
            raise ValueError("courses must be a list")
        tool_calls_raw = payload.get("toolCalls") or []
        if not isinstance(tool_calls_raw, list):
            raise ValueError("toolCalls must be a list")
        user_record_raw = payload.get("userRecord")
        if user_record_raw is not None and not isinstance(user_record_raw, dict):
            raise ValueError("userRecord must be an object when provided")

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
            user_record=_parse_user_record(user_record_raw),
            courses=[_parse_course(item) for item in courses_raw],
            tool_calls=sorted(
                [_parse_tool_call(item) for item in tool_calls_raw],
                key=lambda item: (item.created_at, item.sequence),
            ),
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


@dataclass(frozen=True)
class ProviderToolCall:
    item_id: str
    call_id: str
    name: str
    arguments: str


@dataclass(frozen=True)
class ProviderRoundResult:
    response_id: str | None
    output: list[dict[str, Any]]
    output_text: str
    function_calls: list[ProviderToolCall]
    usage: dict[str, Any] | None
    status: str | None = None


@dataclass(frozen=True)
class ToolExecutionStats:
    course_ids: set[str] = field(default_factory=set)
    assignment_handles: set[str] = field(default_factory=set)
    document_handles: set[str] = field(default_factory=set)

    def to_trace_stats(self) -> dict[str, int]:
        return {
            "toolCallsCount": 0,
            "coursesTouched": len(self.course_ids),
            "assignmentsTouched": len(self.assignment_handles),
            "documentsTouched": len(self.document_handles),
        }


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
        message_id=_optional_string(value, "_id"),
        role=role,
        content=content,
        status=status,
        created_at=int(created_at),
    )


def _parse_user_record(value: dict[str, Any] | None) -> GenerationUserRecord | None:
    if value is None:
        return None
    return GenerationUserRecord(
        user_id=_require_string(value, "userId"),
        onboarding_step=_require_string(value, "onboardingStep"),
        schoology_connected=bool(value.get("schoologyConnected", False)),
    )


def _parse_course(value: Any) -> GenerationCourse:
    if not isinstance(value, dict):
        raise ValueError("course entry must be an object")
    return GenerationCourse(
        course_id=_require_string(value, "courseId"),
        course_title=_require_string(value, "courseTitle"),
        section_title=_optional_string(value, "sectionTitle"),
    )


def _parse_tool_call(value: Any) -> GenerationToolCall:
    if not isinstance(value, dict):
        raise ValueError("toolCall entry must be an object")

    status = value.get("status")
    if status not in {"pending", "running", "completed", "failed"}:
        raise ValueError("toolCall status is invalid")

    return GenerationToolCall(
        sequence=_require_int(value, "sequence"),
        call_id=_require_string(value, "callId"),
        tool_name=_require_string(value, "toolName"),
        status=status,
        arguments_text=_optional_string(value, "argumentsText"),
        output_text=_optional_string(value, "outputText"),
        summary_text=_optional_string(value, "summaryText"),
        error_text=_optional_string(value, "errorText"),
        created_at=_require_int(value, "createdAt"),
        updated_at=_require_int(value, "updatedAt"),
        started_at=_optional_int(value, "startedAt"),
        completed_at=_optional_int(value, "completedAt"),
    )
