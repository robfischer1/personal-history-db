"""Apple DBs ingest helpers — WebActivity, ChatMessage, CallRecord, Note.

Each helper handles the SQL persistence for one record type, including
entity FKs (for Safari history) and dedup logic.
"""

from __future__ import annotations

import hashlib
import sqlite3

from phdb.formats.bookmark_upserts import (
    emit_bookmark_triples,
    upsert_bookmark,
    upsert_browse_action,
    upsert_web_page,
)
from phdb.formats.url import normalize_url
from phdb.records import BookmarkEvent, CallRecord, ChatMessage, DigitalDocument, WebActivity


def ingest_web_activity(
    conn: sqlite3.Connection,
    rec: WebActivity,
    source_file_id: int,
) -> int:
    """Route WebActivity to web_pages entity + (BookmarkAction | BrowseAction)."""
    url = rec.url or ""
    if not url:
        return 0
    norm = normalize_url(url)
    sighted = rec.date_performed or None
    wp_id = upsert_web_page(
        conn, url, norm,
        title=rec.title, sighted=sighted,
        source_file_id=source_file_id,
    )
    if rec.activity_type == "bookmark":
        event = BookmarkEvent(
            provenance=rec.provenance,
            url=url,
            normalized_url=norm,
            title=rec.title,
            instrument="safari",
            date_added=sighted or "",
            tags=(),
        )
        bm_id = upsert_bookmark(conn, source_file_id, event, web_page_id=wp_id)
        # WPEF follow-on brief 100 — emit bookmark-relationship triples.
        # Safari bookmarks have no folder/tags, so this typically yields
        # relatesTo + (when the title has text) mentions edges.
        emit_bookmark_triples(
            conn,
            bookmark_id=bm_id, web_page_id=wp_id,
            event=event, provenance="apple_dbs-emitted",
        )
        return bm_id

    # visit -> BrowseAction
    return upsert_browse_action(
        conn, source_file_id, wp_id,
        visit_time=sighted or "",
        source_device="iPhone",
        raw_hash=rec.provenance.raw_hash,
    )


def ingest_chat_message(
    conn: sqlite3.Connection,
    rec: ChatMessage,
    source_file_id: int,
) -> int:
    """Insert ChatMessage record into chat_messages table."""
    cur = conn.execute(
        """INSERT INTO chat_messages
           (schema_type, message_key, sender_address, date_sent, body_text,
            direction, body_text_source, raw_hash, source_file_id)
           VALUES ('Message', ?, ?, ?, ?, ?, 'apple-imessage', ?, ?)
           ON CONFLICT(source_file_id, raw_hash) DO NOTHING
           RETURNING id""",
        (rec.platform_id, rec.sender_address, rec.date_sent, rec.body_text,
         "outbound" if rec.sender_address == "self" else "inbound",
         rec.provenance.raw_hash, source_file_id),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        "SELECT id FROM chat_messages WHERE source_file_id = ? AND raw_hash = ?",
        (source_file_id, rec.provenance.raw_hash),
    )
    return int(cur.fetchone()[0])


def ingest_call_record(
    conn: sqlite3.Connection,
    rec: CallRecord,
    source_file_id: int,
) -> int:
    """Insert CallRecord into actions table."""
    body = f"Call: {rec.duration_seconds or 0}s, {rec.call_type}"
    body_text = rec.voicemail_text or body
    source = "voicemail" if rec.call_type.startswith("voicemail") else "callhistory"
    cur = conn.execute(
        """INSERT INTO actions
           (schema_type, action_key, sender_address, direction, date_performed,
            body_text, body_text_source, body_text_hash, raw_hash, source_file_id)
           VALUES ('Action', ?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(source_file_id, raw_hash) DO NOTHING
           RETURNING id""",
        (rec.provenance.raw_hash[:12], rec.caller_address, rec.direction,
         rec.date_start, body_text, source,
         hashlib.sha256(body_text.encode()).hexdigest(),
         rec.provenance.raw_hash, source_file_id),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        "SELECT id FROM actions WHERE source_file_id = ? AND raw_hash = ?",
        (source_file_id, rec.provenance.raw_hash),
    )
    return int(cur.fetchone()[0])


def ingest_digital_document(
    conn: sqlite3.Connection,
    rec: DigitalDocument,
    source_file_id: int,
) -> int:
    """Insert DigitalDocument into digital_documents table."""
    body = rec.body_text or ""
    body_hash = hashlib.sha256(body.encode()).hexdigest() if body else None
    cur = conn.execute(
        """INSERT INTO digital_documents
           (schema_type, doc_key, subject, sender_name, direction, date_created,
            body_text, body_text_source, body_text_hash, raw_hash, source_file_id)
           VALUES ('DigitalDocument', ?, ?, 'Me', 'self', ?, ?, 'apple-notes-snippet', ?, ?, ?)
           ON CONFLICT(source_file_id, raw_hash) DO NOTHING
           RETURNING id""",
        (f"notes:{rec.provenance.raw_hash[:12]}", rec.title, rec.created_date,
         rec.body_text, body_hash, rec.provenance.raw_hash, source_file_id),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        "SELECT id FROM digital_documents WHERE source_file_id = ? AND raw_hash = ?",
        (source_file_id, rec.provenance.raw_hash),
    )
    return int(cur.fetchone()[0])
