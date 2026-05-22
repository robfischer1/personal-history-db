"""Apple Health backup adapter — ingests Health SQLite databases from iOS backup.

Consumes parsed records from phdb.formats.apple_health_backup.

Source: healthdb_secure.sqlite + healthdb.sqlite extracted from an encrypted
iOS backup (iMazing, iTunes, etc.) via extract_health_backup.py.

Two record types mapped to messages + 3 sidecar tables:
  Record  -> Observation    + record_metadata
  Workout -> ExerciseAction + workout_events + workout_statistics

Custom run() required for sidecar table writes (same pattern as apple_health).
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
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
from phdb.formats.apple_health_backup import (
    APPLE_EPOCH,
    ParsedRecord,
    ParsedWorkout,
    parse,
)
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.apple_health_backup")

COMMIT_EVERY = 25000

RECORD_METADATA_TABLE = SidecarTableDef(
    table_name="record_metadata",
    columns=(
        SidecarColumn("key", "TEXT", nullable=False),
        SidecarColumn("value", "TEXT"),
    ),
    parent_fk_column="message_id",
    parent_table="observations",
)

WORKOUT_EVENTS_TABLE = SidecarTableDef(
    table_name="workout_events",
    columns=(
        SidecarColumn("event_type", "TEXT"),
        SidecarColumn("date", "TEXT"),
        SidecarColumn("duration_seconds", "REAL"),
    ),
    parent_fk_column="workout_message_id",
    parent_table="exercise_actions",
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
    parent_table="exercise_actions",
)


class AppleHealthBackupAdapter(Adapter):
    """Ingest Apple Health databases extracted from an iOS backup."""

    name = "apple_health_backup"
    source_kind = "apple-health-backup"
    file_kind = "sqlite"
    schema_type = "Observation"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 25000
    sidecar_tables = [
        RECORD_METADATA_TABLE,
        WORKOUT_EVENTS_TABLE,
        WORKOUT_STATISTICS_TABLE,
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

        metrics_thread_id, metrics_created = self._upsert_thread(
            conn, "apple-health-backup:metrics",
        )
        if metrics_created:
            report.threads_created += 1

        touched_threads: set[int] = {metrics_thread_id}
        thread_dates: dict[int, tuple[str, str]] = {}

        secure_db = self._resolve_secure_db(source_path)
        meta_db = secure_db.parent / "healthdb.sqlite"
        if not meta_db.exists():
            meta_db = None

        since_ts = self._last_ingest_ts(conn)
        if since_ts is not None:
            log.info("[%s] Incremental ingest: since_ts=%.0f", self.name, since_ts)

        processed = 0
        for parsed in parse(secure_db, meta_db, since_ts=since_ts):
            if isinstance(parsed, ParsedRecord):
                self._handle_record(
                    conn, parsed, source_file_id, metrics_thread_id, report,
                    thread_dates,
                )
            elif isinstance(parsed, ParsedWorkout):
                wt_threads = self._handle_workout(
                    conn, parsed, source_file_id, report,
                    thread_dates,
                )
                touched_threads.update(wt_threads)

            processed += 1
            if processed % COMMIT_EVERY == 0:
                conn.commit()

        conn.commit()

        for tid in touched_threads:
            dates = thread_dates.get(tid)
            self._update_thread_aggregates(
                conn, tid,
                dates[0] if dates else None,
                dates[1] if dates else None,
            )
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

    @staticmethod
    def _resolve_secure_db(source_path: Path) -> Path:
        """Accept either the directory or the sqlite file itself."""
        if source_path.is_dir():
            candidate = source_path / "healthdb_secure.sqlite"
            if not candidate.exists():
                raise FileNotFoundError(
                    f"healthdb_secure.sqlite not found in {source_path}"
                )
            return candidate
        return source_path

    def _last_ingest_ts(self, conn: sqlite3.Connection) -> float | None:
        """Find the latest date_sent from prior apple-health* source_kinds.

        Returns an Apple epoch timestamp so parse() can filter at the SQL level.
        This skips overlap with the XML export's data.
        """
        row = conn.execute(
            """SELECT MAX(date_observed) FROM observations
               JOIN source_files sf ON sf.id = observations.source_file_id
               WHERE sf.source_kind IN ('apple-health', 'apple-health-backup')
                 AND date_observed IS NOT NULL""",
        ).fetchone()
        if row is None or row[0] is None:
            return None
        try:
            dt = datetime.fromisoformat(row[0])
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return (dt - APPLE_EPOCH).total_seconds()
        except (ValueError, TypeError):
            return None

    def _handle_record(
        self,
        conn: sqlite3.Connection,
        rec: ParsedRecord,
        source_file_id: int,
        metrics_thread_id: int,
        report: IngestReport,
        thread_dates: dict[int, tuple[str, str]],
    ) -> None:
        row = AdapterRow(
            schema_type="Observation",
            rfc822_message_id=f"apple-health-backup:rec:{rec.raw_hash[:16]}",
            subject=rec.subject,
            sender_address=f"apple-health:{rec.source_name}",
            sender_name=rec.source_name,
            direction="self",
            date_sent=rec.start_date,
            date_received=rec.end_date,
            body_text=rec.body_text,
            body_text_source="apple-health-backup",
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
        rd = rec.start_date
        if rd and metrics_thread_id in thread_dates:
            lo, hi = thread_dates[metrics_thread_id]
            thread_dates[metrics_thread_id] = (min(lo, rd), max(hi, rd))
        elif rd:
            thread_dates[metrics_thread_id] = (rd, rd)

        for me in rec.metadata:
            conn.execute(
                "INSERT INTO record_metadata (message_id, key, value) VALUES (?, ?, ?)",
                (message_id, me.key, me.value),
            )

    def _handle_workout(
        self,
        conn: sqlite3.Connection,
        wkt: ParsedWorkout,
        source_file_id: int,
        report: IngestReport,
        thread_dates: dict[int, tuple[str, str]],
    ) -> set[int]:
        touched: set[int] = set()

        row = AdapterRow(
            schema_type="ExerciseAction",
            rfc822_message_id=f"apple-health-backup:wkt:{wkt.raw_hash[:16]}",
            subject=wkt.subject,
            sender_address=f"apple-health:{wkt.source_name}",
            sender_name=wkt.source_name,
            direction="self",
            date_sent=wkt.start_date,
            date_received=wkt.end_date,
            body_text=wkt.body_text,
            body_text_source="apple-health-backup",
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

        thread_key = f"apple-health-backup:workout:{wkt.raw_hash[:16]}"
        thread_id, created = self._upsert_thread(conn, thread_key)
        self._link_message_thread(conn, message_id, thread_id)
        if created:
            report.threads_created += 1
        touched.add(thread_id)
        if wkt.start_date:
            lo = wkt.start_date
            hi = wkt.end_date or wkt.start_date
            if thread_id in thread_dates:
                prev_lo, prev_hi = thread_dates[thread_id]
                thread_dates[thread_id] = (min(prev_lo, lo), max(prev_hi, hi))
            else:
                thread_dates[thread_id] = (lo, hi)

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

        return touched
