"""Google Activity adapter — ingests My Activity + YouTube history HTMLs.

Consumes WebActivity records from phdb.formats.google_activity_html.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.google_activity_html import parse
from phdb.log import get_logger

log = get_logger("phdb.adapters.google_activity")

_MAX_BODY_LEN = 2000

_ACTIVITY_TYPE_TO_SCHEMA: dict[str, str] = {
    "search": "SearchAction",
    "watch": "WatchAction",
    "visit": "Action",
}


class GoogleActivityAdapter(Adapter):
    """Ingest Google My Activity + YouTube history HTMLs."""

    name = "google_activity"
    source_kind = "google-activity"
    file_kind = "html"
    schema_type = "Action"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 1000

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for rec in parse(source_path):
            schema_t = _ACTIVITY_TYPE_TO_SCHEMA.get(rec.activity_type, "Action")
            stream = rec.platform.removeprefix("google:")

            body_parts: list[str] = []
            if rec.title:
                action = rec.query or stream
                body_parts.append(f"{action} {rec.title}")
            if rec.url:
                body_parts.append(f"URL: {rec.url}")
            body_text = ("\n".join(body_parts) or stream)[:_MAX_BODY_LEN]

            subject = f"{rec.query or stream} {rec.title or ''}".strip()[:200]

            yield AdapterRow(
                schema_type=schema_t,
                rfc822_message_id=f"google-activity:{rec.provenance.raw_hash}",
                subject=subject,
                sender_address="google:self",
                sender_name=stream,
                direction="self",
                date_sent=rec.date_performed or None,
                body_text=body_text,
                body_text_source="google-activity-html",
                is_bulk=1,
                bulk_signal="google-activity-event",
                raw_hash=rec.provenance.raw_hash,
                body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
                thread_key=f"google-activity:{stream}",
            )

    def detect_bulk(self, row: AdapterRow) -> tuple[bool, str | None]:
        return True, "google-activity-event"
