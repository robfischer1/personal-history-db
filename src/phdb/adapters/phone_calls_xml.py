"""Phone calls XML adapter — ingests SMS Backup & Restore call-log XML.

Consumes CallRecord records from phdb.formats.smsbr_xml.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.smsbr_xml import (
    _epoch_ms_to_iso,  # noqa: F401
    _normalize_phone,  # noqa: F401
    parse_calls,
)
from phdb.log import get_logger

log = get_logger("phdb.adapters.phone_calls_xml")

_CALL_TYPE_RAW = {
    "1": "incoming", "2": "outgoing", "3": "missed",
    "4": "voicemail", "5": "rejected", "6": "refused",
}


def _synthesize_body(
    call_type: str, duration_s: int, contact_name: str, number: str
) -> str:
    name = contact_name if contact_name and contact_name not in ("(Unknown)", "null") else number
    label = _CALL_TYPE_RAW.get(call_type, "unknown")
    if call_type in ("1", "2"):
        return f"Call ({label}) with {name} - {duration_s}s"
    if call_type == "3":
        return f"Missed call from {name}"
    if call_type == "4":
        return f"Voicemail from {name} - {duration_s}s"
    if call_type == "5":
        return f"Rejected call from {name}"
    if call_type == "6":
        return f"Refused-list call from {name}"
    return f"Call ({label}) with {name} - {duration_s}s"


_RECORD_CALL_TYPE_TO_RAW = {
    "voice": "1",
    "missed": "3",
    "voicemail": "4",
    "rejected": "5",
    "refused": "6",
}


class PhoneCallsXmlAdapter(Adapter):
    """Ingest SMS Backup & Restore call-log XML exports."""

    name = "phone_calls_xml"
    source_kind = "calls-xml"
    file_kind = "xml"
    schema_type = "Action"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for rec in parse_calls(source_path):
            raw_type = _RECORD_CALL_TYPE_TO_RAW.get(rec.call_type, "1")
            if rec.direction == "outbound":
                raw_type = "2"
            body = _synthesize_body(
                raw_type, rec.duration_seconds or 0,
                rec.caller_address, rec.caller_address,
            )

            yield AdapterRow(
                schema_type="Action",
                rfc822_message_id=f"calls-xml:{rec.provenance.raw_hash}",
                sender_address=rec.caller_address if rec.direction == "inbound" else None,
                sender_name=rec.caller_address if rec.direction == "inbound" else None,
                direction=rec.direction,
                date_sent=rec.date_start or None,
                date_received=rec.date_start or None,
                body_text=body,
                body_text_source="sms-br-calls-xml",
                raw_hash=rec.provenance.raw_hash,
                body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                thread_key=f"calls:{rec.caller_address}",
            )
