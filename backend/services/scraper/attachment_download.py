"""
Attachment download helpers for the Schoology scraper.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse

from config import Config
from services.schoology.client import SchoologyService

from .extraction import extract_attachment_text_if_needed
from . import store
from .google_drive import download_google_drive_link_if_needed, google_drive_url_info


def _safe_name(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return normalized or fallback


def attachment_base_dir(section_id: str, attachment_id: str) -> Path:
    return (
        Path(Config.SCRAPER_STORAGE_ROOT)
        / "sections"
        / _safe_name(section_id, "section")
        / "attachments"
        / _safe_name(attachment_id, "attachment")
    )


def write_attachment_metadata(section_id: str, attachment_id: str, metadata_payload: dict) -> None:
    target_dir = attachment_base_dir(section_id, attachment_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "metadata.json").write_text(store.canonical_json(metadata_payload), encoding="utf-8")


def _attachment_candidate_urls(attachment: dict[str, Any]) -> list[str]:
    payload = attachment.get("metadata_payload") or {}
    candidates: list[str] = []
    for source in (payload, attachment):
        if not isinstance(source, dict):
            continue
        for key in ("download_path", "converted_download_path", "download_url", "url", "link"):
            value = source.get(key)
            if value is None:
                continue
            normalized = str(value).strip()
            if normalized and normalized not in candidates:
                candidates.append(normalized)
    return candidates


def download_attachment_if_needed(
    service: SchoologyService,
    *,
    section_id: str,
    attachment_key: str,
    attachment: dict[str, Any],
    should_download: bool,
) -> tuple[str | None, str | None]:
    attachment_dir = attachment_base_dir(section_id, attachment_key)
    attachment_dir.mkdir(parents=True, exist_ok=True)
    write_attachment_metadata(section_id, attachment_key, attachment)

    attachment_kind = attachment.get("attachment_kind", "unknown")
    candidate_urls = _attachment_candidate_urls(attachment)
    google_link = None
    if attachment_kind == "link":
        for candidate_url in candidate_urls:
            google_link = google_drive_url_info(candidate_url)
            if google_link:
                break

    if attachment_kind == "link" and google_link:
        if not should_download:
            return None, None
        return download_google_drive_link_if_needed(
            attachment_dir=attachment_dir,
            attachment=attachment,
        )

    if attachment_kind != "file" or not candidate_urls:
        return None, None

    if not should_download:
        return None, None

    last_error: Exception | None = None
    content = b""
    content_type = None
    used_url = candidate_urls[0]
    for candidate_url in candidate_urls:
        try:
            content, content_type = service.download_attachment_bytes(candidate_url)
            used_url = candidate_url
            last_error = None
            break
        except Exception as exc:
            last_error = exc
    if last_error is not None:
        raise last_error

    download_hash = hashlib.sha256(content).hexdigest()

    payload = attachment.get("metadata_payload") or {}
    filename = str(
        attachment.get("filename")
        or payload.get("converted_filename")
        or payload.get("filename")
        or ""
    ).strip()
    if not filename:
        parsed = urlparse(str(used_url))
        filename = Path(parsed.path).name
    if not filename:
        suffix = ""
        if content_type and "/" in content_type:
            suffix = "." + content_type.split("/")[-1].split(";")[0]
        filename = f"blob{suffix}"

    safe_filename = _safe_name(filename, "blob")
    blob_dir = attachment_dir / "blob"
    blob_dir.mkdir(parents=True, exist_ok=True)
    blob_path = blob_dir / safe_filename
    blob_path.write_bytes(content)
    extract_attachment_text_if_needed(
        attachment_dir=attachment_dir,
        downloaded_path=blob_path,
        download_hash=download_hash,
        mime_type=content_type or attachment.get("mime_type"),
    )
    return str(blob_path), download_hash
