"""
Section-level sync logic for the Schoology shared-content scraper.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from config import Config
from services.schoology.runtime import create_schoology_service

from . import store
from .attachment_download import download_attachment_if_needed
from .google_drive import google_drive_url_info

logger = logging.getLogger(__name__)


def _hash_payload(value: Any) -> str:
    return hashlib.sha256(store.canonical_json(value).encode("utf-8")).hexdigest()


def _ensure_section_resource_dir(section_id: str, resource_type: str) -> Path:
    target = Path(Config.SCRAPER_STORAGE_ROOT) / "sections" / section_id / f"{resource_type}s"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _write_resource_payload(section_id: str, resource_type: str, schoology_id: str, payload: dict) -> None:
    target_dir = _ensure_section_resource_dir(section_id, resource_type)
    (target_dir / f"{schoology_id}.json").write_text(store.canonical_json(payload), encoding="utf-8")


def _first_non_empty(payload: dict[str, Any], keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = payload.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return None


def _resource_schoology_id(payload: dict[str, Any], resource_type: str) -> str:
    explicit = _first_non_empty(payload, ("id", "document_id", "grade_item_id"))
    if explicit:
        return explicit
    return f"{resource_type}:{_hash_payload(payload)[:16]}"


def _preview_text(payload: dict[str, Any]) -> str | None:
    for key in ("description", "body", "content", "description_preview"):
        value = payload.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text[:500]
    return None


def _resource_title(payload: dict[str, Any], fallback: str) -> str:
    return str(
        payload.get("title")
        or payload.get("name")
        or payload.get("course_title")
        or payload.get("section_title")
        or fallback
    )


def _resource_due_at(payload: dict[str, Any]) -> str | None:
    due = payload.get("due")
    return str(due).strip() if due is not None and str(due).strip() else None


def _iter_attachment_candidates(value: Any, kind_hint: str | None = None):
    if value is None:
        return
    if isinstance(value, list):
        for item in value:
            yield from _iter_attachment_candidates(item, kind_hint)
        return
    if not isinstance(value, dict):
        return

    scalar_keys = {
        "id",
        "attachment_id",
        "title",
        "name",
        "filename",
        "url",
        "download_path",
        "converted_download_path",
        "mime",
        "mimetype",
        "filemime",
        "embed_code",
    }
    has_scalar_attachment_signal = any(key in value for key in scalar_keys)
    if has_scalar_attachment_signal:
        payload = dict(value)
        if kind_hint and "attachment_kind" not in payload:
            payload["attachment_kind"] = kind_hint
        yield payload
        return

    for key, child in value.items():
        next_hint = kind_hint
        lowered = str(key).lower()
        if lowered in {"file", "files"}:
            next_hint = "file"
        elif lowered in {"link", "links"}:
            next_hint = "link"
        elif lowered in {"video", "videos"}:
            next_hint = "video"
        elif lowered in {"embed", "embeds"}:
            next_hint = "embed"
        elif lowered == "attachment":
            next_hint = kind_hint
        yield from _iter_attachment_candidates(child, next_hint)


def _infer_attachment_kind(payload: dict[str, Any]) -> str:
    hinted = str(payload.get("attachment_kind") or "").strip().lower()
    if hinted in {"file", "link", "video", "embed"}:
        return hinted

    mime_type = str(
        payload.get("mime")
        or payload.get("mimetype")
        or payload.get("filemime")
        or ""
    ).strip().lower()
    if mime_type:
        return "file"

    filename = str(payload.get("filename") or "").strip()
    if filename:
        return "file"

    url = str(payload.get("download_url") or payload.get("url") or payload.get("link") or "").strip()
    if url:
        netloc = urlparse(url).netloc.lower()
        if "youtube" in netloc or "vimeo" in netloc:
            return "video"
        return "link"

    return "unknown"


def _normalize_attachment(
    attachment_payload: dict[str, Any],
    *,
    section_id: str,
    parent_schoology_id: str,
    parent_resource_type: str,
) -> dict[str, Any]:
    attachment_id = _first_non_empty(attachment_payload, ("id", "attachment_id"))
    attachment_kind = _infer_attachment_kind(attachment_payload)
    url = _first_non_empty(
        attachment_payload,
        ("download_path", "converted_download_path", "download_url", "url", "link"),
    )
    filename = _first_non_empty(attachment_payload, ("filename", "converted_filename", "name"))
    title = _first_non_empty(attachment_payload, ("title", "name", "filename")) or filename or attachment_kind
    mime_type = _first_non_empty(
        attachment_payload,
        ("mime", "mimetype", "filemime", "converted_filemime", "content_type"),
    )

    filesize = (
        attachment_payload.get("filesize")
        or attachment_payload.get("converted_filesize")
        or attachment_payload.get("size")
    )
    try:
        filesize_value = int(filesize) if filesize is not None else None
    except (TypeError, ValueError):
        filesize_value = None

    attachment_key = (
        attachment_id
        or f"synthetic:{_hash_payload({
            'section_id': section_id,
            'parent_schoology_id': parent_schoology_id,
            'parent_resource_type': parent_resource_type,
            'title': title,
            'filename': filename,
            'url': url,
            'attachment_kind': attachment_kind,
        })[:16]}"
    )
    canonical_key = f"{section_id}:{parent_resource_type}:{parent_schoology_id}:{attachment_key}"

    normalized = {
        "attachment_id": attachment_id,
        "attachment_key": attachment_key,
        "canonical_key": canonical_key,
        "attachment_kind": attachment_kind,
        "title": title,
        "filename": filename,
        "url": url,
        "mime_type": mime_type,
        "filesize": filesize_value,
        "metadata_payload": attachment_payload,
    }
    normalized["metadata_hash"] = _hash_payload(
        {
            "attachment_id": attachment_id,
            "attachment_kind": attachment_kind,
            "title": title,
            "filename": filename,
            "url": url,
            "mime_type": mime_type,
            "filesize": filesize_value,
            "metadata": attachment_payload,
        }
    )
    return normalized


def _should_attempt_attachment_download(attachment: dict[str, Any], attachment_result: dict[str, Any]) -> bool:
    if attachment_result["changed"]:
        return True
    if attachment["attachment_kind"] == "file" and not attachment_result["downloaded_path"]:
        return True
    if attachment["attachment_kind"] == "link" and google_drive_url_info(attachment.get("url")):
        return True
    return False


def _extract_attachments(
    payload: dict[str, Any],
    *,
    section_id: str,
    parent_schoology_id: str,
    parent_resource_type: str,
) -> list[dict[str, Any]]:
    attachments_root = payload.get("attachments")
    if attachments_root is None:
        return []

    normalized: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    for candidate in _iter_attachment_candidates(attachments_root):
        normalized_candidate = _normalize_attachment(
            candidate,
            section_id=section_id,
            parent_schoology_id=parent_schoology_id,
            parent_resource_type=parent_resource_type,
        )
        canonical_key = normalized_candidate["canonical_key"]
        if canonical_key in seen_keys:
            continue
        seen_keys.add(canonical_key)
        normalized.append(normalized_candidate)
    return normalized


def _sync_resource_batch(
    *,
    section_id: str,
    resource_type: str,
    payloads: list[dict[str, Any]],
    service,
    now: datetime,
    owner_token: str,
) -> tuple[int, int, set[str], set[str]]:
    changed_count = 0
    total_count = 0
    seen_resource_ids: set[str] = set()
    seen_attachment_keys: set[str] = set()

    for payload in payloads:
        if total_count % 25 == 0:
            store.heartbeat_section_run(section_id, owner_token, store.utcnow())
        schoology_id = _resource_schoology_id(payload, resource_type)
        seen_resource_ids.add(schoology_id)

        attachments = _extract_attachments(
            payload,
            section_id=section_id,
            parent_schoology_id=schoology_id,
            parent_resource_type=resource_type,
        )
        attachment_manifest_hash = _hash_payload(
            [
                {
                    "canonical_key": item["canonical_key"],
                    "metadata_hash": item["metadata_hash"],
                }
                for item in attachments
            ]
        )
        raw_hash = _hash_payload(payload)
        resource_result = store.upsert_resource(
            section_id=section_id,
            schoology_id=schoology_id,
            resource_type=resource_type,
            title=_resource_title(payload, schoology_id),
            description_preview=_preview_text(payload),
            published=payload.get("published"),
            available=payload.get("available"),
            due_at=_resource_due_at(payload),
            raw_payload=payload,
            raw_hash=raw_hash,
            attachment_manifest_hash=attachment_manifest_hash,
            now=now,
        )
        if resource_result["changed"]:
            changed_count += 1
            _write_resource_payload(section_id, resource_type, schoology_id, payload)
        total_count += 1

        for attachment in attachments:
            seen_attachment_keys.add(attachment["canonical_key"])
            attachment_result = store.upsert_attachment(
                canonical_key=attachment["canonical_key"],
                attachment_id=attachment["attachment_id"],
                resource_id=resource_result["resource_id"],
                section_id=section_id,
                parent_schoology_id=schoology_id,
                parent_resource_type=resource_type,
                attachment_kind=attachment["attachment_kind"],
                title=attachment["title"],
                filename=attachment["filename"],
                url=attachment["url"],
                mime_type=attachment["mime_type"],
                filesize=attachment["filesize"],
                metadata_payload=attachment["metadata_payload"],
                metadata_hash=attachment["metadata_hash"],
                now=now,
            )
            store.heartbeat_section_run(section_id, owner_token, store.utcnow())
            should_download = _should_attempt_attachment_download(attachment, attachment_result)
            try:
                downloaded_path, download_hash = download_attachment_if_needed(
                    service,
                    section_id=section_id,
                    attachment_key=attachment["attachment_key"],
                    attachment=attachment,
                    should_download=should_download,
                )
            except Exception:
                logger.exception(
                    "scraper_attachment_download_failed section_id=%s resource_type=%s schoology_id=%s attachment_key=%s",
                    section_id,
                    resource_type,
                    schoology_id,
                    attachment["attachment_key"],
                )
                continue
            store.heartbeat_section_run(section_id, owner_token, store.utcnow())
            if downloaded_path or download_hash or (
                attachment_result["changed"] and attachment["attachment_kind"] != "file"
            ):
                store.update_attachment_download(
                    attachment["canonical_key"],
                    downloaded_path=downloaded_path,
                    download_hash=download_hash,
                )

    return changed_count, total_count, seen_resource_ids, seen_attachment_keys


def run_section_sync(section_id: str, credential_user_id: int, owner_token: str) -> dict[str, Any]:
    now = store.utcnow()
    service = create_schoology_service(credential_user_id)
    if not service:
        raise RuntimeError(f"Schoology service unavailable for user {credential_user_id}")

    logger.info("scraper_section_sync_start section_id=%s user_id=%s", section_id, credential_user_id)
    store.heartbeat_section_run(section_id, owner_token, now)

    assignments = service.get_assignments(
        section_id,
        sync_to_convex=False,
        with_attachments=True,
    )
    store.heartbeat_section_run(section_id, owner_token, store.utcnow())

    documents = service.get_documents(section_id, with_attachments=True)
    store.heartbeat_section_run(section_id, owner_token, store.utcnow())

    changed_assignments, total_assignments, seen_assignment_ids, seen_attachment_keys = _sync_resource_batch(
        section_id=section_id,
        resource_type="assignment",
        payloads=assignments,
        service=service,
        now=store.utcnow(),
        owner_token=owner_token,
    )
    store.heartbeat_section_run(section_id, owner_token, store.utcnow())

    changed_documents, total_documents, seen_document_ids, seen_document_attachment_keys = _sync_resource_batch(
        section_id=section_id,
        resource_type="document",
        payloads=documents,
        service=service,
        now=store.utcnow(),
        owner_token=owner_token,
    )
    store.heartbeat_section_run(section_id, owner_token, store.utcnow())

    store.tombstone_missing_resources(section_id, "assignment", seen_assignment_ids, store.utcnow())
    store.tombstone_missing_resources(section_id, "document", seen_document_ids, store.utcnow())
    store.tombstone_missing_attachments(
        section_id,
        seen_attachment_keys | seen_document_attachment_keys,
        store.utcnow(),
    )

    completed_at = store.utcnow()
    store.complete_section_run(section_id, owner_token, completed_at)
    result = {
        "section_id": section_id,
        "credential_user_id": credential_user_id,
        "assignments_seen": total_assignments,
        "assignments_changed": changed_assignments,
        "documents_seen": total_documents,
        "documents_changed": changed_documents,
        "attachments_seen": len(seen_attachment_keys | seen_document_attachment_keys),
    }
    logger.info("scraper_section_sync_complete section_id=%s result=%s", section_id, result)
    return result
