"""Shared EmailMessage and sidecar upsert logic."""

from __future__ import annotations

import json
import sqlite3

from phdb.log import get_logger
from phdb.records import Attachment, EmailMessage
from phdb.triples import resolve_node

log = get_logger("phdb.formats.email_upserts")


def upsert_email_message(
    conn: sqlite3.Connection,
    source_file_id: int,
    record: EmailMessage,
    *,
    direction: str = "unknown",
) -> int:
    """Insert an EmailMessage row. Dedups on (source_file_id, raw_hash)."""
    gmail_labels_json = (
        json.dumps(list(record.gmail_labels)) if record.gmail_labels else None
    )

    cur = conn.execute(
        """INSERT INTO emails (
            schema_type, rfc822_message_id, in_reply_to, references_chain,
            gmail_thread_id, gmail_labels, subject, sender_address,
            sender_name, sender_domain, direction, date_sent, date_received,
            body_text, body_text_source, is_multipart, has_attachments,
            attachment_count, is_bulk, bulk_signal, source_byte_offset,
            source_byte_length, raw_hash, source_file_id
        ) VALUES (
            'EmailMessage', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        ) ON CONFLICT(source_file_id, raw_hash) DO NOTHING
        RETURNING id""",
        (
            record.rfc822_message_id, record.in_reply_to, record.references_chain,
            record.gmail_thread_id, gmail_labels_json, record.subject,
            record.sender_address if record.sender_address != "unknown" else None,
            record.sender_name, record.sender_domain, direction,
            record.date_sent or None, record.date_received,
            record.body_text, record.body_text_source,
            1 if record.is_multipart else 0, 1 if record.has_attachments else 0,
            record.attachment_count, 1 if record.is_bulk else 0, record.bulk_signal,
            record.provenance.source_byte_offset, record.provenance.source_byte_length,
            record.provenance.raw_hash, source_file_id,
        ),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])

    # If DO NOTHING triggered, find the existing ID
    cur = conn.execute(
        "SELECT id FROM emails WHERE source_file_id = ? AND raw_hash = ?",
        (source_file_id, record.provenance.raw_hash),
    )
    return int(cur.fetchone()[0])


def upsert_attachment(
    conn: sqlite3.Connection,
    message_id: int,
    attachment: Attachment,
) -> int:
    """Insert an attachment row linked to an EmailMessage."""
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


def emit_recipient_triples(
    conn: sqlite3.Connection,
    source_kind: str,
    message_id: int,
    record: EmailMessage,
) -> None:
    """Emit sentTo triples for all recipients."""
    from phdb.triples import get_predicate

    if not record.recipients:
        return

    # Resolve predicate ID
    cur = conn.execute("SELECT id FROM predicates WHERE name = 'sentTo'")
    row = cur.fetchone()
    if not row:
        # Fallback to get_predicate which might create it or check migration
        from phdb.triples import get_predicate
        pred = get_predicate(conn, "sentTo")
        if not pred:
            return
        sent_to_id = pred["id"]
    else:
        sent_to_id = row[0]

    record_label = f"emails:{message_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table="emails", source_id=message_id,
    )

    for r in record.recipients:
        if not r.address:
            continue

        contact_node_id = resolve_node(
            conn, r.address.lower(), "contact",
            source_table="emails", source_id=message_id,
        )

        conn.execute(
            """INSERT OR IGNORE INTO triples
               (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
               VALUES (?, ?, ?, 'adapter', ?)""",
            (record_node_id, sent_to_id, contact_node_id, source_kind),
        )


def emit_thread_triple(
    conn: sqlite3.Connection,
    source_kind: str,
    message_id: int,
    thread_key: str,
) -> None:
    """Emit inThread triple for a message."""
    from phdb.triples import get_predicate

    # Resolve predicate ID
    cur = conn.execute("SELECT id FROM predicates WHERE name = 'inThread'")
    row = cur.fetchone()
    if not row:
        from phdb.triples import get_predicate
        pred = get_predicate(conn, "inThread")
        if not pred:
            return
        in_thread_id = pred["id"]
    else:
        in_thread_id = row[0]

    record_label = f"emails:{message_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table="emails", source_id=message_id,
    )

    # Thread node
    thread_label = f"{source_kind}:{thread_key}"
    thread_node_id = resolve_node(conn, thread_label, "thread")

    conn.execute(
        """INSERT OR IGNORE INTO triples
           (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
           VALUES (?, ?, ?, 'adapter', ?)""",
        (record_node_id, in_thread_id, thread_node_id, source_kind),
    )
