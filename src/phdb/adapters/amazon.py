"""Amazon data export adapter — ingests All Data Categories.zip.

Source: Amazon's "Request Your Data" zip with 8 data streams (CSVs + JSON).
Per-stream threads. All rows are is_bulk=1 (catalog/transaction data).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.amazon_zip import HANDLERS, AmazonRecord, parse
from phdb.log import get_logger

log = get_logger("phdb.adapters.amazon")

# Re-export HANDLERS for test backward compat
__all__ = ["AmazonAdapter", "HANDLERS"]


class AmazonAdapter(Adapter):
    """Ingest Amazon Data Export zips."""

    name = "amazon"
    source_kind = "amazon"
    file_kind = "zip"
    schema_type = "OrderAction"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for rec in parse(source_path):
            yield self._record_to_row(rec)

    @staticmethod
    def _record_to_row(rec: AmazonRecord) -> AdapterRow:
        return AdapterRow(
            schema_type=rec.schema_type,
            rfc822_message_id=f"amazon:{rec.provenance.raw_hash}",
            subject=rec.subject,
            sender_address="amazon:self",
            sender_name=rec.sender_name,
            direction="self",
            date_sent=rec.date_sent,
            body_text=rec.body_text,
            body_text_source="amazon-csv",
            is_bulk=1,
            bulk_signal="amazon-row",
            source_byte_offset=rec.provenance.source_byte_offset,
            source_byte_length=rec.provenance.source_byte_length,
            raw_hash=rec.provenance.raw_hash,
            body_text_hash=hashlib.sha256(rec.body_text.encode()).hexdigest(),
            thread_key=f"amazon:{rec.stream}",
        )

    def detect_bulk(self, row: AdapterRow) -> tuple[bool, str | None]:
        return True, "amazon-row"
