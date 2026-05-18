"""Google Fit adapter — ingests derived metric JSONs from Google Takeout.

Source: a Takeout zip or directory with Fit/All Data/*.json files.
Activity segments -> ExerciseAction; everything else -> Observation.
Per-metric threads. All is_bulk=1.

Delegates parsing to phdb.formats.google_fit_json; this adapter maps
HealthObservation records to AdapterRow for DB insertion.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.google_fit_json import (
    _short_metric,
    _yield_fit_files,
    parse,
)
from phdb.log import get_logger

log = get_logger("phdb.adapters.google_fit")

# Re-export for backward compat in tests
__all__ = ["GoogleFitAdapter", "_short_metric", "_yield_fit_files"]


class GoogleFitAdapter(Adapter):
    """Ingest Google Fit derived metric JSONs."""

    name = "google_fit"
    source_kind = "google-fit"
    file_kind = "json"
    schema_type = "Observation"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 1000

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for record in parse(source_path):
            prov = record.provenance
            metric = record.observation_type

            # Determine schema type from metadata category
            meta_dict = dict(record.metadata)
            category = meta_dict.get("category", "metric")
            schema_t = "ExerciseAction" if category == "exercise" else "Observation"

            # Reconstruct value string for subject/body
            value_str = meta_dict.get("activity_name") or meta_dict.get("value_str")
            if value_str is None and record.value is not None:
                value_str = str(record.value)
            if value_str is None:
                value_str = "None"

            subject = f"{metric}: {value_str}"[:200]
            body = f"{metric} = {value_str}\nstart={record.date_start} end={record.date_end}"[:1000]

            yield AdapterRow(
                schema_type=schema_t,
                rfc822_message_id=f"google-fit:{prov.raw_hash}",
                subject=subject,
                sender_address="google-fit:self",
                sender_name=metric,
                direction="self",
                date_sent=record.date_start,
                date_received=record.date_end,
                body_text=body,
                body_text_source="google-fit-json",
                is_bulk=1,
                bulk_signal="google-fit-datapoint",
                source_byte_offset=prov.source_byte_offset,
                source_byte_length=prov.source_byte_length,
                raw_hash=prov.raw_hash,
                body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                thread_key=f"google-fit:{metric}",
            )

    def detect_bulk(self, row: AdapterRow) -> tuple[bool, str | None]:
        return True, "google-fit-datapoint"
