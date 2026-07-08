"""
Chat generation orchestration.
"""
import json
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

from config import Config
from db import chat_store

from . import live_stream
from .prompting import build_responses_request
from .provider import ChatProviderError, stream_responses_round
from .schoology_tools import execute_tool, get_tool_definitions
from .types import GenerationContext

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"completed", "failed", "cancelled"}

_TITLE_MODEL = "anthropic/claude-haiku-4-5"
_TITLE_SYSTEM_PROMPT = (
    "You generate concise chat thread titles. "
    "Reply with ONLY the title — no quotes, no punctuation at the end, no explanation. "
    "Maximum 32 characters. Aim for 3-5 words. "
    "Be specific: include the subject, chapter, unit, or topic name when mentioned. "
    "Good: 'Chem Ch. 17 Test Help', 'AP Bio Unit 4 Review', 'Calc Derivatives HW'. "
    "Bad: 'Chemistry Test Study Help', 'Biology Review', 'Math Homework'."
)
_TITLE_USER_TEMPLATE = (
    "Generate a short title for a conversation that starts with this message:\n\n{message}"
)


class ChatConfigurationError(RuntimeError):
    """Raised when required chat configuration is missing."""


class ChatGenerationNotFoundError(RuntimeError):
    """Raised when the requested generation record does not exist."""


class ChatContractError(RuntimeError):
    """Raised when the generation-context contract is violated."""


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


def _generate_title(user_message: str) -> str | None:
    from openai import OpenAI
    try:
        client = OpenAI(
            base_url=Config.LLM_BASE_URL,
            api_key=Config.LLM_API_KEY,
            timeout=10.0,
        )
        response = client.chat.completions.create(
            model=_TITLE_MODEL,
            messages=[
                {"role": "system", "content": _TITLE_SYSTEM_PROMPT},
                {"role": "user", "content": _TITLE_USER_TEMPLATE.format(message=user_message[:500])},
            ],
            max_tokens=24,
            temperature=0.3,
        )
        raw = response.choices[0].message.content or ""
        title = raw.strip().strip('"\'').strip()
        return title[:32] if title else None
    except Exception as exc:
        logger.warning("chat_title_generation_failed error=%s", exc)
        return None


def _maybe_spawn_title_thread(context: GenerationContext) -> None:
    user_messages = [m for m in context.transcript if m.role == "user"]
    if len(user_messages) != 1:
        return
    user_content = user_messages[0].content
    if not user_content:
        return
    thread_id = context.thread_id

    def _worker():
        title = _generate_title(user_content)
        if not title:
            return
        try:
            chat_store.update_thread_title(thread_id, title)
            logger.info("chat_title_generated thread_id=%s title=%r", thread_id, title)
        except Exception as exc:
            logger.warning("chat_title_update_failed thread_id=%s error=%s", thread_id, exc)

    threading.Thread(target=_worker, daemon=True, name=f"chat-title-{thread_id}").start()


