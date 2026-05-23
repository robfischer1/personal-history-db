"""Shared Conversation and sidecar upsert logic."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING

from phdb.log import get_logger
from phdb.triples import get_predicate, resolve_node

if TYPE_CHECKING:
    from phdb.records import AISessionMessage

log = get_logger("phdb.formats.conversation_upserts")

_MAX_BODY_LEN = 50_000

def upsert_conversation_message(
    conn: sqlite3.Connection,
    source_file_id: int,
    record: AISessionMessage,
) -> int | None:
    """Insert a Conversation row into conversations_messages table.

    Returns the new row ID, or None if the record is a duplicate
    (based on source_file_id + raw_hash).
    """
    body = record.body_text
    if body and len(body) > _MAX_BODY_LEN:
        body = body[:_MAX_BODY_LEN]
    body_hash = hashlib.sha256(body.encode()).hexdigest() if body else None

    # Claude Code JSONL messages are outbound from user or inbound from assistant
    # but we follow the role from the record.

    cur = conn.execute(
        """INSERT INTO conversations_messages (
            schema_type, conversation_key, date_sent, body_text,
            body_text_hash, is_bulk, kind, role, parent_uuid,
            tool_name, tool_use_id, model, payload, raw_hash, source_file_id
        ) VALUES (
            'Conversation', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        ) ON CONFLICT(source_file_id, raw_hash) DO NOTHING
        RETURNING id""",
        (
            record.thread_key, record.date_sent or None, body,
            body_hash, 0 if record.kind == "message" else 1,
            record.kind, record.role, record.parent_uuid,
            record.tool_name, record.tool_use_id, record.model,
            record.payload, record.provenance.raw_hash, source_file_id,
        ),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    return None

def emit_conversation_thread_triple(
    conn: sqlite3.Connection,
    source_kind: str,
    message_id: int,
    thread_key: str,
) -> None:
    """Emit inThread triple for a conversation message."""
    pred = get_predicate(conn, "inThread")
    if not pred:
        return
    in_thread_id = pred["id"]

    # Record node
    record_label = f"conversations_messages:{message_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table="conversations_messages", source_id=message_id,
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
