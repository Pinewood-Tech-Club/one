"""
OpenRouter/OpenAI-compatible provider wrapper.
"""
from typing import Any, Callable

import httpx
from openai import OpenAI

from config import Config

from .types import ProviderCompletion, StreamDelta


class ChatProviderError(RuntimeError):
    """Raised when provider setup or streaming fails."""


_client_cache: OpenAI | None = None
_client_cache_key: tuple | None = None


def stream_chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str,
    on_delta: Callable[[StreamDelta], None],
) -> ProviderCompletion:
    client = _get_client()
    provider_message_id: str | None = None
    finish_reason: str | None = None
    usage: dict[str, Any] | None = None

    try:
        stream = client.chat.completions.create(
            model=model,
            messages=messages,
            stream=True,
        )
    except Exception as exc:
        raise ChatProviderError(f"Failed to start provider stream: {exc}") from exc

    try:
        for chunk in stream:
            if provider_message_id is None:
                provider_message_id = getattr(chunk, "id", None)

            usage_payload = getattr(chunk, "usage", None)
            if usage_payload is not None:
                usage = _usage_to_dict(usage_payload)

            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue

            choice = choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason

            delta = getattr(choice, "delta", None)
            delta_content = _coerce_delta_content(getattr(delta, "content", None))
            if delta_content:
                on_delta(StreamDelta(content=delta_content))
    except Exception as exc:
        raise ChatProviderError(f"Provider stream failed: {exc}") from exc

    return ProviderCompletion(
        provider_message_id=provider_message_id,
        finish_reason=finish_reason,
        usage=usage,
    )


def _get_client() -> OpenAI:
    global _client_cache, _client_cache_key

    if not Config.LLM_API_KEY:
        raise ChatProviderError("LLM_API_KEY is not configured")

    if not Config.LLM_MODEL:
        raise ChatProviderError("LLM_MODEL is not configured")

    cache_key = (Config.LLM_BASE_URL, Config.LLM_API_KEY, Config.LLM_CONNECT_TIMEOUT_SECONDS, Config.LLM_IDLE_TIMEOUT_SECONDS)
    if _client_cache is not None and _client_cache_key == cache_key:
        return _client_cache

    timeout = httpx.Timeout(
        timeout=None,
        connect=Config.LLM_CONNECT_TIMEOUT_SECONDS,
        read=Config.LLM_IDLE_TIMEOUT_SECONDS,
        write=Config.LLM_IDLE_TIMEOUT_SECONDS,
    )

    _client_cache = OpenAI(
        base_url=Config.LLM_BASE_URL,
        api_key=Config.LLM_API_KEY,
        timeout=timeout,
    )
    _client_cache_key = cache_key
    return _client_cache


def _coerce_delta_content(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        text_parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                text = item.get("text")
                if isinstance(text, str):
                    text_parts.append(text)
            else:
                text = getattr(item, "text", None)
                if isinstance(text, str):
                    text_parts.append(text)
        return "".join(text_parts)
    return ""


def _usage_to_dict(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if hasattr(value, "model_dump"):
        return value.model_dump(exclude_none=True)
    if isinstance(value, dict):
        return value
    return {
        key: getattr(value, key)
        for key in dir(value)
        if not key.startswith("_") and isinstance(getattr(value, key), (str, int, float, bool, dict, list, type(None)))
    }
