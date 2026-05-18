"""Calendar adapter — ingests iCal (.ics) exports.

Consumes CalendarEvent records from phdb.formats.ical.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.ical import parse
from phdb.log import get_logger

log = get_logger("phdb.adapters.calendar")

_MAX_BODY_LEN = 50_000


class CalendarAdapter(Adapter):
    """Ingest iCal calendar exports."""

    name = "calendar"
    source_kind = "calendar"
    file_kind = "ical"
    schema_type = "Event"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for rec in parse(source_path):
            parts = [rec.summary or "(no summary)"]
            if rec.location:
                parts.append(f"@ {rec.location}")
            if rec.description:
                parts.append(rec.description)
            body = "\n".join(parts)[:_MAX_BODY_LEN]

            yield AdapterRow(
                schema_type="Event",
                rfc822_message_id=f"calendar:{rec.provenance.raw_hash}",
                subject=rec.summary or "(no summary)",
                sender_address=rec.calendar_name,
                sender_name=rec.calendar_name,
                direction="self",
                date_sent=rec.date_start or None,
                date_received=rec.date_end,
                body_text=body,
                body_text_source="ical",
                raw_hash=rec.provenance.raw_hash,
                body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                thread_key=f"calendar:{rec.calendar_name}",
            )
