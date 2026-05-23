"""Google Fit plugin — ingests Fit JSON via streaming parser."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.google_fit_json import parse as parse_fit
from phdb.log import get_logger
from phdb.plugins.google_fit.ingest import (
    emit_thread_triple,
    register_source_file,
    upsert_exercise_action,
    upsert_observation,
)

if TYPE_CHECKING:
    from phdb.records import HealthObservation
    from phdb.settings import Settings

log = get_logger("phdb.plugins.google_fit")

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


class GoogleFitPlugin(PhdbSourcePlugin):
    """Google Fit plugin — Phase 7 port."""

    SOURCE_KIND = "google-fit"
    FILE_KIND = "json"

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every zip or json."""
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        yield root, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[HealthObservation]:
        """Yield parsed records from source path."""
        yield from parse_fit(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: Any,
        *,
        source_file_id: int,
        **kwargs: Any,
    ) -> int | None:
        """Ingest a single record.

        This method is required by the PhdbSourcePlugin contract.
        For Google Fit, we do it inline in run() for thread creation logic,
        but we provide this for completeness.
        """
        raise NotImplementedError("Use run() for Google Fit ingest")

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
        """End-to-end ingest of Google Fit."""
        report = IngestSummary(source_path=str(source_path))

        source_file_id = register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        def _get_or_create_thread(conn: sqlite3.Connection, label: str) -> tuple[int, bool]:
            existing = conn.execute(
                "SELECT id FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
                (label.lower(),),
            ).fetchone()
            if existing:
                return existing[0], False
            from phdb.triples import resolve_node
            return resolve_node(conn, label, "thread"), True

        processed = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1

            prov = record.provenance
            metric = record.observation_type

            meta_dict = dict(record.metadata)
            category = meta_dict.get("category", "metric")
            is_exercise = category == "exercise"

            value_str = meta_dict.get("activity_name") or meta_dict.get("value_str")
            if value_str is None and record.value is not None:
                value_str = str(record.value)
            if value_str is None:
                value_str = "None"

            subject = f"{metric}: {value_str}"[:200]
            body = f"{metric} = {value_str}\nstart={record.date_start} end={record.date_end}"[:1000]

            if is_exercise:
                msg_id = upsert_exercise_action(
                    conn, source_file_id, record, value_str, subject, body
                )
                table = "exercise_actions"
            else:
                msg_id = upsert_observation(
                    conn, source_file_id, record, value_str, subject, body
                )
                table = "observations"

            if msg_id:
                report.rows_inserted += 1

                # Thread per metric
                thread_key = f"{metric}"
                thread_label = f"{self.SOURCE_KIND}:{thread_key}"
                _, is_new = _get_or_create_thread(conn, thread_label)
                if is_new:
                    report.threads_created += 1

                emit_thread_triple(
                    conn, self.SOURCE_KIND, table, msg_id, thread_key
                )
            else:
                report.rows_skipped += 1

            processed += 1
            if processed % COMMIT_EVERY == 0:
                conn.commit()

        conn.commit()

        # Update message count
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
