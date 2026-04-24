"""
OpenRouter/OpenAI-compatible provider wrapper.
"""
from typing import Any, Callable, Iterable

import httpx
from openai import OpenAI

from config import Config

from .types import ProviderRoundResult, ProviderToolCall


class ChatProviderError(RuntimeError):
    """Raised when provider setup or streaming fails."""


_client_cache: OpenAI | None = None
_client_cache_key: tuple | None = None


def stream_responses_round(
    *,
    instructions: str,
    input_items: list[dict[str, Any]],
    tools: Iterable[dict[str, Any]] | None,
    model: str,
    on_text_delta: Callable[[str], None],
    on_tool_call_added: Callable[[str, str | None, str | None], None] | None = None,
    on_tool_call_delta: Callable[[str, str], None] | None = None,
    on_tool_call_done: Callable[[str, str, str], None] | None = None,
) -> ProviderRoundResult:
    client = _get_client()
    tool_items: dict[str, dict[str, str | None]] = {}

    try:
        with client.responses.stream(
            model=model,
            instructions=instructions,
            input=input_items,
            tools=list(tools or []),
            parallel_tool_calls=False,
            truncation="disabled",
        ) as stream:
            for event in stream:
                if event.type == "response.output_text.delta":
                    on_text_delta(event.delta)
                    continue

                if event.type == "response.output_item.added" and getattr(event.item, "type", None) == "function_call":
                    item_id = getattr(event.item, "id", None) or getattr(event.item, "call_id", None)
                    if not item_id:
                        continue
                    tool_items[item_id] = {
                        "call_id": getattr(event.item, "call_id", None),
                        "name": getattr(event.item, "name", None),
                    }
                    if on_tool_call_added:
                        on_tool_call_added(
                            item_id,
                            tool_items[item_id]["call_id"],
                            tool_items[item_id]["name"],
                        )
                    continue

                if event.type == "response.function_call_arguments.delta":
                    if on_tool_call_delta:
                        on_tool_call_delta(event.item_id, event.delta)
                    continue

                if event.type == "response.function_call_arguments.done":
                    tool_items.setdefault(event.item_id, {})
                    tool_items[event.item_id]["name"] = event.name
                    if on_tool_call_done:
                        on_tool_call_done(event.item_id, event.name, event.arguments)

            final_response = stream.get_final_response()
    except Exception as exc:
        raise ChatProviderError(f"Provider stream failed: {exc}") from exc

    function_calls: list[ProviderToolCall] = []
    output_items: list[dict[str, Any]] = []
    for item in getattr(final_response, "output", []) or []:
        dumped = item.model_dump(exclude_none=True) if hasattr(item, "model_dump") else dict(item)
        output_items.append(dumped)
        if getattr(item, "type", None) != "function_call":
            continue
        item_id = getattr(item, "id", None) or getattr(item, "call_id", None)
        call_id = getattr(item, "call_id", None)
        name = getattr(item, "name", None)
        arguments = getattr(item, "arguments", None)
        if not item_id or not call_id or not name or arguments is None:
            continue
        function_calls.append(
            ProviderToolCall(
                item_id=item_id,
                call_id=call_id,
                name=name,
                arguments=arguments,
            )
        )

    usage = _usage_to_dict(getattr(final_response, "usage", None))
    return ProviderRoundResult(
        response_id=getattr(final_response, "id", None),
        output=output_items,
        output_text=getattr(final_response, "output_text", "") or "",
        function_calls=function_calls,
        usage=usage,
        status=getattr(final_response, "status", None),
    )


def stream_chat_completion(
    messages: list[dict[str, str]],
    *,
    model: str,
    on_delta: Callable[[Any], None],
) -> Any:
    raise NotImplementedError("stream_chat_completion has been replaced by stream_responses_round")


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
