"""Goodreads adapter — ingests a Goodreads CSV export.

Source: a CSV file with isbn, publisher, title columns.
Each book becomes a schema_type='Book' row. All books bucket into
a single thread.
"""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
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
        with open(source_path, encoding="utf-8-sig") as f:
            rdr = csv.DictReader(f)
            rdr.fieldnames = [
                (fn or "").strip().lstrip("﻿") for fn in (rdr.fieldnames or [])
            ]
            for row in rdr:
                isbn = (row.get("isbn") or "").strip()
                publisher = (row.get("publisher") or "").strip()
                title = (row.get("title") or "").strip()
                if not title:
                    continue

                dedup_seed = f"goodreads|{isbn}|{publisher}|{title}"
                raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

                yield AdapterRow(
                    schema_type="Book",
                    rfc822_message_id=f"goodreads:{raw_hash}",
                    subject=title,
                    sender_address=isbn or None,
                    sender_name=publisher or None,
                    direction="self",
                    body_text=title,
                    body_text_source="goodreads-csv",
                    raw_hash=raw_hash,
                    body_text_hash=hashlib.sha256(title.encode()).hexdigest(),
                    thread_key="goodreads:library",
                )
