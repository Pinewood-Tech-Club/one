"""
Thin client for the TinyFish web APIs (Search and Fetch).

Both endpoints authenticate with a single account API key passed in the
``X-API-Key`` header and are free (no credits). We talk to the REST endpoints
directly with ``requests`` rather than the ``tinyfish`` SDK to avoid a new
dependency and a Python 3.11+ floor, matching how ``services/schoology/client.py``
already makes outbound HTTP calls.

Docs: https://docs.tinyfish.ai/fetch-api/reference and .../search-api/reference
"""

from __future__ import annotations

from typing import Any

import requests

from config import Config

FETCH_ENDPOINT = "https://api.fetch.tinyfish.ai"
SEARCH_ENDPOINT = "https://api.search.tinyfish.ai"

# TinyFish enforces a 110s per-URL backend timeout, but chat generations have a
# much tighter deadline, so we cap each fetch well below that and keep the
# client-side timeout in the same range.
_FETCH_PER_URL_TIMEOUT_MS = 45_000
_FETCH_CLIENT_TIMEOUT_SECONDS = 55.0
_SEARCH_CLIENT_TIMEOUT_SECONDS = 20.0

_MAX_FETCH_URLS = 10  # TinyFish hard limit per request.


class TinyFishError(RuntimeError):
    """Raised when a TinyFish request fails."""


class TinyFishNotConfigured(TinyFishError):
    """Raised when no TINYFISH_API_KEY is configured."""


def _headers() -> dict[str, str]:
    key = Config.TINYFISH_API_KEY
    if not key:
        raise TinyFishNotConfigured("TINYFISH_API_KEY is not configured; set it in backend/.env")
    return {"X-API-Key": key}


def _error_message(resp: requests.Response) -> str:
    """Pull a human-readable message out of a TinyFish error response."""
    try:
        payload = resp.json()
    except ValueError:
        return f"HTTP {resp.status_code}"
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        return error.get("message") or error.get("code") or f"HTTP {resp.status_code}"
    if isinstance(error, str):
        return error
    return f"HTTP {resp.status_code}"


def fetch_urls(urls: list[str], *, fmt: str = "markdown") -> dict[str, Any]:
    """Fetch and extract the contents of up to 10 URLs.

    Returns the raw TinyFish payload: ``{"results": [...], "errors": [...]}``.
    Per-URL failures are reported in ``errors`` alongside a 200 response; only
    request-level failures raise :class:`TinyFishError`.
    """
    if not urls:
        raise TinyFishError("fetch_urls requires at least one URL")
    if len(urls) > _MAX_FETCH_URLS:
        raise TinyFishError(f"fetch_urls accepts at most {_MAX_FETCH_URLS} URLs")

    body = {
        "urls": urls,
        "format": fmt,
        "links": False,
        "image_links": False,
        "per_url_timeout_ms": _FETCH_PER_URL_TIMEOUT_MS,
    }
    try:
        resp = requests.post(
            FETCH_ENDPOINT,
            headers={**_headers(), "Content-Type": "application/json"},
            json=body,
            timeout=_FETCH_CLIENT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise TinyFishError(f"TinyFish fetch request failed: {exc}") from exc

    if resp.status_code != 200:
        raise TinyFishError(f"TinyFish fetch error: {_error_message(resp)}")
    return resp.json()


def search(query: str, *, page: int = 0, **params: Any) -> dict[str, Any]:
    """Run a web search and return the raw TinyFish payload.

    ``params`` may include any supported query parameter (``location``,
    ``language``, ``domain_type``, ``recency_minutes``, ``purpose`` ...).
    """
    if not query or not query.strip():
        raise TinyFishError("search requires a non-empty query")

    query_params: dict[str, Any] = {"query": query, "page": page}
    query_params.update({k: v for k, v in params.items() if v is not None})
    try:
        resp = requests.get(
            SEARCH_ENDPOINT,
            headers=_headers(),
            params=query_params,
            timeout=_SEARCH_CLIENT_TIMEOUT_SECONDS,
        )
    except requests.RequestException as exc:
        raise TinyFishError(f"TinyFish search request failed: {exc}") from exc

    if resp.status_code != 200:
        raise TinyFishError(f"TinyFish search error: {_error_message(resp)}")
    return resp.json()
