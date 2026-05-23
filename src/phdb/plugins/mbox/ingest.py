"""mbox ingestion logic."""

from __future__ import annotations

import sqlite3
from typing import TYPE_CHECKING

from phdb.formats.email_upserts import (
    emit_recipient_triples,
    emit_thread_triple,
    upsert_attachment,
    upsert_email_message,
)
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.records import EmailMessage
    from phdb.settings import Settings

log = get_logger("phdb.plugins.mbox.ingest")


def infer_direction(record: EmailMessage, settings: Settings | None) -> str:
    """Infer message direction using identity settings."""
    if not settings or not settings.identity.is_configured:
        return "unknown"

    identity = settings.identity
    if not record.sender_address:
        return "unknown"

    if identity.is_me(record.sender_address):
        if record.recipients and any(identity.is_me(r.address) for r in record.recipients):
            return "self"
        return "outbound"
    return "inbound"


def ingest_record(
    conn: sqlite3.Connection,
    record: EmailMessage,
    source_file_id: int,
    *,
    source_kind: str = "gmail",
    settings: Settings | None = None,
) -> int:
    """Ingest a single EmailMessage record and its sidecars."""
    direction = infer_direction(record, settings)

    # 1. Upsert the main email message
    message_id = upsert_email_message(
        conn, source_file_id, record, direction=direction
    )

    # 2. Upsert attachments
    for attachment in record.attachments:
        upsert_attachment(conn, message_id, attachment)

    # 3. Emit recipient triples
    emit_recipient_triples(conn, source_kind, message_id, record)

    # 4. Emit thread triple if gmail_thread_id is present
    if record.gmail_thread_id:
        emit_thread_triple(conn, source_kind, message_id, record.gmail_thread_id)

    return message_id
