"""
Attachment text extraction helpers for the Schoology scraper.
"""
from __future__ import annotations

from functools import lru_cache
import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

try:
    from docling.document_converter import DocumentConverter
except ImportError:  # pragma: no cover - optional dependency path
    DocumentConverter = None


DOCLING_SUFFIXES = {".pdf", ".docx", ".pptx"}
EXTRACTED_TEXT_FILENAME = "extracted.md"
EXTRACTION_SIDECAR_FILENAME = "extraction.json"


def extract_attachment_text_if_needed(
    *,
    attachment_dir: Path,
    downloaded_path: Path,
    download_hash: str,
    mime_type: str | None = None,
) -> str | None:
    if not downloaded_path.exists() or not downloaded_path.is_file():
        return None

    suffix = downloaded_path.suffix.lower()
    if suffix not in DOCLING_SUFFIXES:
        _write_sidecar(
            attachment_dir,
            {
                "status": "skipped",
                "reason": "unsupported_suffix",
                "source_hash": download_hash,
                "source_path": str(downloaded_path),
                "suffix": suffix,
                "mime_type": mime_type,
            },
        )
        return None

    existing = _read_sidecar(attachment_dir)
    extracted_path = attachment_dir / EXTRACTED_TEXT_FILENAME
    if (
        existing
        and existing.get("status") == "completed"
        and existing.get("source_hash") == download_hash
        and extracted_path.exists()
    ):
        return str(extracted_path)

    converter = _get_converter()
    if converter is None:
        _write_sidecar(
            attachment_dir,
            {
                "status": "skipped",
                "reason": "docling_unavailable",
                "source_hash": download_hash,
                "source_path": str(downloaded_path),
                "suffix": suffix,
                "mime_type": mime_type,
            },
        )
        return None

    try:
        result = converter.convert(str(downloaded_path))
        text = result.document.export_to_markdown().strip()
    except Exception as exc:
        logger.exception(
            "scraper_attachment_extraction_failed path=%s suffix=%s",
            downloaded_path,
            suffix,
        )
        _write_sidecar(
            attachment_dir,
            {
                "status": "failed",
                "reason": exc.__class__.__name__,
                "detail": str(exc)[:1000],
                "source_hash": download_hash,
                "source_path": str(downloaded_path),
                "suffix": suffix,
                "mime_type": mime_type,
            },
        )
        return None

    if not text:
        _write_sidecar(
            attachment_dir,
            {
                "status": "skipped",
                "reason": "empty_output",
                "source_hash": download_hash,
                "source_path": str(downloaded_path),
                "suffix": suffix,
                "mime_type": mime_type,
            },
        )
        return None

    extracted_path.write_text(text, encoding="utf-8")
    _write_sidecar(
        attachment_dir,
        {
            "status": "completed",
            "extractor": "docling",
            "output_path": str(extracted_path),
            "source_hash": download_hash,
            "source_path": str(downloaded_path),
            "suffix": suffix,
            "mime_type": mime_type,
        },
    )
    return str(extracted_path)


def read_extracted_attachment_text(downloaded_path: str | Path) -> str | None:
    path = Path(downloaded_path)
    attachment_dir = path.parent.parent if path.parent.name == "blob" else path.parent
    sidecar = _read_sidecar(attachment_dir)
    if not sidecar or sidecar.get("status") != "completed":
        return None

    output_path_value = sidecar.get("output_path")
    output_path = Path(output_path_value) if isinstance(output_path_value, str) and output_path_value.strip() else attachment_dir / EXTRACTED_TEXT_FILENAME
    if not output_path.exists() or not output_path.is_file():
        return None

    try:
        return output_path.read_text(encoding="utf-8")
    except Exception:
        return None


@lru_cache(maxsize=1)
def _get_converter():
    if DocumentConverter is None:
        return None
    return DocumentConverter()


def _sidecar_path(attachment_dir: Path) -> Path:
    return attachment_dir / EXTRACTION_SIDECAR_FILENAME


def _read_sidecar(attachment_dir: Path) -> dict[str, Any] | None:
    sidecar_path = _sidecar_path(attachment_dir)
    if not sidecar_path.exists():
        return None
    try:
        return json.loads(sidecar_path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_sidecar(attachment_dir: Path, payload: dict[str, Any]) -> None:
    _sidecar_path(attachment_dir).write_text(
        json.dumps(payload, sort_keys=True, indent=2, ensure_ascii=True),
        encoding="utf-8",
    )
