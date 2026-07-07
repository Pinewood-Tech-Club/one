"""
Schoology chat tool definitions and execution helpers.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from itsdangerous import BadSignature, URLSafeSerializer

from config import Config
from services.schoology.runtime import create_schoology_service
from services.scraper import store as scraper_store
from services.scraper.extraction import read_extracted_attachment_text


class SchoologyToolError(RuntimeError):
    """Raised when a chat tool request is invalid or unavailable."""


@dataclass(frozen=True)
class ToolExecutionResult:
    output_text: str
    summary_text: str
    course_ids: set[str]
    assignment_handles: set[str]
    document_handles: set[str]


def get_tool_definitions(*, enabled: bool) -> list[dict[str, Any]]:
    if not enabled:
        return []

    return [
        {
            "type": "function",
            "name": "search",
            "description": "Search cached Schoology course materials using one or more query strings. Use this when the user asks what a test covers, asks for a study guide, or mentions keywords you should look up.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {
                        "type": "string",
                        "description": "Optional Schoology section ID to limit search to one course. Omit to search all active courses.",
                    },
                    "query_strings": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "One or more search terms or short phrases to look for.",
                    },
                    "max_n": {
                        "type": "integer",
                        "description": "Maximum number of results to return. Defaults to 10.",
                    },
                },
                "required": ["query_strings"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "list_upcoming_assignments",
            "description": "List upcoming assignments from Schoology. Use this for fresh due dates and near-term work.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {
                        "type": "string",
                        "description": "Optional Schoology section ID to limit results to one course.",
                    },
                    "days": {
                        "type": "integer",
                        "description": "How many days ahead to look. Defaults to 14.",
                    },
                },
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "list_items_in_course",
            "description": "List cached assignments and documents already scraped for a Schoology course. Use this to browse course materials.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "course_id": {
                        "type": "string",
                        "description": "Required Schoology section ID from the known course list.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Maximum number of items to return. Defaults to 25.",
                    },
                },
                "required": ["course_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "assignment_detail",
            "description": "Read assignment details using a previously returned assignment handle.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "handle": {
                        "type": "string",
                        "description": "Signed assignment handle returned by another Schoology tool.",
                    }
                },
                "required": ["handle"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "document_detail",
            "description": "Read cached course material details using a previously returned document or attachment handle.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "handle": {
                        "type": "string",
                        "description": "Signed document or attachment handle returned by another Schoology tool.",
                    }
                },
                "required": ["handle"],
                "additionalProperties": False,
            },
        },
    ]


def execute_tool(tool_name: str, arguments: dict[str, Any], *, user_id: str) -> ToolExecutionResult:
    backend_user_id = _coerce_user_id(user_id)
    if backend_user_id is None:
        raise SchoologyToolError("Schoology tools require a valid backend user id")

    if tool_name == "list_upcoming_assignments":
        return _list_upcoming_assignments(arguments, user_id=backend_user_id)
    if tool_name == "search":
        return _search_course_materials(arguments, user_id=backend_user_id)
    if tool_name == "list_items_in_course":
        return _list_items_in_course(arguments, user_id=backend_user_id)
    if tool_name == "assignment_detail":
        return _assignment_detail(arguments, user_id=backend_user_id)
    if tool_name == "document_detail":
        return _document_detail(arguments, user_id=backend_user_id)

    raise SchoologyToolError(f"Unknown Schoology tool: {tool_name}")


def _list_upcoming_assignments(arguments: dict[str, Any], *, user_id: int) -> ToolExecutionResult:
    service = create_schoology_service(user_id)
    if not service:
        raise SchoologyToolError("Schoology is not connected for this user")

    course_id = _optional_string(arguments.get("course_id"))
    days = _coerce_days(arguments.get("days"), default=14)
    assignments = service.get_upcoming_assignments(days=days)
    if course_id:
        _require_section_access(user_id, course_id)
        assignments = [item for item in assignments if str(item.get("section_id")) == course_id]

    items: list[dict[str, Any]] = []
    assignment_handles: set[str] = set()
    course_ids: set[str] = set()
    checked_courses_count = 1 if course_id else 0

    for assignment in assignments[:25]:
        section_id = str(assignment.get("section_id") or "")
        schoology_id = str(assignment.get("id") or assignment.get("grade_item_id") or "")
        if not section_id or not schoology_id:
            continue
        checked_courses_count = max(checked_courses_count, len(course_ids | {section_id}))
        handle = _sign_handle(
            {
                "kind": "assignment_resource",
                "section_id": section_id,
                "resource_type": "assignment",
                "schoology_id": schoology_id,
            }
        )
        assignment_handles.add(handle)
        course_ids.add(section_id)
        items.append(
            {
                "handle": handle,
                "course_id": section_id,
                "course_title": assignment.get("course_title") or assignment.get("section_title") or section_id,
                "title": assignment.get("title") or assignment.get("name") or schoology_id,
                "due_at": assignment.get("due"),
                "description_preview": _truncate_text(
                    _best_text(assignment.get("description"), assignment.get("body")),
                    280,
                ),
            }
        )

    checked_courses_count = max(checked_courses_count, len(course_ids))
    summary = f"Checked {checked_courses_count} course{'s' if checked_courses_count != 1 else ''} • Found {len(items)} upcoming assignment{'s' if len(items) != 1 else ''}"
    return ToolExecutionResult(
        output_text=json.dumps(
            {
                "course_id": course_id,
                "days": days,
                "items": items,
            },
            ensure_ascii=True,
        ),
        summary_text=summary,
        course_ids=course_ids or ({course_id} if course_id else set()),
        assignment_handles=assignment_handles,
        document_handles=set(),
    )


def _search_course_materials(arguments: dict[str, Any], *, user_id: int) -> ToolExecutionResult:
    course_id = _optional_string(arguments.get("course_id"))
    query_strings = arguments.get("query_strings")
    if not isinstance(query_strings, list):
        raise SchoologyToolError("query_strings must be an array of search terms")
    normalized_queries = [str(item).strip() for item in query_strings if str(item).strip()]
    if not normalized_queries:
        raise SchoologyToolError("query_strings must contain at least one search term")

    if course_id:
        _require_section_access(user_id, course_id)
        section_ids = [course_id]
    else:
        section_ids = [row["section_id"] for row in scraper_store.list_active_sections_for_user(user_id)]
        if not section_ids:
            raise SchoologyToolError("No active Schoology courses are available for search")

    max_n = _coerce_limit(arguments.get("max_n"), default=10, maximum=20)
    results = scraper_store.search_materials(
        section_ids=section_ids,
        query_terms=normalized_queries,
        limit=max_n,
    )

    items: list[dict[str, Any]] = []
    assignment_handles: set[str] = set()
    document_handles: set[str] = set()
    course_ids: set[str] = set()

    for result in results:
        course_ids.add(result["section_id"])
        resource_handle = _sign_handle(
            {
                "kind": "assignment_resource" if result["resource_type"] == "assignment" else "resource",
                "section_id": result["section_id"],
                "resource_type": result["resource_type"],
                "schoology_id": result["schoology_id"],
            }
        )
        if result["resource_type"] == "assignment":
            assignment_handles.add(resource_handle)
        else:
            document_handles.add(resource_handle)

        attachment_items = []
        for attachment in result.get("attachments", []):
            attachment_handle = _sign_handle(
                {
                    "kind": "attachment",
                    "section_id": result["section_id"],
                    "canonical_key": attachment["canonical_key"],
                }
            )
            attachment_items.append(
                {
                    "handle": attachment_handle,
                    "title": attachment.get("title") or attachment.get("filename") or attachment["canonical_key"],
                    "attachment_kind": attachment.get("attachment_kind"),
                    "mime_type": attachment.get("mime_type"),
                    "filename": attachment.get("filename"),
                }
            )
            document_handles.add(attachment_handle)

        items.append(
            {
                "handle": resource_handle,
                "kind": result["resource_type"],
                "course_id": result["section_id"],
                "title": result.get("title") or result["schoology_id"],
                "description_preview": result.get("description_preview"),
                "due_at": result.get("due_at"),
                "matched_queries": result.get("matched_queries") or normalized_queries,
                "attachments": attachment_items,
            }
        )

    searched_courses_count = len(section_ids)
    summary = (
        f"Searched {searched_courses_count} course{'s' if searched_courses_count != 1 else ''} "
        f"• Found {len(items)} matching item{'s' if len(items) != 1 else ''}"
    )
    return ToolExecutionResult(
        output_text=json.dumps(
            {
                "course_id": course_id,
                "query_strings": normalized_queries,
                "items": items,
            },
            ensure_ascii=True,
        ),
        summary_text=summary,
        course_ids=course_ids or set(section_ids),
        assignment_handles=assignment_handles,
        document_handles=document_handles,
    )


def _list_items_in_course(arguments: dict[str, Any], *, user_id: int) -> ToolExecutionResult:
    course_id = _required_string(arguments.get("course_id"), "course_id")
    _require_section_access(user_id, course_id)
    limit = _coerce_limit(arguments.get("limit"), default=25, maximum=50)
    resources = scraper_store.list_section_resources(course_id, limit=limit)
    items: list[dict[str, Any]] = []
    assignment_handles: set[str] = set()
    document_handles: set[str] = set()

    for resource in resources:
        handle = _sign_handle(
            {
                "kind": "assignment_resource" if resource["resource_type"] == "assignment" else "resource",
                "section_id": resource["section_id"],
                "resource_type": resource["resource_type"],
                "schoology_id": resource["schoology_id"],
            }
        )
        attachments = scraper_store.list_resource_attachments(resource["resource_id"])
        attachment_items = []
        for attachment in attachments[:4]:
            attachment_handle = _sign_handle(
                {
                    "kind": "attachment",
                    "section_id": attachment["section_id"],
                    "canonical_key": attachment["canonical_key"],
                }
            )
            attachment_items.append(
                {
                    "handle": attachment_handle,
                    "title": attachment.get("title") or attachment.get("filename") or attachment["canonical_key"],
                    "attachment_kind": attachment.get("attachment_kind"),
                    "mime_type": attachment.get("mime_type"),
                    "filename": attachment.get("filename"),
                }
            )
            document_handles.add(attachment_handle)

        item = {
            "handle": handle,
            "kind": resource["resource_type"],
            "course_id": resource["section_id"],
            "title": resource.get("title") or resource["schoology_id"],
            "description_preview": resource.get("description_preview"),
            "due_at": resource.get("due_at"),
            "attachments": attachment_items,
        }
        items.append(item)
        if resource["resource_type"] == "assignment":
            assignment_handles.add(handle)
        else:
            document_handles.add(handle)

    summary = f"Checked 1 course • Looked at {len(items)} cached item{'s' if len(items) != 1 else ''}"
    return ToolExecutionResult(
        output_text=json.dumps(
            {
                "course_id": course_id,
                "items": items,
            },
            ensure_ascii=True,
        ),
        summary_text=summary,
        course_ids={course_id},
        assignment_handles=assignment_handles,
        document_handles=document_handles,
    )


def _assignment_detail(arguments: dict[str, Any], *, user_id: int) -> ToolExecutionResult:
    handle = _required_string(arguments.get("handle"), "handle")
    payload = _load_handle(handle)
    if payload.get("kind") != "assignment_resource":
        raise SchoologyToolError("assignment_detail requires an assignment handle")

    section_id = _required_string(payload.get("section_id"), "section_id")
    schoology_id = _required_string(payload.get("schoology_id"), "schoology_id")
    _require_section_access(user_id, section_id)

    resource = scraper_store.get_section_resource(section_id, "assignment", schoology_id)
    if resource is None:
        service = create_schoology_service(user_id)
        if not service:
            raise SchoologyToolError("Schoology is not connected for this user")
        for assignment in service.get_assignments(section_id, sync_to_convex=False, with_attachments=True):
            candidate_id = str(assignment.get("id") or assignment.get("grade_item_id") or "")
            if candidate_id != schoology_id:
                continue
            detail = {
                "handle": handle,
                "course_id": section_id,
                "title": assignment.get("title") or assignment.get("name") or schoology_id,
                "due_at": assignment.get("due"),
                "description": _best_text(assignment.get("description"), assignment.get("body")),
                "source": "live_schoology",
            }
            return ToolExecutionResult(
                output_text=json.dumps(detail, ensure_ascii=True),
                summary_text=f"Read assignment details for {detail['title']}",
                course_ids={section_id},
                assignment_handles={handle},
                document_handles=set(),
            )
        raise SchoologyToolError("Assignment was not found")

    attachments = scraper_store.list_resource_attachments(resource["resource_id"])
    detail = {
        "handle": handle,
        "course_id": section_id,
        "title": resource.get("title") or schoology_id,
        "due_at": resource.get("due_at"),
        "description": _extract_resource_text(resource),
        "attachments": [
            {
                "handle": _sign_handle(
                    {
                        "kind": "attachment",
                        "section_id": attachment["section_id"],
                        "canonical_key": attachment["canonical_key"],
                    }
                ),
                "title": attachment.get("title") or attachment.get("filename") or attachment["canonical_key"],
                "attachment_kind": attachment.get("attachment_kind"),
                "mime_type": attachment.get("mime_type"),
                "filename": attachment.get("filename"),
            }
            for attachment in attachments
        ],
        "source": "scraper_cache",
    }
    return ToolExecutionResult(
        output_text=json.dumps(detail, ensure_ascii=True),
        summary_text=f"Read assignment details for {detail['title']}",
        course_ids={section_id},
        assignment_handles={handle},
        document_handles=set(),
    )


def _document_detail(arguments: dict[str, Any], *, user_id: int) -> ToolExecutionResult:
    handle = _required_string(arguments.get("handle"), "handle")
    try:
        payload = _load_handle(handle)
    except SchoologyToolError:
        payload = _resolve_pseudo_document_handle(handle, user_id=user_id)
    kind = _required_string(payload.get("kind"), "kind")

    if kind == "attachment":
        section_id = _required_string(payload.get("section_id"), "section_id")
        canonical_key = _required_string(payload.get("canonical_key"), "canonical_key")
        _require_section_access(user_id, section_id)
        attachment = scraper_store.get_attachment_by_canonical_key(canonical_key)
        if attachment is None:
            raise SchoologyToolError("Attachment was not found")
        content_text = _extract_attachment_text(attachment)
        detail = {
            "handle": handle,
            "course_id": section_id,
            "title": attachment.get("title") or attachment.get("filename") or canonical_key,
            "attachment_kind": attachment.get("attachment_kind"),
            "mime_type": attachment.get("mime_type"),
            "filename": attachment.get("filename"),
            "content_available": bool(content_text),
            "content_excerpt": content_text,
        }
        return ToolExecutionResult(
            output_text=json.dumps(detail, ensure_ascii=True),
            summary_text=f"Read document attachment {detail['title']}",
            course_ids={section_id},
            assignment_handles=set(),
            document_handles={handle},
        )

    if kind != "resource":
        raise SchoologyToolError("document_detail requires a document or attachment handle")

    section_id = _required_string(payload.get("section_id"), "section_id")
    schoology_id = _required_string(payload.get("schoology_id"), "schoology_id")
    resource_type = _required_string(payload.get("resource_type"), "resource_type")
    _require_section_access(user_id, section_id)
    resource = scraper_store.get_section_resource(section_id, resource_type, schoology_id)
    if resource is None:
        raise SchoologyToolError("Document was not found")

    attachments = scraper_store.list_resource_attachments(resource["resource_id"])
    attachment_items = []
    attachment_texts: list[str] = []
    for attachment in attachments:
        attachment_handle = _sign_handle(
            {
                "kind": "attachment",
                "section_id": attachment["section_id"],
                "canonical_key": attachment["canonical_key"],
            }
        )
        attachment_text = _extract_attachment_text(attachment)
        attachment_items.append(
            {
                "handle": attachment_handle,
                "title": attachment.get("title") or attachment.get("filename") or attachment["canonical_key"],
                "attachment_kind": attachment.get("attachment_kind"),
                "mime_type": attachment.get("mime_type"),
                "filename": attachment.get("filename"),
                "content_available": bool(attachment_text),
                "content_excerpt": _truncate_text(attachment_text, Config.DOC_DETAIL_MAX_CHARS) if attachment_text else None,
            }
        )
        if attachment_text:
            attachment_texts.append(attachment_text)

    resource_text = _extract_resource_text(resource)
    detail = {
        "handle": handle,
        "course_id": section_id,
        "kind": resource_type,
        "title": resource.get("title") or schoology_id,
        "description": resource_text,
        "attachments": attachment_items,
        "content_available": bool(resource_text or attachment_texts),
        "content_excerpt": _truncate_text(resource_text or "\n\n".join(attachment_texts), Config.DOC_DETAIL_MAX_CHARS) if (resource_text or attachment_texts) else None,
    }
    return ToolExecutionResult(
        output_text=json.dumps(detail, ensure_ascii=True),
        summary_text=f"Read course material {detail['title']}",
        course_ids={section_id},
        assignment_handles=set(),
        document_handles={handle, *[item["handle"] for item in attachment_items]},
    )


def _extract_resource_text(resource: dict[str, Any]) -> str | None:
    raw_payload = resource.get("raw_payload") or {}
    if not isinstance(raw_payload, dict):
        return None
    text = _best_text(
        raw_payload.get("description"),
        raw_payload.get("body"),
        raw_payload.get("content"),
        raw_payload.get("text"),
        resource.get("description_preview"),
    )
    return _truncate_text(text, Config.DOC_DETAIL_MAX_CHARS)


def _extract_attachment_text(attachment: dict[str, Any]) -> str | None:
    downloaded_path = attachment.get("downloaded_path")
    if not isinstance(downloaded_path, str) or not downloaded_path.strip():
        return None

    extracted_text = read_extracted_attachment_text(downloaded_path)
    if extracted_text:
        return _truncate_text(_best_text(extracted_text), Config.DOC_DETAIL_MAX_CHARS)

    path = Path(downloaded_path)
    if not path.exists() or not path.is_file():
        return None

    suffix = path.suffix.lower()
    mime_type = str(attachment.get("mime_type") or "").lower()
    if suffix not in {".txt", ".md", ".markdown", ".json", ".csv", ".tsv", ".html", ".htm"} and not mime_type.startswith("text/"):
        return None

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        try:
            text = path.read_text(encoding="latin-1")
        except Exception:
            return None
    except Exception:
        return None

    if suffix in {".html", ".htm"}:
        text = _strip_html(text)
    return _truncate_text(_best_text(text), Config.DOC_DETAIL_MAX_CHARS)


def _strip_html(text: str) -> str:
    import re

    without_tags = re.sub(r"<[^>]+>", " ", text)
    return " ".join(without_tags.split())


def _resolve_pseudo_document_handle(handle: str, *, user_id: int) -> dict[str, Any]:
    match = re.match(r"^(?P<section_id>\d+)_document_(?P<title>.+)$", handle.strip())
    if not match:
        raise SchoologyToolError("Invalid Schoology handle")

    section_id = match.group("section_id")
    requested_title = " ".join(match.group("title").replace("_", " ").split()).strip().lower()
    if not requested_title:
        raise SchoologyToolError("Invalid Schoology handle")

    _require_section_access(user_id, section_id)
    resources = scraper_store.list_section_resources(section_id, limit=200)
    candidates = [
        resource
        for resource in resources
        if resource.get("resource_type") == "document"
        and _normalize_title(resource.get("title") or resource.get("schoology_id") or "") == requested_title
    ]
    if not candidates:
        candidates = [
            resource
            for resource in resources
            if resource.get("resource_type") == "document"
            and requested_title in _normalize_title(resource.get("title") or resource.get("schoology_id") or "")
        ]
    if len(candidates) != 1:
        raise SchoologyToolError("Invalid Schoology handle")

    resource = candidates[0]
    return {
        "kind": "resource",
        "section_id": resource["section_id"],
        "resource_type": resource["resource_type"],
        "schoology_id": resource["schoology_id"],
    }


def _normalize_title(value: str) -> str:
    normalized = " ".join(str(value).replace("_", " ").split()).strip().lower()
    normalized = re.sub(r"\bchapter\b", "ch", normalized)
    normalized = re.sub(r"[^a-z0-9\s]+", " ", normalized)
    return " ".join(normalized.split())


def _require_section_access(user_id: int, section_id: str) -> None:
    if scraper_store.user_has_active_section_membership(user_id, section_id):
        return
    raise SchoologyToolError("This Schoology course is not available for the current user")


def _sign_handle(payload: dict[str, Any]) -> str:
    return _handle_serializer().dumps(payload)


def _load_handle(handle: str) -> dict[str, Any]:
    try:
        payload = _handle_serializer().loads(handle)
    except BadSignature as exc:
        raise SchoologyToolError("Invalid Schoology handle") from exc
    if not isinstance(payload, dict):
        raise SchoologyToolError("Invalid Schoology handle payload")
    return payload


def _handle_serializer() -> URLSafeSerializer:
    return URLSafeSerializer(Config.SECRET_KEY, salt="chat-schoology-tools-v1")


def _coerce_user_id(value: str) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _coerce_days(value: Any, *, default: int) -> int:
    try:
        days = int(value) if value is not None else default
    except (TypeError, ValueError):
        days = default
    return min(max(days, 1), 60)


def _coerce_limit(value: Any, *, default: int, maximum: int) -> int:
    try:
        limit = int(value) if value is not None else default
    except (TypeError, ValueError):
        limit = default
    return min(max(limit, 1), maximum)


def _required_string(value: Any, field_name: str) -> str:
    if value is None:
        raise SchoologyToolError(f"{field_name} is required")
    text = str(value).strip()
    if not text:
        raise SchoologyToolError(f"{field_name} is required")
    return text


def _optional_string(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _best_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _truncate_text(value: str | None, limit: int) -> str | None:
    if not value:
        return None
    cleaned = " ".join(str(value).split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(limit - 3, 1)].rstrip() + "..."
