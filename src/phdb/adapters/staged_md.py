"""Staged markdown adapter — ingests frontmatter+body .md files.

Source: a directory of .md files with YAML frontmatter.
Each file becomes one row. @type from frontmatter drives schema_type.
Per-directory threads.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.staged_md import (
    _extract_body_text,
    _parse_frontmatter,
    parse as parse_staged_md,
)
from phdb.log import get_logger

log = get_logger("phdb.adapters.staged_md")

# Re-export for test backward compatibility
__all__ = ["StagedMdAdapter", "_parse_frontmatter", "_extract_body_text"]


class StagedMdAdapter(Adapter):
    """Ingest staged personal-history .md files."""

    name = "staged_md"
    source_kind = "staged-md"
    file_kind = "md"
    schema_type = "CreativeWork"
    target_table = "documents"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 200

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for rec in parse_staged_md(source_path):
            # Derive synthetic message ID from the full path stored in provenance
            sub_path = rec.provenance.source_path.replace("/", "\\")
            synthetic_msgid = f"staged-md:{hashlib.sha256(sub_path.encode()).hexdigest()}"

            body_text_hash = (
                hashlib.sha256(rec.body_text.encode("utf-8")).hexdigest()
                if rec.body_text
                else None
            )

            yield AdapterRow(
                schema_type=rec.document_type or "CreativeWork",
                rfc822_message_id=synthetic_msgid,
                subject=rec.title,
                date_sent=rec.created_date,
                body_text=rec.body_text,
                body_text_source=rec.body_text_source,
                raw_hash=rec.provenance.raw_hash,
                body_text_hash=body_text_hash,
                file_path=rec.file_path,
                file_size=rec.file_size,
                bucket=rec.bucket,
            )
