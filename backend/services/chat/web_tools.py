"""
Web chat tools backed by TinyFish (Fetch + Search).

Unlike the Schoology tools, these need no Schoology connection, so they are
offered to every chat generation. They let the assistant read external links
the user shares (e.g. a course catalog PDF) and search the public web.
"""

from __future__ import annotations

import json
from typing import Any

from services import tinyfish
from services.chat.schoology_tools import ToolExecutionResult

# Names dispatched to this module by the chat tool loop.
WEB_TOOL_NAMES = frozenset({"fetch_url", "web_search"})

# Keep tool output bounded so a large page/PDF can't blow the model context.
_PER_URL_CHAR_CAP = 15_000
_MAX_URLS_PER_CALL = 5
_DEFAULT_SEARCH_RESULTS = 5
_MAX_SEARCH_RESULTS = 10


def get_web_tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "name": "fetch_url",
            "description": (
                "Fetch and read the contents of one or more public web pages or "
                "PDF documents by URL, returning clean text. Use this whenever the "
                "user shares a link, or when a Schoology item references an external "
                "URL (e.g. a course catalog, syllabus, or Google Doc) whose contents "
                "you need to read. Only works on public URLs that require no login."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": ("1-5 absolute http(s) URLs to fetch and read."),
                    }
                },
                "required": ["urls"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "web_search",
            "description": (
                "Search the public web and return ranked results (title, URL, "
                "snippet). Use this when you need current information or to find a "
                "page or URL you don't already have. Follow up with fetch_url to read "
                "a result's full contents."
            ),
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The web search query.",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": (
                            f"Maximum results to return. Defaults to "
                            f"{_DEFAULT_SEARCH_RESULTS}, max {_MAX_SEARCH_RESULTS}."
                        ),
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    ]


def execute_web_tool(tool_name: str, arguments: dict[str, Any]) -> ToolExecutionResult:
    if tool_name == "fetch_url":
        return _fetch_url(arguments)
    if tool_name == "web_search":
        return _web_search(arguments)
    raise tinyfish.TinyFishError(f"Unknown web tool: {tool_name}")


def _empty_result(output_text: str, summary_text: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        output_text=output_text,
        summary_text=summary_text,
        course_ids=set(),
        assignment_handles=set(),
        document_handles=set(),
    )


def _fetch_url(arguments: dict[str, Any]) -> ToolExecutionResult:
    raw_urls = arguments.get("urls")
    if not isinstance(raw_urls, list):
        raise tinyfish.TinyFishError("fetch_url requires a 'urls' array")
    urls = [u.strip() for u in raw_urls if isinstance(u, str) and u.strip()]
    if not urls:
        raise tinyfish.TinyFishError("fetch_url requires at least one URL")
    if len(urls) > _MAX_URLS_PER_CALL:
        urls = urls[:_MAX_URLS_PER_CALL]

    payload = tinyfish.fetch_urls(urls)
    results = payload.get("results") or []
    errors = payload.get("errors") or []

    blocks: list[str] = []
    for item in results:
        title = item.get("title") or item.get("final_url") or item.get("url") or ""
        source = item.get("final_url") or item.get("url") or ""
        text = item.get("text")
        if not isinstance(text, str):
            text = json.dumps(text, ensure_ascii=True)
        truncated = len(text) > _PER_URL_CHAR_CAP
        if truncated:
            text = text[:_PER_URL_CHAR_CAP].rstrip() + "\n\n[...truncated...]"
        header = f"# {title}".rstrip()
        blocks.append(f"{header}\nSource: {source}\n\n{text}")

    for err in errors:
        blocks.append(
            f"# Failed to fetch {err.get('url', '')}\nError: {err.get('error', 'unknown')}"
        )

    if not blocks:
        output_text = "No content could be fetched from the provided URL(s)."
    else:
        output_text = "\n\n---\n\n".join(blocks)

    summary = f"fetch_url: {len(results)} of {len(urls)} URL(s) fetched" + (
        f", {len(errors)} failed" if errors else ""
    )
    return _empty_result(output_text, summary)


def _web_search(arguments: dict[str, Any]) -> ToolExecutionResult:
    query = arguments.get("query")
    if not isinstance(query, str) or not query.strip():
        raise tinyfish.TinyFishError("web_search requires a non-empty 'query'")
    query = query.strip()

    max_results = arguments.get("max_results")
    if not isinstance(max_results, int) or max_results <= 0:
        max_results = _DEFAULT_SEARCH_RESULTS
    max_results = min(max_results, _MAX_SEARCH_RESULTS)

    payload = tinyfish.search(query)
    results = payload.get("results") or []

    trimmed: list[dict[str, Any]] = []
    for item in results[:max_results]:
        entry = {
            "position": item.get("position"),
            "title": item.get("title"),
            "url": item.get("url"),
            "snippet": item.get("snippet"),
            "site_name": item.get("site_name"),
        }
        if item.get("date"):
            entry["date"] = item["date"]
        trimmed.append(entry)

    output_text = json.dumps({"query": query, "results": trimmed}, ensure_ascii=True)
    summary = f"web_search: {len(trimmed)} result(s) for {query!r}"
    return _empty_result(output_text, summary)
