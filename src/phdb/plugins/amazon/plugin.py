"""AmazonPlugin — Phase 7 brief 022 port of the Amazon Request-My-Data ZIP ingester.

Consumes Amazon's "Request Your Data" export ZIP (CSVs + JSON across 8
data streams) and routes each record to one of four typed tables:

- ``products`` (@type ``Product``) — Wishlist items.
- ``order_actions`` (@type ``OrderAction``) — Order History, Digital
  Content Orders, Kindle Orders.
- ``reviews`` (@type ``Review``) — Customer reviews.
- ``watch_actions`` (@type ``WatchAction``) — Prime Video watch events.

Per-record routing is driven by ``AmazonRecord.schema_type`` set by the
``phdb.formats.amazon_zip`` parser; the plugin's ``ingest_row`` looks
up the right typed-table SQL via ``ingest.ingest_amazon_record``. Each
row also gets an ``inThread`` triple pointing at an
``amazon:<stream>``-keyed thread node so existing test assertions over
thread-node counts and inThread-triple density keep passing.

Replaces the legacy ``phdb.adapters.amazon`` module deleted in the
same commit per Phase 0 Q14 (no shim). Reuses the typed tables
introduced in migration 0021; no schema changes.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.amazon_zip import AmazonRecord
from phdb.formats.amazon_zip import parse as parse_amazon
from phdb.log import get_logger
from phdb.plugins.amazon.ingest import emit_thread_triple, ingest_amazon_record

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.amazon")


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


def _register_source_file(
    conn: sqlite3.Connection,
    source_path: Path,
    *,
    source_kind: str = "amazon",
    file_kind: str = "zip",
) -> int:
    """Insert (or refresh) a source_files row for the given path.

    Equivalent to the legacy ``Adapter._register_source`` — copied here
    so the plugin doesn't need to inherit the deprecated ``Adapter``
    base. Phase 7 lifts this into a shared helper once enough plugins
    port.
    """
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


class AmazonPlugin(PhdbSourcePlugin):
    """Amazon Request-My-Data ZIP plugin — Phase 7 brief 022 port."""

    SOURCE_KIND = "amazon"
    FILE_KIND = "zip"
    BATCH_SIZE = 500

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every Amazon ZIP.

        Accepts a single ZIP file directly, or a directory containing
        Amazon export ZIPs. The parser opens the ZIP and walks its
        ``HANDLERS`` table — discovery here just locates the container.
        """
        if root.is_file():
            if root.suffix.lower() == ".zip":
                yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*.zip")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[AmazonRecord]:
        """Yield AmazonRecord intermediates from one Amazon export ZIP."""
        yield from parse_amazon(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: AmazonRecord,
        *,
        source_file_id: int,
    ) -> int | None:
        """Insert one AmazonRecord into its typed table; emit inThread triple.

        Returns the inserted row id (or ``None`` on dedup / unhandled
        schema_type). When a row is inserted, an ``inThread`` triple is
        emitted with ``thread_key="amazon:<stream>"`` — matching the
        legacy adapter's per-stream thread bucketing.
        """
        table, row_id = ingest_amazon_record(conn, record, source_file_id)
        if row_id is None or table is None:
            return None
        emit_thread_triple(
            conn, self.SOURCE_KIND, table, row_id,
            thread_key=f"amazon:{record.stream}",
        )
        return row_id

    def register_cli(self, parser: Any) -> None:
        """Phase 7: registration via generic ``phdb plugin ingest amazon <path>``."""
        return None

    def register_tools(self, server: Any) -> None:
        """No amazon-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one Amazon Request-My-Data ZIP.

        Mirrors the legacy ``AmazonAdapter.run`` surface — the ported
        tests consume this entry point. ``rows_inserted`` /
        ``rows_skipped`` track every typed-table emission (across
        ``products`` / ``order_actions`` / ``reviews`` /
        ``watch_actions``) so the test assertions stay valid.
        """
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
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

        log.info(
            "[amazon] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
