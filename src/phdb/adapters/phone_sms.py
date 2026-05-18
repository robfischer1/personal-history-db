"""Phone SMS adapter — ingests Android mmssms.db (TitaniumBackup or standalone).

Source: a single mmssms.db file. For TitaniumBackup tarballs, extract the DB
before passing it to this adapter.
Reads sms table for SMS and pdu/addr/part tables for MMS.
Per-address threads.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.phone_sms_sqlite import (
    _epoch_ms_to_iso,  # noqa: F401
    _normalize_phone,  # noqa: F401
    parse,
)
from phdb.log import get_logger

log = get_logger("phdb.adapters.phone_sms")


class PhoneSmsAdapter(Adapter):
    """Ingest Android SMS/MMS from mmssms.db."""

    name = "phone_sms"
    source_kind = "phone-sms"
    file_kind = "sqlite"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for msg in parse(source_path):
            direction: str
            if msg.sender_address == "self":
                direction = "outbound"
            elif msg.sender_address == "unknown":
                direction = "unknown"
            else:
                direction = "inbound"

            # Determine body_text_source from thread_key prefix
            is_mms = msg.is_multipart or msg.has_attachments or (
                msg.body_text is not None and (
                    msg.body_text.startswith("(MMS with")
                    or msg.body_text.startswith("(empty MMS)")
                )
            )
            body_text_source = "phone-mms" if is_mms else "phone-sms"

            recipients = [{"address": r.address, "rtype": r.rtype} for r in msg.recipients]
            attachments = [
                {
                    "filename": a.filename or "",
                    "content_type": a.content_type,
                    "content_hash": a.content_hash,
                }
                for a in msg.attachments
            ]

            rfc_prefix = "phone-mms" if is_mms else "phone-sms"

            yield AdapterRow(
                schema_type="Message",
                rfc822_message_id=f"{rfc_prefix}:{msg.provenance.raw_hash}",
                sender_address=msg.sender_address,
                direction=direction,
                date_sent=msg.date_sent,
                body_text=msg.body_text,
                body_text_source=body_text_source,
                is_multipart=1 if msg.is_multipart else 0,
                has_attachments=1 if msg.has_attachments else 0,
                attachment_count=msg.attachment_count,
                source_byte_offset=msg.provenance.source_byte_offset,
                raw_hash=msg.provenance.raw_hash,
                body_text_hash=hashlib.sha256((msg.body_text or "").encode()).hexdigest(),
                thread_key=msg.thread_key,
                recipients=recipients,
                attachments=attachments,
            )
