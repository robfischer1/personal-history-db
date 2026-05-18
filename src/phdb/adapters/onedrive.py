"""OneDrive adapter — ingests documents from a local OneDrive directory.

Source: local OneDrive root directory (F:\\OneDrive\\ post-2026-05-13 reorg).
Walks Outputs/, Reference/, Records/ top-level pillars. Skips binary
extensions and files >20MB.

Reference/ has a body-extract allowlist: active-pursuit subdirs get full
body extraction; everything else gets metadata-only rows (subject +
file_path, is_bulk=1).

Each file becomes a schema_type='DigitalDocument' row in the documents
typed table.

Parsing logic lives in phdb.formats.onedrive_local; this adapter maps
DigitalDocument records to AdapterRow for DB insert.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.onedrive_local import (
    INCLUDE_TOP_DIRS,  # noqa: F401
    REFERENCE_BODY_ALLOWLIST,  # noqa: F401
    _derive_bucket,  # noqa: F401
    _is_reference_body_allowed,  # noqa: F401
    parse,
)
from phdb.log import get_logger

log = get_logger("phdb.adapters.onedrive")


class OneDriveAdapter(Adapter):
    """Ingest OneDrive documents from a local directory."""

    name = "onedrive"
    source_kind = "onedrive"
    file_kind = "local-files"
    schema_type = "DigitalDocument"
    target_table = "documents"
    dedup_strategy = DedupStrategy.RFC822_MESSAGE_ID
    batch_size = 500

    def compute_raw_hash(self, row: AdapterRow) -> str:
        return hashlib.sha256(
            f"onedrive|{row.rfc822_message_id or ''}".encode()
        ).hexdigest()

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for doc in parse(source_path):
            # Derive is_bulk: files under Reference/ are bulk
            rel_parts = Path(doc.file_path).parts if doc.file_path else ()
            is_bulk = 1 if rel_parts and rel_parts[0] == "Reference" else 0

            # Extract msg_id from the raw_hash seed (onedrive:{path_hash})
            # Reconstruct path_hash from provenance
            path_hash = hashlib.sha1(
                doc.provenance.source_path.encode()
            ).hexdigest()[:16]
            msg_id = f"onedrive:{path_hash}"

            yield AdapterRow(
                schema_type="DigitalDocument",
                rfc822_message_id=msg_id,
                subject=doc.title,
                date_sent=doc.modified_date,
                body_text=doc.body_text,
                body_text_source=doc.body_text_source,
                is_bulk=is_bulk,
                raw_hash=doc.provenance.raw_hash,
                body_text_hash=(
                    hashlib.sha256(doc.body_text.encode()).hexdigest()
                    if doc.body_text
                    else None
                ),
                file_path=doc.file_path,
                file_size=doc.file_size,
                ctime=doc.created_date,
                bucket=doc.bucket,
            )
