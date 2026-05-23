"""GoogleTimelinePlugin — Phase 7 brief 030 port of the Google Timeline ingester.

Consumes Google Maps Timeline ``locationhistory.json`` exports
(post-2024 on-device format) and routes each record to one of three
typed tables:

- ``places`` (@type ``Place``) — visits.
- ``travel_actions`` (@type ``TravelAction``) — activity segments.
- ``geo_shapes`` (@type ``GeoShape``) — timelinePath segments;
  per-waypoint rows land in the ``geo_traces`` sidecar.

Per-record routing is driven by ``GeoTrace.trace_type`` set by the
``phdb.formats.google_timeline_json`` parser; the plugin's ``ingest_row``
delegates to ``ingest.ingest_geo_trace`` which looks up the right
typed-table SQL. Every inserted row also gets an ``inThread`` triple
pointing at the single ``google-timeline:lifestream`` thread node so the
test suite's thread-count assertions keep passing.

Replaces the legacy ``phdb.adapters.google_timeline`` module deleted in
the same commit per Phase 0 Q14 (no shim). Reuses the typed tables
introduced in migration 0021 + the ``geo_traces`` sidecar from
migration 0003; no schema changes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.google_timeline_json import parse as parse_google_timeline
from phdb.log import get_logger
from phdb.plugins.google_timeline.ingest import (
    emit_thread_triple,
    ensure_sidecar_tables,
    ingest_geo_trace,
    register_source_file,
)
from phdb.records import GeoTrace

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.google_timeline")


@dataclass
class IngestSummary:
    """Result of one ``run()`` call — mirrors the legacy IngestReport surface."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    threads_created: int = 0
    errors: list[str] = field(default_factory=list)


class GoogleTimelinePlugin(PhdbSourcePlugin):
    """Google Timeline location-history JSON plugin — Phase 7 brief 030 port."""

    SOURCE_KIND = "google-timeline"
    FILE_KIND = "json"
    THREAD_KEY = "lifestream"
    BATCH_SIZE = 1000

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every Timeline JSON.

        Accepts a single ``locationhistory.json`` directly, or a
        directory containing one or more JSON exports. The legacy
        adapter ran against a single file; directory walks here pick up
        archived per-month exports without changing the per-file
        ingest contract.
        """
        if root.is_file():
            if root.suffix.lower() == ".json":
                yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*.json")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[GeoTrace]:
        """Yield GeoTrace records from one Timeline JSON export."""
        yield from parse_google_timeline(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: GeoTrace,
        *,
        source_file_id: int,
    ) -> int | None:
        """Insert one GeoTrace into its typed table; emit ``inThread`` triple.

        Returns the inserted row id (or ``None`` on dedup / unknown
        ``trace_type``). When a row is inserted, an ``inThread`` triple
        is emitted with ``thread_key="lifestream"`` — matching the
        legacy adapter's single-bucket thread shape.
        """
        table, row_id = ingest_geo_trace(conn, record, source_file_id)
        if row_id is None or table is None:
            return None
        emit_thread_triple(
            conn, self.SOURCE_KIND, table, row_id,
            thread_key=self.THREAD_KEY,
        )
        return row_id

    def register_cli(self, parser: Any) -> None:
        """Phase 7: registration via generic ``phdb plugin ingest google_timeline <path>``."""
        return None

    def register_tools(self, server: Any) -> None:
        """No google_timeline-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one Google Timeline JSON export.

        Mirrors the legacy ``GoogleTimelineAdapter.run`` surface — the
        ported tests consume this entry point. ``rows_inserted`` /
        ``rows_skipped`` track every typed-table emission (across
        ``places`` / ``travel_actions`` / ``geo_shapes``) so the test
        assertions stay valid.
        """
        report = IngestSummary(source_path=str(source_path))

        ensure_sidecar_tables(conn)
        source_file_id = register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1

            row_id = self.ingest_row(
                conn, record, source_file_id=source_file_id,
            )
            if row_id is None:
                report.rows_skipped += 1
            else:
                report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        # Track thread creation in the summary — the lifestream thread is
        # created at most once across the run (per-record emit is idempotent).
        thread_label = f"{self.SOURCE_KIND}:{self.THREAD_KEY}"
        thread_row = conn.execute(
            "SELECT id FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
            (thread_label.lower(),),
        ).fetchone()
        if thread_row and report.rows_inserted > 0:
            report.threads_created = 1

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[google-timeline] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
