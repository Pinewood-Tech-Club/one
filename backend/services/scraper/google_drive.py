"""
Google Drive download helpers for Schoology link attachments.
"""
from __future__ import annotations

import hashlib
import io
import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from config import Config

logger = logging.getLogger(__name__)

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaIoBaseDownload
except ImportError:  # pragma: no cover - optional dependency path
    Request = None
    Credentials = None
    InstalledAppFlow = None
    build = None
    HttpError = None
    MediaIoBaseDownload = None


DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
GOOGLE_EXPORT_FORMATS = {
    "document": {
        "mime": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "ext": ".docx",
    },
    "spreadsheet": {
        "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "ext": ".xlsx",
    },
    "presentation": {
        "mime": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "ext": ".pptx",
    },
}
GOOGLE_PDF_EXPORT = {
    "mime": "application/pdf",
    "ext": ".pdf",
}


def google_drive_dependencies_available() -> bool:
    return all(
        dependency is not None
        for dependency in (Request, Credentials, InstalledAppFlow, build, MediaIoBaseDownload)
    )


def google_drive_url_info(url: str | None) -> dict[str, str] | None:
    if not url:
        return None

    normalized = str(url).strip()
    if not normalized:
        return None

    parsed = urlparse(normalized)
    netloc = parsed.netloc.lower()
    if "docs.google.com" not in netloc and "drive.google.com" not in netloc:
        return None

    doc_type = None
    if "docs.google.com/document" in normalized:
        doc_type = "document"
    elif "docs.google.com/spreadsheets" in normalized:
        doc_type = "spreadsheet"
    elif "docs.google.com/presentation" in normalized:
        doc_type = "presentation"
    elif "drive.google.com" in netloc:
        doc_type = "drive"

    if not doc_type:
        return None

    file_id = None
    for pattern in (r"/d/([a-zA-Z0-9-_]+)", r"[?&]id=([a-zA-Z0-9-_]+)"):
        match = re.search(pattern, normalized)
        if match:
            file_id = match.group(1)
            break

    if not file_id:
        return None

    query = parse_qs(parsed.query)
    resource_key = None
    values = query.get("resourcekey")
    if values:
        resource_key = values[0].strip() or None

    return {
        "url": normalized,
        "file_id": file_id,
        "doc_type": doc_type,
        **({"resource_key": resource_key} if resource_key else {}),
    }


@lru_cache(maxsize=1)
def get_google_drive_service():
    if not google_drive_dependencies_available():
        logger.warning("scraper_google_drive_unavailable reason=missing_dependencies")
        return None

    scopes = [DRIVE_READONLY_SCOPE]
    token_path = Path(Config.GOOGLE_DRIVE_TOKEN_FILE)
    client_secret_path = Path(Config.GOOGLE_DRIVE_CLIENT_SECRET_FILE)
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), scopes)

    if creds and not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token_path.parent.mkdir(parents=True, exist_ok=True)
            token_path.write_text(creds.to_json(), encoding="utf-8")
        else:
            creds = None

    if creds is None and Config.GOOGLE_DRIVE_ENABLE_INTERACTIVE_AUTH:
        if not client_secret_path.exists():
            logger.warning(
                "scraper_google_drive_unavailable reason=missing_client_secret path=%s",
                client_secret_path,
            )
            return None
        flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), scopes)
        creds = flow.run_local_server(port=0)
        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    if creds is None:
        logger.info(
            "scraper_google_drive_unavailable reason=missing_credentials token_path=%s interactive=%s",
            token_path,
            Config.GOOGLE_DRIVE_ENABLE_INTERACTIVE_AUTH,
        )
        return None

    return build("drive", "v3", credentials=creds, cache_discovery=False)


def _apply_resource_key(request, file_id: str, resource_key: str | None) -> None:
    if not resource_key:
        return
    headers = getattr(request, "headers", None)
    if isinstance(headers, dict):
        headers["X-Goog-Drive-Resource-Keys"] = f"{file_id}/{resource_key}"


