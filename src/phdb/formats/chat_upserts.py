"""Shared ChatMessage and sidecar upsert logic."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING

from phdb.log import get_logger
from phdb.triples import get_predicate, resolve_node

if TYPE_CHECKING:
    from phdb.records import Attachment, ChatMessage

log = get_logger("phdb.formats.chat_upserts")

_MAX_BODY_LEN = 50_000


def upsert_chat_message(
    conn: sqlite3.Connection,
    source_file_id: int,
    record: ChatMessage,
    *,
    direction: str = "unknown",
    body_text_source: str | None = None,
    sender_address: str | None = None,
    sender_name: str | None = None,
) -> int | None:
    """Insert a ChatMessage row into chat_messages table.

    Returns the new row ID, or None if the record is a duplicate
    (based on source_file_id + raw_hash).
    """
    body = record.body_text
    if body and len(body) > _MAX_BODY_LEN:
        body = body[:_MAX_BODY_LEN]
    body_hash = hashlib.sha256(body.encode()).hexdigest() if body else None

    # Use overrides if provided, otherwise use record values
    addr = sender_address if sender_address else record.sender_address
    name = sender_name if sender_name else record.sender_name

    sender_domain: str | None = None
    if addr and "@" in addr:
        sender_domain = addr.split("@", 1)[1]

    cur = conn.execute(
        """INSERT INTO chat_messages (
            schema_type, message_key, subject, sender_address, sender_name, sender_domain,
            direction, date_sent, body_text, body_text_source, body_text_hash,
            is_multipart, has_attachments, attachment_count, is_bulk, bulk_signal,
            source_byte_offset, source_byte_length, raw_hash, source_file_id
        ) VALUES (
            'Message', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        ) ON CONFLICT(source_file_id, raw_hash) DO NOTHING
        RETURNING id""",
        (
            record.platform_id, None, addr, name, sender_domain,
            direction, record.date_sent or None, body, body_text_source, body_hash,
            1 if record.is_multipart else 0, 1 if record.has_attachments else 0,
            record.attachment_count, 0, None,
            record.provenance.source_byte_offset, record.provenance.source_byte_length,
            record.provenance.raw_hash, source_file_id,
        ),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    return None


def get_existing_chat_message_id(
    conn: sqlite3.Connection,
    source_file_id: int,
    raw_hash: str,
) -> int | None:
    """Find the ID of an existing chat message by source_file_id + raw_hash."""
    cur = conn.execute(
        "SELECT id FROM chat_messages WHERE source_file_id = ? AND raw_hash = ?",
        (source_file_id, raw_hash),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def upsert_chat_attachment(
    conn: sqlite3.Connection,
    message_id: int,
    attachment: Attachment,
) -> int:
    """Insert an attachment row linked to a ChatMessage."""
    cur = conn.execute(
        """INSERT INTO attachments (
            schema_type, message_id, filename, content_type,
            content_disposition, size_bytes, on_disk_path, content_hash
        ) VALUES ('DigitalDocument', ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT DO NOTHING
        RETURNING id""",
        (
            message_id, attachment.filename, attachment.content_type,
            attachment.content_disposition, attachment.size_bytes,
            attachment.on_disk_path, attachment.content_hash,
        ),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    return 0


def emit_chat_recipient_triples(
    conn: sqlite3.Connection,
    source_kind: str,
    message_id: int,
    record: ChatMessage,
) -> None:
    """Emit sentTo triples for all recipients."""
    if not record.recipients:
        return

    pred = get_predicate(conn, "sentTo")
    if not pred:
        return
    sent_to_id = pred["id"]

    record_label = f"chat_messages:{message_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table="chat_messages", source_id=message_id,
    )

    for r in record.recipients:
        if not r.address:
            continue

        contact_node_id = resolve_node(
            conn, r.address.lower(), "contact",
            source_table="chat_messages", source_id=message_id,
        )

        conn.execute(
            """INSERT OR IGNORE INTO triples
               (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
               VALUES (?, ?, ?, 'plugin', ?)""",
            (record_node_id, sent_to_id, contact_node_id, source_kind),
        )


def emit_chat_thread_triple(
    conn: sqlite3.Connection,
    source_kind: str,
    message_id: int,
    thread_key: str,
) -> None:
    """Emit inThread triple for a message."""
    pred = get_predicate(conn, "inThread")
    if not pred:
        return
    in_thread_id = pred["id"]

    record_label = f"chat_messages:{message_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table="chat_messages", source_id=message_id,
    )

    # Thread node
    thread_label = f"{source_kind}:{thread_key}"
    thread_node_id = resolve_node(conn, thread_label, "thread")

    conn.execute(
        """INSERT OR IGNORE INTO triples
           (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
           VALUES (?, ?, ?, 'plugin', ?)""",
        (record_node_id, in_thread_id, thread_node_id, source_kind),
    )
