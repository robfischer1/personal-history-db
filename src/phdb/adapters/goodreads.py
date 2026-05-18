"""Goodreads adapter — ingests a Goodreads CSV export.

Consumes ConsumedItem records from phdb.formats.goodreads_csv.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.goodreads_csv import parse
from phdb.log import get_logger

log = get_logger("phdb.adapters.goodreads")


class GoodreadsAdapter(Adapter):
    """Ingest Goodreads CSV exports."""

    name = "goodreads"
    source_kind = "goodreads"
    file_kind = "csv"
    schema_type = "Book"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for rec in parse(source_path):
            yield AdapterRow(
                schema_type="Book",
                rfc822_message_id=f"goodreads:{rec.provenance.raw_hash}",
                subject=rec.title,
                sender_address=rec.isbn,
                sender_name=rec.author,
                direction="self",
                body_text=rec.title,
                body_text_source="goodreads-csv",
                raw_hash=rec.provenance.raw_hash,
                body_text_hash=hashlib.sha256(rec.title.encode()).hexdigest(),
                thread_key="goodreads:library",
            )
