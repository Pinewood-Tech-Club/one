"""
Chat generation orchestration.
"""
import logging
import queue
import threading
import time
from dataclasses import dataclass

from config import Config

from . import convex_sync, live_stream
from .prompting import build_provider_messages
from .provider import ChatProviderError, stream_chat_completion
from .types import GenerationContext

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


class ChatConfigurationError(RuntimeError):
    """Raised when required chat configuration is missing."""


class ChatGenerationNotFoundError(RuntimeError):
    """Raised when the requested generation record does not exist."""


class ChatContractError(RuntimeError):
    """Raised when the backend/Convex contract is violated."""


class _GenerationCancelled(Exception):
    """Raised internally to short-circuit provider streaming."""


@dataclass(frozen=True)
class GenerationRunResult:
    generation_id: str
    status: str
    characters_streamed: int
    event_count: int
    provider_message_id: str | None = None
    usage: dict | None = None


def run_generation(generation_id: str) -> GenerationRunResult:
    _validate_chat_configuration()

    raw_context = convex_sync.get_generation_context(generation_id)
    if raw_context is None:
        raise ChatGenerationNotFoundError(f"Generation {generation_id} was not found")

    try:
        context = GenerationContext.from_convex(generation_id, raw_context)
    except ValueError as exc:
        raise ChatContractError(str(exc)) from exc

    provider_name = context.provider or _provider_name()
    model_name = context.model or Config.LLM_MODEL
    accumulated_content = context.assistant_message_content
    event_count = 0
    first_token_seen = False
    last_text_at = context.started_at or context.updated_at or _now_ms()
    last_activity = "thinking"

    logger.info(
        "chat_generation_start generation_id=%s thread_id=%s user_id=%s provider=%s model=%s status=%s",
        context.generation_id,
        context.thread_id,
        context.user_id,
        provider_name,
        model_name,
        context.status,
    )

    if context.status in TERMINAL_STATUSES:
        logger.info(
            "chat_generation_terminal_noop generation_id=%s status=%s",
            context.generation_id,
            context.status,
        )
        return GenerationRunResult(
            generation_id=context.generation_id,
            status=context.status,
            characters_streamed=len(accumulated_content),
            event_count=0,
        )

    def publish_terminal(
        *,
        status: str,
        updated_at: int,
        provider_message_id: str | None = None,
        usage: dict | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> None:
        live_stream.publish_terminal(
            context.generation_id,
            status=status,
            content=accumulated_content,
            updated_at=updated_at,
            provider_message_id=provider_message_id,
            usage=usage,
            error_code=error_code,
            error_message=error_message,
        )

    def cancel_if_requested():
        if not convex_sync.is_generation_cancel_requested(context.generation_id):
            return
        completed_at = _now_ms()
        convex_sync.mark_generation_cancelled(
            context.generation_id,
            completed_at,
            content=accumulated_content,
        )
        publish_terminal(status="cancelled", updated_at=completed_at)
        logger.info(
            "chat_generation_terminal generation_id=%s thread_id=%s user_id=%s terminal_status=cancelled event_count=%s chars=%s",
            context.generation_id,
            context.thread_id,
            context.user_id,
            event_count,
            len(accumulated_content),
        )
        raise _GenerationCancelled()

    cancel_if_requested()

    started_at = _now_ms()
    streaming_state = convex_sync.mark_generation_streaming(
        context.generation_id,
        started_at,
        provider=provider_name,
        model=model_name,
    )
    if isinstance(streaming_state, dict) and not streaming_state.get("accepted", True):
        existing_status = str(streaming_state.get("status", context.status))
        logger.info(
            "chat_generation_streaming_noop generation_id=%s status=%s",
            context.generation_id,
            existing_status,
        )
        return GenerationRunResult(
            generation_id=context.generation_id,
            status=existing_status,
            characters_streamed=len(accumulated_content),
            event_count=event_count,
        )

    live_stream.initialize_live_state(
        context.generation_id,
        status="streaming",
        content=accumulated_content,
        provider=provider_name,
        model=model_name,
        updated_at=started_at,
        user_id=context.user_id,
    )

    heartbeat_stop = threading.Event()

    # Single background worker for all async I/O tasks (delta writes + heartbeats).
    # Keeps thread count fixed at 2 per generation regardless of traffic.
    worker_queue: queue.Queue = queue.Queue()

    def background_worker():
        while True:
            task = worker_queue.get()
            if task is None:
                break
            try:
                task()
            except Exception as e:
                logger.warning(
                    "chat_background_task_failed generation_id=%s error=%s",
                    context.generation_id,
                    str(e),
                )

    worker_thread = threading.Thread(
        target=background_worker,
        daemon=True,
        name=f"chat-worker-{context.generation_id}",
    )
    worker_thread.start()

    def send_heartbeat():
        now = _now_ms()
        content_snapshot = accumulated_content
        activity_snapshot = last_activity
        text_at_snapshot = last_text_at

        worker_queue.put(lambda: convex_sync.heartbeat_generation(
            context.generation_id,
            now,
            last_text_at=text_at_snapshot if content_snapshot else None,
            activity=activity_snapshot,
        ))
        worker_queue.put(lambda: live_stream.touch_live_state(
            context.generation_id,
            active=True,
            content=content_snapshot,
            updated_at=now,
        ))

        # Check for cancellation synchronously (quick Convex query)
        cancel_if_requested()

    def heartbeat_loop():
        interval_seconds = max(Config.CHAT_CONVEX_HEARTBEAT_MS / 1000.0, 1.0)
        while not heartbeat_stop.wait(interval_seconds):
            send_heartbeat()

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        daemon=True,
        name=f"chat-heartbeat-{context.generation_id}",
    )
    heartbeat_thread.start()

    try:
        provider_messages = build_provider_messages(context)
    except ValueError as exc:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=0.1)
        worker_queue.put(None)
        worker_thread.join(timeout=0.1)
        completed_at = _now_ms()
        error_message = _truncate_error(str(exc))
        convex_sync.mark_generation_failed(
            context.generation_id,
            "invalid_prompt_state",
            error_message,
            completed_at,
            content=accumulated_content,
        )
        publish_terminal(
            status="failed",
            updated_at=completed_at,
            error_code="invalid_prompt_state",
            error_message=error_message,
        )
        raise ChatContractError(error_message) from exc

    def on_delta(delta):
        nonlocal accumulated_content, event_count, first_token_seen, last_activity, last_text_at

        accumulated_content += delta.content
        last_text_at = _now_ms()
        last_activity = "streaming_text"

        # Enqueue Redis write through the shared background worker
        token_content = delta.content
        token_at = last_text_at
        worker_queue.put(lambda: live_stream.append_delta(
            context.generation_id,
            delta=token_content,
            status="streaming",
            updated_at=token_at,
        ))
        event_count += 1

        if not first_token_seen:
            first_token_seen = True
            logger.info(
                "chat_generation_first_token generation_id=%s thread_id=%s user_id=%s",
                context.generation_id,
                context.thread_id,
                context.user_id,
            )

    try:
        provider_completion = stream_chat_completion(
            provider_messages,
            model=model_name,
            on_delta=on_delta,
        )
        cancel_if_requested()
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=0.1)
        worker_queue.put(None)
        worker_thread.join(timeout=2.0)
        completed_at = _now_ms()
        convex_sync.mark_generation_completed(
            context.generation_id,
            accumulated_content,
            completed_at,
            provider_message_id=provider_completion.provider_message_id,
            usage=provider_completion.usage,
        )
        publish_terminal(
            status="completed",
            updated_at=completed_at,
            provider_message_id=provider_completion.provider_message_id,
            usage=provider_completion.usage,
        )
        logger.info(
            "chat_generation_terminal generation_id=%s thread_id=%s user_id=%s terminal_status=completed event_count=%s chars=%s finish_reason=%s",
            context.generation_id,
            context.thread_id,
            context.user_id,
            event_count,
            len(accumulated_content),
            provider_completion.finish_reason,
        )
        return GenerationRunResult(
            generation_id=context.generation_id,
            status="completed",
            characters_streamed=len(accumulated_content),
            event_count=event_count,
            provider_message_id=provider_completion.provider_message_id,
            usage=provider_completion.usage,
        )
    except _GenerationCancelled:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=0.1)
        worker_queue.put(None)
        worker_thread.join(timeout=2.0)
        return GenerationRunResult(
            generation_id=context.generation_id,
            status="cancelled",
            characters_streamed=len(accumulated_content),
            event_count=event_count,
        )
    except Exception as exc:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=0.1)
        worker_queue.put(None)
        worker_thread.join(timeout=2.0)
        completed_at = _now_ms()
        error_code = _error_code_for_exception(exc)
        error_message = _truncate_error(str(exc))
        convex_sync.mark_generation_failed(
            context.generation_id,
            error_code,
            error_message,
            completed_at,
            content=accumulated_content,
        )
        publish_terminal(
            status="failed",
            updated_at=completed_at,
            error_code=error_code,
            error_message=error_message,
        )
        logger.warning(
            "chat_generation_terminal generation_id=%s thread_id=%s user_id=%s terminal_status=failed event_count=%s chars=%s error_class=%s",
            context.generation_id,
            context.thread_id,
            context.user_id,
            event_count,
            len(accumulated_content),
            exc.__class__.__name__,
        )
        if isinstance(exc, ChatProviderError):
            raise ChatConfigurationError(error_message) from exc
        if isinstance(exc, ChatContractError):
            raise
        raise


