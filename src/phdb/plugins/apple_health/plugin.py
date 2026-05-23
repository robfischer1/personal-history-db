"""Apple Health plugin — ingests Health_Export.zip via streaming XML."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.apple_health_xml import (
    ParsedClinical,
    ParsedRecord,
    ParsedWorkout,
)
from phdb.formats.apple_health_xml import (
    parse as parse_apple_health,
)
from phdb.log import get_logger
from phdb.plugins.apple_health.ingest import (
    emit_thread_triple,
    ensure_sidecar_tables,
    register_source_file,
    upsert_exercise_action,
    upsert_medical_record,
    upsert_observation,
)

if TYPE_CHECKING:
    from phdb.formats.apple_health_xml import ParsedElement
    from phdb.settings import Settings

log = get_logger("phdb.plugins.apple_health")

COMMIT_EVERY = 25000

@dataclass
class IngestSummary:
    """Result of one ``run()`` call."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    threads_created: int = 0
    errors: list[str] = field(default_factory=list)


class AppleHealthPlugin(PhdbSourcePlugin):
    """Apple Health plugin — Phase 7 port."""

    SOURCE_KIND = "apple-health"
    FILE_KIND = "zip"

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every Apple Health zip."""
        if root.is_file():
            if root.name == "Health_Export.zip":
                yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("Health_Export.zip")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[ParsedElement]:
        """Yield parsed records from one Apple Health source file."""
        yield from parse_apple_health(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: Any,
        *,
        source_file_id: int,
        metrics_thread_id: int | None = None,
        clinical_thread_id: int | None = None,
    ) -> int | None:
        """Ingest a single record."""
        if isinstance(record, ParsedRecord):
            msg_id = upsert_observation(conn, source_file_id, record)
            if msg_id and metrics_thread_id:
                emit_thread_triple(
                    conn, self.SOURCE_KIND, "observations", msg_id, "metrics"
                )
            return msg_id

        if isinstance(record, ParsedWorkout):
            msg_id = upsert_exercise_action(conn, source_file_id, record)
            if msg_id:
                thread_key = f"workout:{record.raw_hash[:16]}"
                emit_thread_triple(
                    conn, self.SOURCE_KIND, "exercise_actions", msg_id, thread_key
                )
            return msg_id

        if isinstance(record, ParsedClinical):
            msg_id = upsert_medical_record(conn, source_file_id, record)
            if msg_id and clinical_thread_id:
                emit_thread_triple(
                    conn, self.SOURCE_KIND, "medical_records", msg_id, "clinical"
                )
            return msg_id

        return None

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one Apple Health zip."""
        report = IngestSummary(source_path=str(source_path))

        ensure_sidecar_tables(conn)
        source_file_id = register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        # We need to know if they were JUST created.
        def _get_or_create_thread(conn: sqlite3.Connection, label: str) -> tuple[int, bool]:
            existing = conn.execute(
                "SELECT id FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
                (label.lower(),),
            ).fetchone()
            if existing:
                return existing[0], False
            from phdb.triples import resolve_node
            # resolve_node always returns int for these inputs; the int|None
            # signature covers query-only callers.
            return resolve_node(conn, label, "thread"), True  # type: ignore[return-value]

        metrics_thread_id, m_new = _get_or_create_thread(conn, f"{self.SOURCE_KIND}:metrics")
        clinical_thread_id, c_new = _get_or_create_thread(conn, f"{self.SOURCE_KIND}:clinical")
        if m_new:
            report.threads_created += 1
        if c_new:
            report.threads_created += 1

        processed = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1

            # Handle workout thread creation count
            is_new_workout = False
            if isinstance(record, ParsedWorkout):
                thread_label = f"{self.SOURCE_KIND}:workout:{record.raw_hash[:16]}"
                _, is_new_workout = _get_or_create_thread(conn, thread_label)

            msg_id = self.ingest_row(
                conn, record,
                source_file_id=source_file_id,
                metrics_thread_id=metrics_thread_id,
                clinical_thread_id=clinical_thread_id
            )

            if msg_id:
                report.rows_inserted += 1
                if is_new_workout:
                    report.threads_created += 1
            else:
                report.rows_skipped += 1

            processed += 1  # noqa: SIM113 — used solely for commit-interval bookkeeping
            if processed % COMMIT_EVERY == 0:
                conn.commit()

        conn.commit()

        # Update message count in source_files
        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.SOURCE_KIND, report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
