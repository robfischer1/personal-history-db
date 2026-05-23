"""StrongPlugin — ingest Strong workout SQLite exports."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.strong_sqlite import _format_weight, parse_workouts
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.plugins.strong")


@dataclass
class IngestSummary:
    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class AdapterRow:
    """Local row carrier for strong workout records.

    The plugin originally imported AdapterRow from the deprecated
    ``phdb.adapters.base`` module; inlined here when that module was
    retired (Phase 7 epilogue). Carries only the fields strong's
    ``parse`` / ``ingest_row`` actually use. Pre-Phase-10 hardening
    should replace this with a proper ``phdb.records.WorkoutSession``
    record type.
    """

    schema_type: str = "ExerciseAction"
    rfc822_message_id: str | None = None
    subject: str | None = None
    sender_address: str | None = None
    sender_name: str | None = None
    direction: str = "unknown"
    date_sent: str | None = None
    body_text: str | None = None
    body_text_hash: str | None = None
    raw_hash: str | None = None
    thread_key: str | None = None


def _register_source_file(
    conn: sqlite3.Connection,
    source_path: Path,
    *,
    source_kind: str = "strong",
    file_kind: str = "sqlite",
) -> int:
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


class StrongPlugin(PhdbSourcePlugin):
    SOURCE_KIND = "strong"
    FILE_KIND = "sqlite"
    BATCH_SIZE = 500

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*.sqlite")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[AdapterRow]:
        for ctx, sets in parse_workouts(path):
            body_parts = [f"Workout: {ctx.workout_name}"]

            if ctx.duration_seconds:
                body_parts.append(f"Duration: {ctx.duration_seconds // 60} min")
            if ctx.body_weight_kg and ctx.body_weight_kg > 0:
                bw = _format_weight(ctx.body_weight_kg)
                if bw:
                    body_parts.append(f"Body weight: {bw}")
            if ctx.notes:
                body_parts.append(f"Notes: {ctx.notes}")

            current_exercise = ""
            for s in sets:
                if s.exercise_name != current_exercise:
                    current_exercise = s.exercise_name
                    body_parts.append(f"\n{current_exercise}:")
                parts: list[str] = []
                if s.weight_kg and s.weight_kg > 0:
                    w = _format_weight(s.weight_kg)
                    if w:
                        parts.append(w)
                if s.reps and s.reps > 0:
                    parts.append(f"{s.reps} reps")
                if s.duration_seconds and s.duration_seconds > 0:
                    parts.append(f"{s.duration_seconds // 60}:{s.duration_seconds % 60:02d}")
                if s.distance_meters and s.distance_meters > 0:
                    parts.append(f"{s.distance_meters:.0f}m")
                body_parts.append(f"  Set {s.set_number}: " + (" × ".join(parts) if parts else "(body weight)"))

            body_text = "\n".join(body_parts)

            yield AdapterRow(
                schema_type="ExerciseAction",
                rfc822_message_id=f"strong:{ctx.z_pk}",
                subject=ctx.workout_name,
                sender_address="strong:self",
                sender_name="Strong",
                direction="self",
                date_sent=ctx.date_iso,
                body_text=body_text,
                body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
                thread_key="strong:workouts",
            )

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: AdapterRow,
        *,
        source_file_id: int | None = None,
    ) -> int:
        cur = conn.execute(
            """INSERT INTO exercise_actions
               (source_file_id, type_identifier, subject, sender_domain,
                direction, date_performed, body_text, body_text_hash, raw_hash)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(source_file_id, raw_hash) DO NOTHING
               RETURNING id""",
            (source_file_id, record.sender_name, record.subject, record.sender_address,
             record.direction, record.date_sent, record.body_text, record.body_text_hash,
             record.raw_hash),
        )
        row = cur.fetchone()
        
        # If the row was not inserted (due to conflict), find the existing one
        if row is None:
            cur = conn.execute(
                "SELECT id FROM exercise_actions WHERE source_file_id = ? AND raw_hash = ?",
                (source_file_id, record.raw_hash),
            )
            row_id = int(cur.fetchone()[0])
        else:
            row_id = int(row[0])
            # Only link threads if newly inserted? 
            # Actually, let's keep it simple for now, link always if present.
            if record.thread_key:
                thread_id, _ = self._upsert_thread(conn, record.thread_key)
                self._link_message_thread(conn, row_id, thread_id, row=record)

        return row_id

    def _upsert_thread(
        self,
        conn: sqlite3.Connection,
        thread_key: str,
    ) -> tuple[int, bool]:
        label = f"{self.SOURCE_KIND}:{thread_key}"
        existing = conn.execute(
            "SELECT id FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
            (label.lower(),),
        ).fetchone()
        if existing:
            return existing[0], False

        from phdb.triples import resolve_node
        node_id = resolve_node(conn, label, "thread")
        return node_id, True

    def _link_message_thread(
        self, conn: sqlite3.Connection, message_id: int, thread_node_id: int,
        *, row: AdapterRow,
    ) -> None:
        from phdb.triples import resolve_node, get_predicate
        in_thread_id = get_predicate(conn, "inThread")["id"]
        source_table = "exercise_actions"

        record_label = f"{source_table}:{message_id}"
        record_node_id = resolve_node(
            conn, record_label, "record",
            source_table=source_table, source_id=message_id,
        )

        conn.execute(
            "INSERT OR IGNORE INTO triples"
            " (subject_node_id, predicate_id, object_node_id,"
            "  provenance, source_ref)"
            " VALUES (?, ?, ?, 'adapter', ?)",
            (record_node_id, in_thread_id, thread_node_id, self.SOURCE_KIND),
        )

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        report = IngestSummary(source_path=str(source_path))
        
        # Check if already registered
        cur = conn.execute("SELECT id FROM source_files WHERE source_path = ?", (str(source_path),))
        row = cur.fetchone()
        
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id
        
        if row:
            # Already registered, count rows in the existing source
            cur = conn.execute("SELECT count(*) FROM exercise_actions WHERE source_file_id = ?", (row[0],))
            report.rows_skipped = int(cur.fetchone()[0])
            return report

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            
            self.ingest_row(conn, record, source_file_id=source_file_id)
            report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()
        log.info(
            "[strong] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