def get_google_drive_metadata(link_info: dict[str, str]) -> dict[str, Any] | None:
    service = get_google_drive_service()
    if service is None:
        return None

    request = service.files().get(
        fileId=link_info["file_id"],
        fields="id,name,modifiedTime,mimeType",
        supportsAllDrives=True,
    )
    _apply_resource_key(request, link_info["file_id"], link_info.get("resource_key"))
    return request.execute()


def _safe_name(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
    return normalized or fallback


def _sidecar_path(attachment_dir: Path) -> Path:
    return attachment_dir / "google_drive.json"


def _read_sidecar(attachment_dir: Path) -> dict[str, Any] | None:
    sidecar_path = _sidecar_path(attachment_dir)
    if not sidecar_path.exists():
        return None
    try:
        return json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None


def _write_sidecar(attachment_dir: Path, payload: dict[str, Any]) -> None:
    _sidecar_path(attachment_dir).write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )


def _write_skip_sidecar(
    attachment_dir: Path,
    *,
    link_info: dict[str, str],
    metadata: dict[str, Any],
    reason: str,
    message: str,
) -> None:
    _write_sidecar(
        attachment_dir,
        {
            "doc_type": link_info["doc_type"],
            "drive_mime_type": metadata.get("mimeType"),
            "drive_name": metadata.get("name"),
            "file_id": link_info["file_id"],
            "modifiedTime": metadata.get("modifiedTime"),
            "resource_key": link_info.get("resource_key"),
            "skip_message": message,
            "skip_reason": reason,
            "source_url": link_info["url"],
        },
    )


def _blob_path(attachment_dir: Path, filename: str) -> Path:
    blob_dir = attachment_dir / "blob"
    blob_dir.mkdir(parents=True, exist_ok=True)
    return blob_dir / _safe_name(filename, "blob")


def _download_bytes(service, link_info: dict[str, str], metadata: dict[str, Any]) -> tuple[bytes, str, str]:
    file_id = link_info["file_id"]
    doc_type = link_info["doc_type"]
    mime_type = str(metadata.get("mimeType") or "").strip()
    drive_name = str(metadata.get("name") or file_id).strip() or file_id

    if doc_type in GOOGLE_EXPORT_FORMATS:
        export_format = GOOGLE_EXPORT_FORMATS[doc_type]
        request = service.files().export_media(
            fileId=file_id,
            mimeType=export_format["mime"],
        )
        _apply_resource_key(request, file_id, link_info.get("resource_key"))
        filename = drive_name
        if not filename.lower().endswith(export_format["ext"]):
            filename = f"{filename}{export_format['ext']}"
        content_type = export_format["mime"]
    else:
        if mime_type == "application/vnd.google-apps.folder":
            raise ValueError(f"Google Drive folders are not downloadable: {file_id}")
        if mime_type.startswith("application/vnd.google-apps."):
            raise ValueError(f"Unsupported Google native mime type {mime_type} for file {file_id}")
        request = service.files().get_media(
            fileId=file_id,
            supportsAllDrives=True,
        )
        _apply_resource_key(request, file_id, link_info.get("resource_key"))
        filename = drive_name
        content_type = mime_type or "application/octet-stream"

    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    return fh.getvalue(), filename, content_type


def _download_pdf_fallback(service, link_info: dict[str, str], metadata: dict[str, Any]) -> tuple[bytes, str, str]:
    file_id = link_info["file_id"]
    drive_name = str(metadata.get("name") or file_id).strip() or file_id
    request = service.files().export_media(
        fileId=file_id,
        mimeType=GOOGLE_PDF_EXPORT["mime"],
    )
    _apply_resource_key(request, file_id, link_info.get("resource_key"))

    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    filename = drive_name
    if not filename.lower().endswith(GOOGLE_PDF_EXPORT["ext"]):
        filename = f"{filename}{GOOGLE_PDF_EXPORT['ext']}"
    return fh.getvalue(), filename, GOOGLE_PDF_EXPORT["mime"]