def run_generation(generation_id: str) -> GenerationRunResult:
    _validate_chat_configuration()

    raw_context = chat_store.get_generation_context(generation_id)
    if raw_context is None:
        raise ChatGenerationNotFoundError(f"Generation {generation_id} was not found")

    try:
        context = GenerationContext.from_payload(generation_id, raw_context)
    except ValueError as exc:
        raise ChatContractError(str(exc)) from exc

    provider_name = context.provider or _provider_name()
    model_name = context.model or Config.LLM_MODEL
    accumulated_content = context.assistant_message_content
    event_count = 0
    first_token_seen = False
    last_text_at = context.started_at or context.updated_at or _now_ms()
    last_activity = "thinking"
    cancel_flag = threading.Event()
    tool_trace = _ToolTraceAccumulator()
    tool_call_sequences: dict[str, int] = {
        item.call_id: item.sequence for item in context.tool_calls
    }
    next_tool_sequence = (max(tool_call_sequences.values()) + 1) if tool_call_sequences else 1
    deadline_at = time.monotonic() + 90.0
    tool_budget = 24

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
        if not cancel_flag.is_set() and not chat_store.is_cancel_requested(context.generation_id):
            return
        completed_at = _now_ms()
        chat_store.mark_generation_cancelled(
            context.generation_id,
            completed_at,
            content=accumulated_content,
            tool_trace_summary=tool_trace.summary_text(),
            tool_trace_stats=tool_trace.trace_stats(),
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
    streaming_state = chat_store.mark_generation_streaming(
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

    _maybe_spawn_title_thread(context)

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

        # A terminal-state ChatStateError here is caught and logged by background_worker.
        worker_queue.put(lambda: chat_store.heartbeat_generation(
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
        worker_queue.put(lambda: cancel_flag.set() if chat_store.is_cancel_requested(context.generation_id) else None)

    def heartbeat_loop():
        interval_seconds = max(Config.CHAT_HEARTBEAT_MS / 1000.0, 1.0)
        while not heartbeat_stop.wait(interval_seconds):
            send_heartbeat()

    heartbeat_thread = threading.Thread(
        target=heartbeat_loop,
        daemon=True,
        name=f"chat-heartbeat-{context.generation_id}",
    )
    heartbeat_thread.start()

    try:
        instructions, input_items = build_responses_request(context)
    except ValueError as exc:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=0.1)
        worker_queue.put(None)
        worker_thread.join(timeout=0.1)
        completed_at = _now_ms()
        error_message = _truncate_error(str(exc))
        chat_store.mark_generation_failed(
            context.generation_id,
            "invalid_prompt_state",
            error_message,
            completed_at,
            content=accumulated_content,
            tool_trace_summary=tool_trace.summary_text(),
            tool_trace_stats=tool_trace.trace_stats(),
        )
        publish_terminal(
            status="failed",
            updated_at=completed_at,
            error_code="invalid_prompt_state",
            error_message=error_message,
        )
        raise ChatContractError(error_message) from exc

    def on_text_delta(delta_text: str):
        nonlocal accumulated_content, event_count, first_token_seen, last_activity, last_text_at

        accumulated_content += delta_text
        last_text_at = _now_ms()
        last_activity = "streaming_text"

        # Enqueue Redis write through the shared background worker
        token_content = delta_text
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
        round_count = 0
        while True:
            cancel_if_requested()
            if time.monotonic() >= deadline_at:
                raise ChatContractError("Tool loop exceeded time budget")
            round_count += 1

            pending_argument_buffers: dict[str, str] = {}
            pending_call_meta: dict[str, tuple[str | None, str | None]] = {}

            def on_tool_call_added(item_id: str, call_id: str | None, tool_name: str | None) -> None:
                if not call_id or not tool_name:
                    return
                nonlocal next_tool_sequence, last_activity
                sequence = tool_call_sequences.get(call_id)
                if sequence is None:
                    sequence = next_tool_sequence
                    tool_call_sequences[call_id] = sequence
                    next_tool_sequence += 1
                pending_call_meta[item_id] = (call_id, tool_name)
                last_activity = "thinking"
                created_at = _now_ms()
                worker_queue.put(
                    lambda seq=sequence, cid=call_id, name=tool_name, ts=created_at: chat_store.upsert_tool_call(
                        context.generation_id,
                        sequence=seq,
                        call_id=cid,
                        tool_name=name,
                        status="pending",
                        started_at=ts,
                    )
                )
                worker_queue.put(
                    lambda seq=sequence, cid=call_id, name=tool_name, ts=created_at: live_stream.append_event(
                        context.generation_id,
                        event_type="tool_call",
                        status="pending",
                        updated_at=ts,
                        sequence=seq,
                        callId=cid,
                        toolName=name,
                    )
                )

            def on_tool_call_delta(item_id: str, delta: str) -> None:
                call_meta = pending_call_meta.get(item_id)
                if not call_meta:
                    return
                call_id, tool_name = call_meta
                if not call_id or not tool_name:
                    return
                pending_argument_buffers[item_id] = pending_argument_buffers.get(item_id, "") + delta
                sequence = tool_call_sequences[call_id]
                updated_at = _now_ms()
                worker_queue.put(
                    lambda seq=sequence, cid=call_id, name=tool_name, args=pending_argument_buffers[item_id], ts=updated_at, delta_text=delta: live_stream.append_event(
                        context.generation_id,
                        event_type="tool_call",
                        status="pending",
                        updated_at=ts,
                        sequence=seq,
                        callId=cid,
                        toolName=name,
                        argumentsText=args,
                        argumentsDelta=delta_text,
                    )
                )

            def on_tool_call_done(item_id: str, tool_name: str, arguments: str) -> None:
                call_meta = pending_call_meta.get(item_id)
                if not call_meta:
                    return
                call_id, _ = call_meta
                if not call_id:
                    return
                pending_argument_buffers[item_id] = arguments
                sequence = tool_call_sequences[call_id]
                updated_at = _now_ms()
                worker_queue.put(
                    lambda seq=sequence, cid=call_id, name=tool_name, args=arguments, ts=updated_at: chat_store.upsert_tool_call(
                        context.generation_id,
                        sequence=seq,
                        call_id=cid,
                        tool_name=name,
                        status="pending",
                        arguments_text=args,
                        started_at=ts,
                    )
                )
                worker_queue.put(
                    lambda seq=sequence, cid=call_id, name=tool_name, args=arguments, ts=updated_at: live_stream.append_event(
                        context.generation_id,
                        event_type="tool_call",
                        status="pending",
                        updated_at=ts,
                        sequence=seq,
                        callId=cid,
                        toolName=name,
                        argumentsText=args,
                    )
                )

            round_result = stream_responses_round(
                instructions=instructions,
                input_items=input_items,
                tools=get_tool_definitions(
                    enabled=bool(context.user_record and context.user_record.schoology_connected)
                ),
                model=model_name,
                on_text_delta=on_text_delta,
                on_tool_call_added=on_tool_call_added,
                on_tool_call_delta=on_tool_call_delta,
                on_tool_call_done=on_tool_call_done,
            )
            cancel_if_requested()

            if not round_result.function_calls:
                provider_message_id = round_result.response_id
                provider_usage = round_result.usage
                break

            input_items.extend(round_result.output)
            tool_output_items: list[dict[str, Any]] = []
            if tool_trace.tool_calls_count + len(round_result.function_calls) > tool_budget:
                raise ChatContractError("Tool loop exceeded tool call budget")
            for function_call in round_result.function_calls:
                sequence = tool_call_sequences.get(function_call.call_id)
                if sequence is None:
                    sequence = next_tool_sequence
                    tool_call_sequences[function_call.call_id] = sequence
                    next_tool_sequence += 1
                started_at = _now_ms()
                last_activity = "tool_running"
                worker_queue.put(
                    lambda seq=sequence, fc=function_call, ts=started_at: chat_store.upsert_tool_call(
                        context.generation_id,
                        sequence=seq,
                        call_id=fc.call_id,
                        tool_name=fc.name,
                        status="running",
                        arguments_text=fc.arguments,
                        started_at=ts,
                    )
                )
                worker_queue.put(
                    lambda seq=sequence, fc=function_call, ts=started_at: live_stream.append_event(
                        context.generation_id,
                        event_type="tool_call",
                        status="running",
                        updated_at=ts,
                        sequence=seq,
                        callId=fc.call_id,
                        toolName=fc.name,
                        argumentsText=fc.arguments,
                    )
                )

                try:
                    parsed_arguments = _load_tool_arguments(fc=function_call)
                    execution = execute_tool(function_call.name, parsed_arguments, user_id=context.user_id)
                    completed_at = _now_ms()
                    tool_trace.record(
                        tool_name=function_call.name,
                        summary_text=execution.summary_text,
                        course_ids=execution.course_ids,
                        assignment_handles=execution.assignment_handles,
                        document_handles=execution.document_handles,
                    )
                    tool_output_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": function_call.call_id,
                            "output": execution.output_text,
                        }
                    )
                    worker_queue.put(
                        lambda seq=sequence, fc=function_call, out=execution.output_text, summary=execution.summary_text, ts=completed_at: chat_store.upsert_tool_call(
                            context.generation_id,
                            sequence=seq,
                            call_id=fc.call_id,
                            tool_name=fc.name,
                            status="completed",
                            arguments_text=fc.arguments,
                            output_text=out,
                            summary_text=summary,
                            completed_at=ts,
                        )
                    )
                    worker_queue.put(
                        lambda seq=sequence, fc=function_call, out=execution.output_text, summary=execution.summary_text, ts=completed_at: live_stream.append_event(
                            context.generation_id,
                            event_type="tool_call",
                            status="completed",
                            updated_at=ts,
                            sequence=seq,
                            callId=fc.call_id,
                            toolName=fc.name,
                            argumentsText=fc.arguments,
                            outputText=out,
                            summaryText=summary,
                        )
                    )
                except Exception as exc:
                    completed_at = _now_ms()
                    error_message = _truncate_error(str(exc) or exc.__class__.__name__)
                    error_output = json.dumps({"error": error_message}, ensure_ascii=True)
                    tool_output_items.append(
                        {
                            "type": "function_call_output",
                            "call_id": function_call.call_id,
                            "output": error_output,
                        }
                    )
                    worker_queue.put(
                        lambda seq=sequence, fc=function_call, err=error_message, out=error_output, ts=completed_at: chat_store.upsert_tool_call(
                            context.generation_id,
                            sequence=seq,
                            call_id=fc.call_id,
                            tool_name=fc.name,
                            status="failed",
                            arguments_text=fc.arguments,
                            output_text=out,
                            error_text=err,
                            completed_at=ts,
                        )
                    )
                    worker_queue.put(
                        lambda seq=sequence, fc=function_call, err=error_message, ts=completed_at: live_stream.append_event(
                            context.generation_id,
                            event_type="tool_call",
                            status="failed",
                            updated_at=ts,
                            sequence=seq,
                            callId=fc.call_id,
                            toolName=fc.name,
                            argumentsText=fc.arguments,
                            errorText=err,
                        )
                    )

                cancel_if_requested()

            input_items.extend(tool_output_items)
            last_activity = "post_tool_reasoning"

        cancel_if_requested()
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=0.1)
        worker_queue.put(None)
        worker_thread.join(timeout=2.0)
        completed_at = _now_ms()
        chat_store.mark_generation_completed(
            context.generation_id,
            accumulated_content,
            completed_at,
            provider_message_id=provider_message_id,
            usage=provider_usage,
            tool_trace_summary=tool_trace.summary_text(),
            tool_trace_stats=tool_trace.trace_stats(),
        )
        publish_terminal(
            status="completed",
            updated_at=completed_at,
            provider_message_id=provider_message_id,
            usage=provider_usage,
        )
        logger.info(
            "chat_generation_terminal generation_id=%s thread_id=%s user_id=%s terminal_status=completed event_count=%s chars=%s finish_reason=%s",
            context.generation_id,
            context.thread_id,
            context.user_id,
            event_count,
            len(accumulated_content),
            round_result.status,
        )
        return GenerationRunResult(
            generation_id=context.generation_id,
            status="completed",
            characters_streamed=len(accumulated_content),
            event_count=event_count,
            provider_message_id=provider_message_id,
            usage=provider_usage,
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
        chat_store.mark_generation_failed(
            context.generation_id,
            error_code,
            error_message,
            completed_at,
            content=accumulated_content,
            tool_trace_summary=tool_trace.summary_text(),
            tool_trace_stats=tool_trace.trace_stats(),
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


def _load_tool_arguments(*, fc) -> dict[str, object]:
    try:
        payload = json.loads(fc.arguments or "{}")
    except json.JSONDecodeError as exc:
        raise ChatContractError(f"Tool arguments were not valid JSON for {fc.name}") from exc
    if not isinstance(payload, dict):
        raise ChatContractError(f"Tool arguments must decode to an object for {fc.name}")
    return payload


class _ToolTraceAccumulator:
    def __init__(self) -> None:
        self.tool_calls_count = 0
        self.course_ids: set[str] = set()
        self.assignment_handles: set[str] = set()
        self.document_handles: set[str] = set()
        self.summary_parts: list[str] = []

    def record(
        self,
        *,
        tool_name: str,
        summary_text: str,
        course_ids: set[str],
        assignment_handles: set[str],
        document_handles: set[str],
    ) -> None:
        self.tool_calls_count += 1
        self.course_ids.update(course_ids)
        self.assignment_handles.update(assignment_handles)
        self.document_handles.update(document_handles)
        if summary_text:
            self.summary_parts.append(summary_text)
        elif tool_name:
            self.summary_parts.append(tool_name)

    def summary_text(self) -> str:
        if self.tool_calls_count == 0:
            return ""
        return (
            f"Checked {len(self.course_ids)} course{'s' if len(self.course_ids) != 1 else ''} "
            f"• Looked at {len(self.assignment_handles)} assignment{'s' if len(self.assignment_handles) != 1 else ''} "
            f"• Read {len(self.document_handles)} document{'s' if len(self.document_handles) != 1 else ''}"
        )

    def trace_stats(self) -> dict[str, int]:
        return {
            "toolCallsCount": self.tool_calls_count,
            "coursesTouched": len(self.course_ids),
            "assignmentsTouched": len(self.assignment_handles),
            "documentsTouched": len(self.document_handles),
        }
