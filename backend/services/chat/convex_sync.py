"""
Chat-specific Convex sync helpers using admin auth.
"""
import os
import queue
import threading
from typing import Any, Callable

from convex import ConvexClient

from config import Config

CONVEX_CALL_TIMEOUT_SECONDS = float(os.environ.get("CONVEX_CALL_TIMEOUT_SECONDS", "8"))
_POOL_SIZE = 5

_pool: queue.Queue[ConvexClient] = queue.Queue()
_pool_lock = threading.Lock()
_pool_ready = False


def _ensure_pool() -> queue.Queue[ConvexClient]:
    global _pool_ready
    if _pool_ready:
        return _pool
    with _pool_lock:
        if not _pool_ready:
            if not Config.CONVEX_ADMIN_KEY:
                raise RuntimeError("CONVEX_ADMIN_KEY is not configured")
            for _ in range(_POOL_SIZE):
                c = ConvexClient(Config.CONVEX_URL)
                c.set_admin_auth(Config.CONVEX_ADMIN_KEY)
                _pool.put(c)
            _pool_ready = True
    return _pool


def _run_with_timeout(operation: str, callback: Callable[[ConvexClient], Any]) -> Any:
    pool = _ensure_pool()

    try:
        client = pool.get(timeout=CONVEX_CALL_TIMEOUT_SECONDS)
    except queue.Empty as exc:
        raise TimeoutError(
            f"No Convex client available after {CONVEX_CALL_TIMEOUT_SECONDS}s ({operation})"
        ) from exc

    result_queue: queue.Queue[tuple[bool, Any]] = queue.Queue(maxsize=1)

    def runner():
        try:
            result_queue.put((True, callback(client)))
        except Exception as exc:
            result_queue.put((False, exc))
        finally:
            pool.put(client)

    thread = threading.Thread(
        target=runner,
        daemon=True,
        name=f"convex-chat-{operation.replace(':', '-')}",
    )
    thread.start()

    try:
        success, payload = result_queue.get(timeout=CONVEX_CALL_TIMEOUT_SECONDS)
    except queue.Empty as exc:
        # Thread is still running and will return the client to the pool when done.
        raise TimeoutError(
            f"Convex chat call timed out after {CONVEX_CALL_TIMEOUT_SECONDS}s ({operation})"
        ) from exc

    if success:
        return payload
    raise payload


def _query(name: str, args: dict[str, Any]) -> Any:
    return _run_with_timeout(
        f"query {name}",
        lambda client: client.query(name, args),
    )


def _mutation(name: str, args: dict[str, Any]) -> Any:
    return _run_with_timeout(
        f"mutation {name}",
        lambda client: client.mutation(name, args),
    )


def get_generation_context(generation_id: str) -> dict[str, Any] | None:
    return _query("chatInternal:getGenerationContext", {"generationId": generation_id})


def is_generation_cancel_requested(generation_id: str) -> bool:
    result = _query("chatInternal:getGenerationCancelState", {"generationId": generation_id})
    if isinstance(result, dict):
        return bool(result.get("cancelRequested", False))
    return bool(result)


def mark_generation_streaming(
    generation_id: str,
    started_at: int,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "generationId": generation_id,
        "startedAt": started_at,
    }
    if provider:
        payload["provider"] = provider
    if model:
        payload["model"] = model
    return _mutation("chatInternal:markGenerationStreaming", payload)


def heartbeat_generation(
    generation_id: str,
    updated_at: int,
    *,
    last_text_at: int | None = None,
    activity: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "generationId": generation_id,
        "updatedAt": updated_at,
    }
    if last_text_at is not None:
        payload["lastTextAt"] = last_text_at
    if activity:
        payload["activity"] = activity
    return _mutation("chatInternal:heartbeatGeneration", payload)


def mark_generation_completed(
    generation_id: str,
    content: str,
    completed_at: int,
    provider_message_id: str | None = None,
    usage: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "generationId": generation_id,
        "content": content,
        "completedAt": completed_at,
    }
    if provider_message_id:
        payload["providerMessageId"] = provider_message_id
    if usage is not None:
        payload["usage"] = usage
    return _mutation("chatInternal:markGenerationCompleted", payload)


def mark_generation_failed(
    generation_id: str,
    error_code: str,
    error_message: str,
    completed_at: int,
    *,
    content: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "generationId": generation_id,
        "errorCode": error_code,
        "errorMessage": error_message,
        "completedAt": completed_at,
    }
    if content is not None:
        payload["content"] = content
    return _mutation("chatInternal:markGenerationFailed", payload)


def mark_generation_cancelled(
    generation_id: str,
    completed_at: int,
    *,
    content: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "generationId": generation_id,
        "completedAt": completed_at,
    }
    if content is not None:
        payload["content"] = content
    return _mutation("chatInternal:markGenerationCancelled", payload)
