"""Phone calls XML ingest helpers — Action upserts + Person/Thread facets."""

from __future__ import annotations

import hashlib
import sqlite3

from phdb.core.graph import get_predicate, resolve_node
from phdb.records import CallRecord

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


def upsert_call(
    conn: sqlite3.Connection,
    source_file_id: int,
    record: CallRecord,
    *,
    source_kind: str = "phone_calls_xml",
) -> int:
    """Ingest one CallRecord into the actions table and link facets."""
    raw_type = _RECORD_CALL_TYPE_TO_RAW.get(record.call_type, "1")
    if record.direction == "outbound":
        raw_type = "2"

    body = _synthesize_body(
        raw_type, record.duration_seconds or 0,
        record.caller_address, record.caller_address,
    )
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    raw_hash = record.provenance.raw_hash
    subject = f"Call with {record.caller_address}"

    # 1. Insert into actions table
    cur = conn.execute(
        """INSERT OR IGNORE INTO actions (
            schema_type, action_key, subject, sender_address, sender_name,
            direction, date_performed, date_received, body_text, body_text_source, body_text_hash,
            is_bulk, bulk_signal, raw_hash, source_file_id
        ) VALUES (
            'Action', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        ) RETURNING id""",
        (
            f"calls-xml:{raw_hash}", subject,
            record.caller_address if record.direction == "inbound" else None,
            record.caller_address if record.direction == "inbound" else None,
            record.direction, record.date_start or None, record.date_start or None,
            body, "sms-br-calls-xml", body_hash,
            0, None, raw_hash, source_file_id
        ),
    )
    row = cur.fetchone()
    if not row:
        return 0
    row_id = int(row[0])

    # 2. Link to Thread facet
    thread_key = f"calls:{record.caller_address}"
    thread_node_id = resolve_node(conn, f"{source_kind}:{thread_key}", "thread")

    in_thread_pred = get_predicate(conn, "inThread")
    if in_thread_pred:
        record_label = f"actions:{row_id}"
        record_node_id = resolve_node(
            conn, record_label, "record",
            source_table="actions", source_id=row_id
        )
        conn.execute(
            """INSERT OR IGNORE INTO triples
               (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
               VALUES (?, ?, ?, 'plugin', ?)""",
            (record_node_id, in_thread_pred["id"], thread_node_id, source_kind)
        )

    # 3. Link to Person facet (remote phone number)
    person_node_id = resolve_node(conn, record.caller_address, "person")

    pred_name = "receivedFrom" if record.direction == "inbound" else "sentTo"
    pred = get_predicate(conn, pred_name)
    if pred:
        record_label = f"actions:{row_id}"
        record_node_id = resolve_node(
            conn, record_label, "record",
            source_table="actions", source_id=row_id
        )
        conn.execute(
            """INSERT OR IGNORE INTO triples
               (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
               VALUES (?, ?, ?, 'plugin', ?)""",
            (record_node_id, pred["id"], person_node_id, source_kind)
        )

    return row_id
