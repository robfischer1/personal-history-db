"""Apple Health Backup plugin — ingests Health SQLite databases from iOS backup."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.apple_health_backup import (
    APPLE_EPOCH,
    ParsedRecord,
    ParsedWorkout,
)
from phdb.formats.apple_health_backup import (
    parse as parse_apple_health_backup,
)
from phdb.log import get_logger
from phdb.plugins.apple_health_backup.ingest import (
    emit_thread_triple,
    ensure_sidecar_tables,
    register_source_file,
    upsert_exercise_action,
    upsert_observation,
)

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.plugins.apple_health_backup")

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


class AppleHealthBackupPlugin(PhdbSourcePlugin):
    """Apple Health Backup plugin — Phase 7 port."""

    SOURCE_KIND = "apple-health-backup"
    FILE_KIND = "sqlite"

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every Apple Health backup dir or sqlite."""
        if root.is_file() and root.name == "healthdb_secure.sqlite":
            yield root, self.SOURCE_KIND
            return

        for path in sorted(root.rglob("healthdb_secure.sqlite")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[Any]:
        """Not used directly by this plugin due to complex since_ts logic, see run()."""
        yield from []

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

        return None

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    # ------------------------- Convenience runner --------------------------

    def _resolve_secure_db(self, source_path: Path) -> Path:
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
        """Find the latest date_sent from prior apple-health* source_kinds."""
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

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one Apple Health Backup."""
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
            node_id = resolve_node(conn, label, "thread")
            assert node_id is not None
            return node_id, True

        metrics_thread_id, m_new = _get_or_create_thread(conn, f"{self.SOURCE_KIND}:metrics")
        if m_new:
            report.threads_created += 1

        secure_db = self._resolve_secure_db(source_path)
        meta_db_path = secure_db.parent / "healthdb.sqlite"
        meta_db: Path | None = meta_db_path if meta_db_path.exists() else None

        since_ts = self._last_ingest_ts(conn)
        if since_ts is not None:
            log.info("[%s] Incremental ingest: since_ts=%.0f", self.SOURCE_KIND, since_ts)

        processed = 0
        for record in parse_apple_health_backup(secure_db, meta_db, since_ts=since_ts):
            report.rows_yielded += 1

            is_new_workout = False
            if isinstance(record, ParsedWorkout):
                thread_label = f"{self.SOURCE_KIND}:workout:{record.raw_hash[:16]}"
                _, is_new_workout = _get_or_create_thread(conn, thread_label)

            msg_id = self.ingest_row(
                conn, record,
                source_file_id=source_file_id,
                metrics_thread_id=metrics_thread_id,
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