def _validate_chat_configuration():
    if not Config.CONVEX_ADMIN_KEY:
        raise ChatConfigurationError("CONVEX_ADMIN_KEY is not configured")
    if not Config.CHAT_INTERNAL_SECRET:
        raise ChatConfigurationError("CHAT_INTERNAL_SECRET is not configured")
    if not Config.LLM_API_KEY:
        raise ChatConfigurationError("LLM_API_KEY is not configured")
    if not Config.LLM_MODEL:
        raise ChatConfigurationError("LLM_MODEL is not configured")
    if not Config.UPSTASH_REDIS_URL:
        raise ChatConfigurationError("UPSTASH_REDIS_URL is not configured")


def _provider_name() -> str:
    base_url = Config.LLM_BASE_URL.rstrip("/").lower()
    if "openrouter" in base_url:
        return "openrouter"
    return "openai_compatible"


def _error_code_for_exception(exc: Exception) -> str:
    if isinstance(exc, ChatProviderError):
        return "provider_error"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, ChatContractError):
        return "contract_error"
    return "internal_error"


def _truncate_error(message: str) -> str:
    cleaned = " ".join(message.split())
    if len(cleaned) <= 240:
        return cleaned
    return cleaned[:237] + "..."


def _now_ms() -> int:
    return int(time.time() * 1000)
