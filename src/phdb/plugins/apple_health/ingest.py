"""Apple Health ingest logic — handles sidecar tables and row insertions."""

from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.triples import resolve_node

if TYPE_CHECKING:
    from phdb.formats.apple_health_xml import ParsedClinical, ParsedRecord, ParsedWorkout

def register_source_file(
    conn: sqlite3.Connection,
    source_path: Path,
    *,
    source_kind: str = "apple-health",
    file_kind: str = "zip",
) -> int:
    """Insert (or refresh) a source_files row for the given path."""
    cur = conn.execute(
        """INSERT INTO source_files
           (source_path, source_org, file_kind, source_kind, session_uuid, ingested_at)
           VALUES (?, ?, ?, ?, NULL,
                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
           ON CONFLICT(source_path) DO UPDATE
             SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           RETURNING id""",
        (str(source_path), None, file_kind, source_kind),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])

def ensure_sidecar_tables(conn: sqlite3.Connection) -> None:
    """Ensure apple_health sidecar tables exist."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS record_metadata (
            message_id INTEGER NOT NULL,
            key TEXT NOT NULL,
            value TEXT,
            FOREIGN KEY(message_id) REFERENCES observations(id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS hr_samples (
            parent_message_id INTEGER NOT NULL,
            ts TEXT NOT NULL,
            bpm INTEGER NOT NULL,
            FOREIGN KEY(parent_message_id) REFERENCES observations(id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS workout_events (
            workout_message_id INTEGER NOT NULL,
            event_type TEXT,
            date TEXT,
            duration_seconds REAL,
            FOREIGN KEY(workout_message_id) REFERENCES exercise_actions(id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS workout_statistics (
            workout_message_id INTEGER NOT NULL,
            stat_type TEXT NOT NULL,
            value_min REAL,
            value_avg REAL,
            value_max REAL,
            value_sum REAL,
            unit TEXT,
            date_start TEXT,
            date_end TEXT,
            FOREIGN KEY(workout_message_id) REFERENCES exercise_actions(id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS geo_traces (
            parent_message_id INTEGER NOT NULL,
            source_kind TEXT NOT NULL,
            point_idx INTEGER NOT NULL,
            ts TEXT,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            elevation_m REAL,
            speed_mps REAL,
            course REAL,
            horizontal_accuracy_m REAL,
            vertical_accuracy_m REAL,
            extra_json TEXT,
            FOREIGN KEY(parent_message_id) REFERENCES exercise_actions(id)
        )"""
    )
    # Add indexes
    conn.execute("CREATE INDEX IF NOT EXISTS idx_record_metadata_mid ON record_metadata(message_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_hr_samples_pmid ON hr_samples(parent_message_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_workout_events_wmid ON workout_events(workout_message_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_workout_stats_wmid ON workout_statistics(workout_message_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_geo_traces_pmid ON geo_traces(parent_message_id)")

def upsert_observation(
    conn: sqlite3.Connection,
    source_file_id: int,
    rec: ParsedRecord,
) -> int | None:
    """Insert a ParsedRecord into the observations table."""
    body_text_hash = hashlib.sha256(rec.body_text.encode()).hexdigest()
    cur = conn.execute(
        """INSERT OR IGNORE INTO observations (
            schema_type, observation_key, type_identifier, subject,
            source_device, direction, date_observed, date_end,
            body_text, body_text_source, body_text_hash, is_bulk,
            bulk_signal, raw_hash, source_file_id
        ) VALUES (
            'Observation', ?, ?, ?, ?, 'self', ?, ?, ?,
            'apple-health-xml', ?, 1, 'apple-health-record', ?, ?
        )""",
        (
            f"apple-health:rec:{rec.raw_hash[:16]}", rec.record_type_label,
            rec.subject, rec.source_name, rec.start_date, rec.end_date,
            rec.body_text, body_text_hash, rec.raw_hash, source_file_id,
        ),
    )
    if cur.rowcount == 0:
        return None

    message_id = cur.lastrowid
    assert message_id is not None

    for me in rec.metadata:
        conn.execute(
            "INSERT INTO record_metadata (message_id, key, value) VALUES (?, ?, ?)",
            (message_id, me.key, me.value),
        )

    for hr in rec.hr_samples:
        conn.execute(
            "INSERT INTO hr_samples (parent_message_id, ts, bpm) VALUES (?, ?, ?)",
            (message_id, hr.ts, hr.bpm),
        )

    return message_id

