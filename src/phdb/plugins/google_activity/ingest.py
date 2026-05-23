"""Google Activity ingest helpers — WebPage upsert + Action upserts.

Ported from ``phdb.adapters.google_activity`` as part of Phase 7 of the
phdb Plugin Architecture plan.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from phdb.formats.bookmark_upserts import upsert_web_page
from phdb.formats.url import normalize_url
from phdb.records import WebActivity
from phdb.triples import get_predicate, resolve_node

_MAX_BODY_LEN = 2000

_ACTIVITY_TYPE_TO_TABLE = {
    "search": "search_actions",
    "watch": "watch_actions",
    "visit": "actions",
}

_ACTIVITY_TYPE_TO_SCHEMA = {
    "search": "SearchAction",
    "watch": "WatchAction",
    "visit": "Action",
}


def upsert_web_activity(
    conn: sqlite3.Connection,
    source_file_id: int,
    record: WebActivity,
) -> int:
    """Ingest one WebActivity record into the appropriate typed table."""
    stream = record.platform.removeprefix("google:")

    # 1. Resolve WebPage entity if URL is present
    wp_id = None
    if record.url:
        norm = normalize_url(record.url)
        wp_id = upsert_web_page(
            conn, record.url, norm,
            title=record.title,
            sighted=record.date_performed or None,
            source_file_id=source_file_id,
        )

    # 2. Prepare action row fields
    activity_type = record.activity_type
    table = _ACTIVITY_TYPE_TO_TABLE.get(activity_type, "actions")
    schema_t = _ACTIVITY_TYPE_TO_SCHEMA.get(activity_type, "Action")

    body_parts: list[str] = []
    if record.title:
        action = record.query or stream
        body_parts.append(f"{action} {record.title}")
    if record.url:
        body_parts.append(f"URL: {record.url}")
    body_text = ("\n".join(body_parts) or stream)[:_MAX_BODY_LEN]
    body_text_hash = hashlib.sha256(body_text.encode("utf-8")).hexdigest()

    subject = f"{record.query or stream} {record.title or ''}".strip()[:200]
    raw_hash = record.provenance.raw_hash

    # 3. Insert into typed table
    # We use manual SQL here to handle the different table shapes + web_page_id
    if table == "search_actions":
        sql = """INSERT OR IGNORE INTO search_actions
                 (schema_type, action_key, subject, source_device, sender_name,
                  direction, date_performed, body_text, body_text_source, body_text_hash,
                  is_bulk, bulk_signal, raw_hash, source_file_id, web_page_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        params: tuple[Any, ...] = (
            schema_t, f"google-activity:{raw_hash}", subject, "google:self", stream,
            "self", record.date_performed or None, body_text, "google-activity-html",
            body_text_hash, 1, "google-activity-event", raw_hash, source_file_id, wp_id
        )
    elif table == "watch_actions":
        sql = """INSERT OR IGNORE INTO watch_actions
                 (schema_type, watch_key, subject, platform_name, source_device,
                  direction, date_watched, body_text, body_text_source, body_text_hash,
                  is_bulk, bulk_signal, raw_hash, source_file_id, web_page_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        params = (
            schema_t, f"google-activity:{raw_hash}", subject, stream, "google:self",
            "self", record.date_performed or None, body_text, "google-activity-html",
            body_text_hash, 1, "google-activity-event", raw_hash, source_file_id, wp_id
        )
    else:  # actions
        sql = """INSERT OR IGNORE INTO actions
                 (schema_type, action_key, subject, sender_address, sender_name,
                  direction, date_performed, body_text, body_text_source, body_text_hash,
                  is_bulk, bulk_signal, raw_hash, source_file_id)
                 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        params = (
            schema_t, f"google-activity:{raw_hash}", subject, "google:self", stream,
            "self", record.date_performed or None, body_text, "google-activity-html",
            body_text_hash, 1, "google-activity-event", raw_hash, source_file_id
        )

    cur = conn.execute(sql, params)
    if cur.rowcount == 0:
        return 0
    row_id = int(cur.lastrowid)  # type: ignore[arg-type]

    # 4. Handle Thread facet
    thread_key = f"google-activity:{stream}"
    thread_node_id = resolve_node(conn, thread_key, "thread")

    in_thread_pred = get_predicate(conn, "inThread")
    if in_thread_pred:
        pred_id = in_thread_pred["id"]
        record_label = f"{table}:{row_id}"
        record_node_id = resolve_node(
            conn, record_label, "record",
            source_table=table, source_id=row_id
        )
        conn.execute(
            "INSERT OR IGNORE INTO triples (subject_node_id, predicate_id, object_node_id, provenance, source_ref) VALUES (?, ?, ?, 'adapter', 'google-activity')",
            (record_node_id, pred_id, thread_node_id)
        )

    return row_id
