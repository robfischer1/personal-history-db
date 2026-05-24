"""Google Fit ingest logic."""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING

from phdb.core.source_files import register_source_file as register_source_file
from phdb.triples import resolve_node

if TYPE_CHECKING:
    from phdb.records import HealthObservation


def upsert_observation(
    conn: sqlite3.Connection,
    source_file_id: int,
    rec: HealthObservation,
    value_str: str,
    subject: str,
    body: str,
) -> int | None:
    """Insert a HealthObservation into the observations table."""
    body_text_hash = hashlib.sha256(body.encode()).hexdigest()
    cur = conn.execute(
        """INSERT OR IGNORE INTO observations (
            schema_type, observation_key, type_identifier, subject,
            source_device, direction, date_observed, date_end,
            body_text, body_text_source, body_text_hash, is_bulk,
            bulk_signal, raw_hash, source_file_id
        ) VALUES (
            'Observation', ?, ?, ?, ?, 'self', ?, ?, ?,
            'google-fit-json', ?, 1, 'google-fit-datapoint', ?, ?
        )""",
        (
            f"google-fit:obs:{rec.provenance.raw_hash[:16]}", rec.observation_type,
            subject, "google-fit:self", rec.date_start, rec.date_end,
            body, body_text_hash, rec.provenance.raw_hash, source_file_id,
        ),
    )
    if cur.rowcount == 0:
        return None
    return cur.lastrowid


def upsert_exercise_action(
    conn: sqlite3.Connection,
    source_file_id: int,
    rec: HealthObservation,
    value_str: str,
    subject: str,
    body: str,
) -> int | None:
    """Insert a HealthObservation into the exercise_actions table."""
    body_text_hash = hashlib.sha256(body.encode()).hexdigest()
    cur = conn.execute(
        """INSERT OR IGNORE INTO exercise_actions (
            schema_type, exercise_key, type_identifier, subject,
            source_device, direction, date_performed, date_end,
            body_text, body_text_source, body_text_hash, is_bulk,
            bulk_signal, raw_hash, source_file_id
        ) VALUES (
            'ExerciseAction', ?, ?, ?, ?, 'self', ?, ?, ?,
            'google-fit-json', ?, 1, 'google-fit-datapoint', ?, ?
        )""",
        (
            f"google-fit:wkt:{rec.provenance.raw_hash[:16]}", rec.observation_type,
            subject, "google-fit:self", rec.date_start, rec.date_end,
            body, body_text_hash, rec.provenance.raw_hash, source_file_id,
        ),
    )
    if cur.rowcount == 0:
        return None
    return cur.lastrowid


def emit_thread_triple(
    conn: sqlite3.Connection,
    source_kind: str,
    source_table: str,
    message_id: int,
    thread_key: str,
) -> int:
    """Emit inThread triple and return thread_node_id."""
    cur = conn.execute("SELECT id FROM predicates WHERE name = 'inThread'")
    row = cur.fetchone()
    if row:
        in_thread_id = row[0]
    else:
        from phdb.triples import get_predicate
        pred = get_predicate(conn, "inThread")
        assert pred is not None
        in_thread_id = pred["id"]

    record_label = f"{source_table}:{message_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table=source_table, source_id=message_id,
    )
    thread_label = f"{source_kind}:{thread_key}"
    thread_node_id = resolve_node(conn, thread_label, "thread")
    assert thread_node_id is not None

    conn.execute(
        """INSERT OR IGNORE INTO triples
           (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
           VALUES (?, ?, ?, 'adapter', ?)""",
        (record_node_id, in_thread_id, thread_node_id, source_kind),
    )
    return thread_node_id