def upsert_exercise_action(
    conn: sqlite3.Connection,
    source_file_id: int,
    wkt: ParsedWorkout,
) -> int | None:
    """Insert a ParsedWorkout into the exercise_actions table."""
    body_text_hash = hashlib.sha256(wkt.body_text.encode()).hexdigest()
    cur = conn.execute(
        """INSERT OR IGNORE INTO exercise_actions (
            schema_type, exercise_key, type_identifier, subject,
            source_device, direction, date_performed, date_end,
            body_text, body_text_source, body_text_hash, is_bulk,
            bulk_signal, raw_hash, source_file_id
        ) VALUES (
            'ExerciseAction', ?, ?, ?, ?, 'self', ?, ?, ?,
            'apple-health-xml', ?, 1, 'apple-health-workout', ?, ?
        )""",
        (
            f"apple-health:wkt:{wkt.raw_hash[:16]}", wkt.activity_label,
            wkt.subject, wkt.source_name, wkt.start_date, wkt.end_date,
            wkt.body_text, body_text_hash, wkt.raw_hash, source_file_id,
        ),
    )
    if cur.rowcount == 0:
        return None

    message_id = cur.lastrowid
    assert message_id is not None

    for we in wkt.events:
        conn.execute(
            "INSERT INTO workout_events (workout_message_id, event_type, date, duration_seconds) VALUES (?, ?, ?, ?)",
            (message_id, we.event_type, we.date, we.duration_seconds),
        )

    for ws in wkt.statistics:
        conn.execute(
            """INSERT INTO workout_statistics
                  (workout_message_id, stat_type, value_min, value_avg, value_max, value_sum,
                   unit, date_start, date_end)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message_id, ws.stat_type, ws.value_min, ws.value_avg,
                ws.value_max, ws.value_sum, ws.unit, ws.date_start, ws.date_end,
            ),
        )

    for idx, pt in enumerate(wkt.gpx_points):
        conn.execute(
            """INSERT INTO geo_traces
                  (parent_message_id, source_kind, point_idx, ts, lat, lon,
                   elevation_m, speed_mps, course, horizontal_accuracy_m,
                   vertical_accuracy_m, extra_json)
               VALUES (?, 'apple-health-gpx', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                message_id, idx, pt.ts, pt.lat, pt.lon, pt.elevation_m,
                pt.speed_mps, pt.course, pt.horizontal_accuracy_m,
                pt.vertical_accuracy_m, None,
            ),
        )

    return message_id

def upsert_medical_record(
    conn: sqlite3.Connection,
    source_file_id: int,
    clin: ParsedClinical,
) -> int | None:
    """Insert a ParsedClinical into the medical_records table."""
    body_text_hash = hashlib.sha256(clin.body_text.encode()).hexdigest()
    cur = conn.execute(
        """INSERT OR IGNORE INTO medical_records (
            schema_type, record_key, subject, sender_address,
            sender_name, direction, date_recorded,
            body_text, body_text_source, body_text_hash,
            is_bulk, bulk_signal, raw_hash, source_file_id
        ) VALUES (
            'MedicalRecord', ?, ?, ?, ?, 'inbound', ?, ?,
            'apple-health-xml', ?, 1, 'apple-health-clinical', ?, ?
        )""",
        (
            f"apple-health:clin:{clin.raw_hash[:16]}", clin.subject,
            f"apple-health:{clin.source_name}", clin.source_name,
            clin.received_date, clin.body_text, body_text_hash,
            clin.raw_hash, source_file_id,
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

    conn.execute(
        """INSERT OR IGNORE INTO triples
           (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
           VALUES (?, ?, ?, 'adapter', ?)""",
        (record_node_id, in_thread_id, thread_node_id, source_kind),
    )
    return thread_node_id