def download_google_drive_link_if_needed(
    *,
    attachment_dir: Path,
    attachment: dict[str, Any],
) -> tuple[str | None, str | None]:
    payload = attachment.get("metadata_payload") or {}
    url = str(attachment.get("url") or payload.get("url") or payload.get("link") or "").strip()
    link_info = google_drive_url_info(url)
    if not link_info:
        return None, None

    service = get_google_drive_service()
    if service is None:
        return None, None

    metadata = get_google_drive_metadata(link_info)
    if not metadata:
        raise RuntimeError(f"Failed to fetch Google Drive metadata for {link_info['file_id']}")

    sidecar = _read_sidecar(attachment_dir) or {}
    previous_filename = str(sidecar.get("downloaded_filename") or "").strip()
    previous_modified_time = str(sidecar.get("modifiedTime") or "").strip()
    previous_path = _blob_path(attachment_dir, previous_filename) if previous_filename else None
    previous_skip_reason = str(sidecar.get("skip_reason") or "").strip()
    current_modified_time = str(metadata.get("modifiedTime") or "").strip()

    if (
        previous_path
        and previous_path.exists()
        and previous_modified_time
        and current_modified_time
        and previous_modified_time == current_modified_time
    ):
        return None, None
    if (
        previous_skip_reason
        and previous_modified_time
        and current_modified_time
        and previous_modified_time == current_modified_time
        and previous_skip_reason != "export_size_limit_exceeded"
    ):
        return None, None

    used_fallback = False
    try:
        content, filename, content_type = _download_bytes(service, link_info, metadata)
    except ValueError as exc:
        _write_skip_sidecar(
            attachment_dir,
            link_info=link_info,
            metadata=metadata,
            reason="unsupported",
            message=str(exc),
        )
        logger.info(
            "scraper_google_drive_skip file_id=%s reason=unsupported detail=%s",
            link_info["file_id"],
            exc,
        )
        return None, None
    except Exception as exc:
        if HttpError is not None and isinstance(exc, HttpError):
            error_reason = ""
            try:
                details = json.loads(exc.content.decode("utf-8"))
                errors = details.get("error", {}).get("errors", [])
                if errors:
                    error_reason = str(errors[0].get("reason") or "").strip()
            except Exception:
                error_reason = ""

            if error_reason == "exportSizeLimitExceeded":
                try:
                    content, filename, content_type = _download_pdf_fallback(
                        service,
                        link_info,
                        metadata,
                    )
                    used_fallback = True
                except Exception as fallback_exc:
                    _write_skip_sidecar(
                        attachment_dir,
                        link_info=link_info,
                        metadata=metadata,
                        reason="export_size_limit_exceeded",
                        message=f"{exc} | pdf_fallback_failed={fallback_exc}",
                    )
                    logger.info(
                        "scraper_google_drive_skip file_id=%s reason=export_size_limit_exceeded",
                        link_info["file_id"],
                    )
                    return None, None
        raise

    blob_path = _blob_path(attachment_dir, filename)
    blob_path.write_bytes(content)
    download_hash = hashlib.sha256(content).hexdigest()

    _write_sidecar(
        attachment_dir,
        {
            "content_type": content_type,
            "doc_type": link_info["doc_type"],
            "download_hash": download_hash,
            "downloaded_filename": blob_path.name,
            "drive_mime_type": metadata.get("mimeType"),
            "drive_name": metadata.get("name"),
            "export_fallback": "pdf" if used_fallback else None,
            "file_id": link_info["file_id"],
            "modifiedTime": metadata.get("modifiedTime"),
            "resource_key": link_info.get("resource_key"),
            "source_url": url,
        },
    )
    return str(blob_path), download_hash
