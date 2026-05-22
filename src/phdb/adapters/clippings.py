"""Clippings adapter — ingests vault clippings + reddit posts into the clippings table.

Consumes ClippingRecord records from phdb.formats.clippings_md.
Source: References/Clippings/ and References/Reddit Posts/ vault directories.
Reddit Posts are not differentiated — they dissolve as clippings.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.clippings_md import ClippingRecord, parse as parse_clippings_md
from phdb.log import get_logger

log = get_logger("phdb.adapters.clippings")

__all__ = ["ClippingsAdapter", "ClippingRecord"]


class ClippingsAdapter(Adapter):
    """Ingest vault clippings/reddit-posts into the `clippings` typed table."""

    name = "clippings"
    source_kind = "vault-clippings"
    file_kind = "md"
    schema_type = "Quotation"
    target_table = "clippings"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 100

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for rec in parse_clippings_md(source_path):
            extra: dict[str, object] = {
                "url": rec.url,
                "publisher": rec.publisher,
                "creator": rec.creator,
                "description": rec.description,
                "image_url": rec.image_url,
                "categories": rec.categories,
                "tags": rec.tags,
                "aliases": rec.aliases,
                "note_type": rec.note_type,
                "author_type": rec.author_type,
                "mtime": rec.mtime,
            }

            yield AdapterRow(
                schema_type=rec.schema_type,
                subject=rec.title,
                body_text=rec.body_text,
                body_text_source=rec.body_text_source,
                raw_hash=rec.provenance.raw_hash,
                file_path=rec.file_path,
                file_size=rec.file_size,
                ctime=rec.ctime,
                bucket=rec.bucket,
                extra=extra,
            )
