"""
Chat-specific Convex sync helpers over the HTTP Functions API.
"""
import os
from typing import Any

import requests

from config import Config

CONVEX_CALL_TIMEOUT_SECONDS = float(os.environ.get("CONVEX_CALL_TIMEOUT_SECONDS", "8"))


def _call_action(name: str, args: dict[str, Any]) -> Any:
    secret = Config.CHAT_INTERNAL_SECRET
    if not secret:
        raise RuntimeError("CHAT_INTERNAL_SECRET is not configured")

    payload = {
        "path": f"chatBridge:{name}",
        "args": {
            "secret": secret,
            **args,
        },
        "format": "json",
    }

    response = requests.post(
        f"{Config.CONVEX_URL.rstrip('/')}/api/action",
        json=payload,
        timeout=CONVEX_CALL_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("status") != "success":
        raise RuntimeError(body.get("errorMessage", f"Convex action failed: {name}"))
    return body.get("value")


def get_generation_context(generation_id: str) -> dict[str, Any] | None:
    return _call_action("getGenerationContext", {"generationId": generation_id})


def is_generation_cancel_requested(generation_id: str) -> bool:
    result = _call_action("getGenerationCancelState", {"generationId": generation_id})
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
    return _call_action("markGenerationStreaming", payload)


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
    return _call_action("heartbeatGeneration", payload)


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
    return _call_action("markGenerationCompleted", payload)


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
    return _call_action("markGenerationFailed", payload)


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
    return _call_action("markGenerationCancelled", payload)
