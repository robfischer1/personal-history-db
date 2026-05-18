"""SMS XML adapter — ingests SMS Backup & Restore XML exports.

Consumes ChatMessage records from phdb.formats.smsbr_xml.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.smsbr_xml import (
    _epoch_ms_to_iso,  # noqa: F401
    _normalize_phone,  # noqa: F401
    parse_sms,
)
from phdb.log import get_logger

log = get_logger("phdb.adapters.sms_xml")


class SmsXmlAdapter(Adapter):
    """Ingest SMS Backup & Restore XML exports."""

    name = "sms_xml"
    source_kind = "sms-xml"
    file_kind = "xml"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for rec in parse_sms(source_path):
            direction = "unknown"
            sender_address = None
            sender_name = None

            if rec.sender_address == "self":
                direction = "outbound"
            elif rec.sender_address:
                direction = "inbound"
                sender_address = rec.sender_address
                sender_name = rec.sender_name

            recipients = [
                {"address": r.address, "name": r.name or "", "rtype": r.rtype}
                for r in rec.recipients
            ]

            body = rec.body_text or ""

            yield AdapterRow(
                schema_type="Message",
                rfc822_message_id=rec.platform_id,
                sender_address=sender_address,
                sender_name=sender_name,
                direction=direction,
                date_sent=rec.date_sent or None,
                date_received=rec.date_sent or None,
                body_text=rec.body_text,
                body_text_source="sms-br-xml",
                is_multipart=int(rec.is_multipart),
                has_attachments=int(rec.has_attachments),
                attachment_count=rec.attachment_count,
                raw_hash=rec.provenance.raw_hash,
                body_text_hash=hashlib.sha256(body.encode()).hexdigest() if body else None,
                recipients=recipients,
                thread_key=rec.thread_key,
            )
