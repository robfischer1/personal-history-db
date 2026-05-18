"""Goodreads CSV format parser — yields ConsumedItem records.

Source: Goodreads library export CSV with isbn, publisher, title columns.
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import csv
import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.records import ConsumedItem, Provenance


def parse(source_path: Path) -> Iterator[ConsumedItem]:
    """Parse a Goodreads CSV export, yielding ConsumedItem records."""
    source_str = str(source_path)

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

            yield ConsumedItem(
                provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
                item_type="book",
                title=title,
                platform="goodreads",
                author=publisher or None,
                isbn=isbn or None,
            )
