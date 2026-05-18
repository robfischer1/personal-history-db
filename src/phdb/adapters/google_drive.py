"""Google Drive adapter — ingests documents from Google Takeout zips.

Source: a Google Takeout zip (or extracted directory) containing ``Drive/``
paths with text-bearing documents.

Each extracted file becomes a schema_type='DigitalDocument' row.
Text extraction covers docx, pdf, xlsx, ipynb, html, csv, json, txt, md, rtf.
External-library formats (docx, pdf, xlsx, html) gracefully degrade when
their dependencies are not installed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.document_extract import (
    EXTRACTORS,
    MAX_BODY_LEN,
    extract_csv,
    extract_docx,
    extract_html,
    extract_ipynb,
    extract_json,
    extract_pdf,
    extract_rtf,
    extract_txt,
    extract_xlsx,
)
from phdb.formats.google_drive_zip import (
    SKIP_EXTENSIONS,
    SKIP_PATH_PATTERNS,
    TEXT_EXTENSIONS,
    derive_bucket,
    parse,
)
from phdb.log import get_logger

log = get_logger("phdb.adapters.google_drive")

# Re-export for backward compatibility (tests import from this module)
__all__ = [
    "EXTRACTORS",
    "GoogleDriveAdapter",
    "MAX_BODY_LEN",
    "SKIP_EXTENSIONS",
    "SKIP_PATH_PATTERNS",
    "TEXT_EXTENSIONS",
    "derive_bucket",
    "extract_csv",
    "extract_docx",
    "extract_html",
    "extract_ipynb",
    "extract_json",
    "extract_pdf",
    "extract_rtf",
    "extract_txt",
    "extract_xlsx",
]


_BULK_FILENAME_PATTERNS = {
    "subscriptions",
    "youtube_subscriptions",
    "watch-history",
    "watch_history",
    "search-history",
    "search_history",
    "liked_videos",
    "playlists",
}


def _is_bulk_file(filename: str) -> bool:
    """Catalog/export dumps are bulk — they crowd out personal content."""
    stem = Path(filename).stem.lower()
    return stem in _BULK_FILENAME_PATTERNS


class GoogleDriveAdapter(Adapter):
    """Ingest Google Drive documents from Takeout zips or directories."""

    name = "google_drive"
    source_kind = "google-drive"
    file_kind = "zip"
    schema_type = "DigitalDocument"
    target_table = "documents"
    dedup_strategy = DedupStrategy.CONTENT_HASH
    batch_size = 500

    def compute_raw_hash(self, row: AdapterRow) -> str:
        seed = f"google-drive|{row.extra.get('relpath', '')}|{len(row.body_text or '')}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for doc in parse(source_path):
            body = doc.body_text
            if not body or not body.strip():
                continue

            filename = doc.title or ""
            raw_hash = doc.provenance.raw_hash

            yield AdapterRow(
                schema_type="DigitalDocument",
                rfc822_message_id=f"google-drive:{raw_hash}",
                subject=filename[:200],
                date_sent=doc.modified_date,
                body_text=body,
                body_text_source=doc.body_text_source,
                is_bulk=1 if _is_bulk_file(filename) else 0,
                source_byte_offset=doc.provenance.source_byte_offset or 0,
                source_byte_length=doc.provenance.source_byte_length or len(body),
                raw_hash=raw_hash,
                body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                file_path=doc.file_path,
                file_size=doc.file_size,
                bucket=doc.bucket,
                extra={"relpath": doc.file_path or ""},
            )
