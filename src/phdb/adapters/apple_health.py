"""Apple Health adapter — ingests Health_Export.zip via streaming XML.

Consumes parsed records from phdb.formats.apple_health_xml.

Source: Health_Export.zip containing apple_health_export/export.xml + GPX routes.
Three record types mapped to messages + 5 sidecar tables:
  Record      -> Observation   + record_metadata + hr_samples
  Workout     -> ExerciseAction + workout_events + workout_statistics + geo_traces
  ClinicalRecord -> MedicalRecord (no sidecars)

Custom run() required for sidecar table writes.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import (
    Adapter,
    AdapterRow,
    DedupStrategy,
    IngestReport,
    SidecarColumn,
    SidecarTableDef,
)
from phdb.formats.apple_health_xml import (
    ParsedClinical,
    ParsedRecord,
    ParsedWorkout,
    parse,
)
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.apple_health")

MAX_BODY_LEN = 2000
COMMIT_EVERY = 25000

RECORD_METADATA_TABLE = SidecarTableDef(
    table_name="record_metadata",
    columns=(
        SidecarColumn("key", "TEXT", nullable=False),
        SidecarColumn("value", "TEXT"),
    ),
    parent_fk_column="message_id",
    parent_table="messages",
)

HR_SAMPLES_TABLE = SidecarTableDef(
    table_name="hr_samples",
    columns=(
        SidecarColumn("ts", "TEXT", nullable=False),
        SidecarColumn("bpm", "INTEGER", nullable=False),
    ),
    parent_fk_column="parent_message_id",
    parent_table="messages",
)

WORKOUT_EVENTS_TABLE = SidecarTableDef(
    table_name="workout_events",
    columns=(
        SidecarColumn("event_type", "TEXT"),
        SidecarColumn("date", "TEXT"),
        SidecarColumn("duration_seconds", "REAL"),
    ),
    parent_fk_column="workout_message_id",
    parent_table="messages",
)

WORKOUT_STATISTICS_TABLE = SidecarTableDef(
    table_name="workout_statistics",
    columns=(
        SidecarColumn("stat_type", "TEXT", nullable=False),
        SidecarColumn("value_min", "REAL"),
        SidecarColumn("value_avg", "REAL"),
        SidecarColumn("value_max", "REAL"),
        SidecarColumn("value_sum", "REAL"),
        SidecarColumn("unit", "TEXT"),
        SidecarColumn("date_start", "TEXT"),
        SidecarColumn("date_end", "TEXT"),
    ),
    parent_fk_column="workout_message_id",
    parent_table="messages",
)

GEO_TRACES_TABLE = SidecarTableDef(
    table_name="geo_traces",
    columns=(
        SidecarColumn("source_kind", "TEXT", nullable=False),
        SidecarColumn("point_idx", "INTEGER", nullable=False),
        SidecarColumn("ts", "TEXT"),
        SidecarColumn("lat", "REAL", nullable=False),
        SidecarColumn("lon", "REAL", nullable=False),
        SidecarColumn("elevation_m", "REAL"),
        SidecarColumn("speed_mps", "REAL"),
        SidecarColumn("course", "REAL"),
        SidecarColumn("horizontal_accuracy_m", "REAL"),
        SidecarColumn("vertical_accuracy_m", "REAL"),
        SidecarColumn("extra_json", "TEXT"),
    ),
    parent_fk_column="parent_message_id",
    parent_table="messages",
)


class AppleHealthAdapter(Adapter):
    """Ingest Apple Health Export zip (streaming XML + GPX)."""

    name = "apple_health"
    source_kind = "apple-health"
    file_kind = "zip"
    schema_type = "Observation"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 25000
    sidecar_tables = [
        RECORD_METADATA_TABLE,
        HR_SAMPLES_TABLE,
        WORKOUT_EVENTS_TABLE,
        WORKOUT_STATISTICS_TABLE,
        GEO_TRACES_TABLE,
    ]

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        raise NotImplementedError("Use run() directly — sidecar tables need conn access")

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestReport:
        report = IngestReport(
            adapter_name=self.name,
            source_path=str(source_path),
            source_file_id=0,
        )

        self.ensure_sidecar_tables(conn)
        source_file_id = self._register_source(conn, source_path)
        report.source_file_id = source_file_id

        metrics_thread_id, metrics_created = self._upsert_thread(conn, "apple-health:metrics")
        clinical_thread_id, clinical_created = self._upsert_thread(conn, "apple-health:clinical")
        if metrics_created:
            report.threads_created += 1
        if clinical_created:
            report.threads_created += 1

        touched_threads: set[int] = {metrics_thread_id, clinical_thread_id}
        processed = 0

        for parsed in parse(source_path):
            if isinstance(parsed, ParsedRecord):
                self._handle_record(
                    conn, parsed, source_file_id, metrics_thread_id, report,
                )
            elif isinstance(parsed, ParsedWorkout):
                wt_threads = self._handle_workout(
                    conn, parsed, source_file_id, report,
                )
                touched_threads.update(wt_threads)
            elif isinstance(parsed, ParsedClinical):
                self._handle_clinical(
                    conn, parsed, source_file_id, clinical_thread_id, report,
                )

            processed += 1
            if processed % COMMIT_EVERY == 0:
                conn.commit()

        conn.commit()

        for tid in touched_threads:
            self._update_thread_aggregates(conn, tid)
        conn.commit()

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.name,
            report.rows_yielded,
            report.rows_inserted,
            report.rows_skipped,
        )
        return report

    def _handle_record(
        self,
        conn: sqlite3.Connection,
        rec: ParsedRecord,
        source_file_id: int,
        metrics_thread_id: int,
        report: IngestReport,
    ) -> None:
        row = AdapterRow(
            schema_type="Observation",
            rfc822_message_id=f"apple-health:rec:{rec.raw_hash[:16]}",
            subject=rec.subject,
            sender_address=f"apple-health:{rec.source_name}",
            sender_name=rec.source_name,
            direction="self",
            date_sent=rec.start_date,
            date_received=rec.end_date,
            body_text=rec.body_text,
            body_text_source="apple-health-xml",
            is_bulk=1,
            bulk_signal="apple-health-record",
            raw_hash=rec.raw_hash,
            body_text_hash=hashlib.sha256(rec.body_text.encode()).hexdigest(),
        )

        report.rows_yielded += 1
        message_id = self._insert_row(conn, row, source_file_id)
        if message_id is None:
            report.rows_skipped += 1
            return
        report.rows_inserted += 1

        self._link_message_thread(conn, message_id, metrics_thread_id)

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

    def _handle_workout(
        self,
        conn: sqlite3.Connection,
        wkt: ParsedWorkout,
        source_file_id: int,
        report: IngestReport,
    ) -> set[int]:
        touched: set[int] = set()

        row = AdapterRow(
            schema_type="ExerciseAction",
            rfc822_message_id=f"apple-health:wkt:{wkt.raw_hash[:16]}",
            subject=wkt.subject,
            sender_address=f"apple-health:{wkt.source_name}",
            sender_name=wkt.source_name,
            direction="self",
            date_sent=wkt.start_date,
            date_received=wkt.end_date,
            body_text=wkt.body_text,
            body_text_source="apple-health-xml",
            is_bulk=1,
            bulk_signal="apple-health-workout",
            raw_hash=wkt.raw_hash,
            body_text_hash=hashlib.sha256(wkt.body_text.encode()).hexdigest(),
        )

        report.rows_yielded += 1
        message_id = self._insert_row(conn, row, source_file_id)
        if message_id is None:
            report.rows_skipped += 1
            return touched
        report.rows_inserted += 1

        thread_key = f"apple-health:workout:{wkt.raw_hash[:16]}"
        thread_id, created = self._upsert_thread(conn, thread_key)
        self._link_message_thread(conn, message_id, thread_id)
        if created:
            report.threads_created += 1
        touched.add(thread_id)

        conn.execute(
            "UPDATE threads SET date_first=?, date_last=? WHERE id=?",
            (wkt.start_date, wkt.end_date, thread_id),
        )

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
                    message_id,
                    ws.stat_type,
                    ws.value_min,
                    ws.value_avg,
                    ws.value_max,
                    ws.value_sum,
                    ws.unit,
                    ws.date_start,
                    ws.date_end,
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

        return touched

    def _handle_clinical(
        self,
        conn: sqlite3.Connection,
        clin: ParsedClinical,
        source_file_id: int,
        clinical_thread_id: int,
        report: IngestReport,
    ) -> None:
        row = AdapterRow(
            schema_type="MedicalRecord",
            rfc822_message_id=f"apple-health:clin:{clin.raw_hash[:16]}",
            subject=clin.subject,
            sender_address=f"apple-health:{clin.source_name}",
            sender_name=clin.source_name,
            direction="inbound",
            date_sent=clin.received_date,
            body_text=clin.body_text,
            body_text_source="apple-health-xml",
            is_bulk=1,
            bulk_signal="apple-health-clinical",
            raw_hash=clin.raw_hash,
            body_text_hash=hashlib.sha256(clin.body_text.encode()).hexdigest(),
        )

        report.rows_yielded += 1
        message_id = self._insert_row(conn, row, source_file_id)
        if message_id is None:
            report.rows_skipped += 1
            return
        report.rows_inserted += 1

        self._link_message_thread(conn, message_id, clinical_thread_id)
