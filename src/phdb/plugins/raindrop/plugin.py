"""RaindropPlugin — the new-contract pilot port.

Phase 5 of the phdb Plugin Architecture plan. The plugin satisfies
the ``PhdbSourcePlugin`` ABC:

- ``discover(root)`` walks the filesystem for raindrop-shaped files
  (currently the export CSV); yields ``(path, source_kind)`` tuples.
- ``parse(path)`` reuses ``phdb.formats.raindrop.parse`` and yields
  ``BookmarkEvent`` records.
- ``ingest_row(conn, event)`` upserts the WebPage entity then the
  BookmarkAction row with the entity FK.
- ``register_cli(parser)`` adds ``phdb plugin ingest raindrop`` as a
  subcommand — wired by the loader at CLI startup.
- ``register_tools(server)`` registers raindrop-specific MCP tools
  (none yet; Phase 6+ may add).
- ``run(source_path, conn, settings)`` is a convenience wrapping the
  discover/parse/ingest_row sequence for callers (and tests).

This plugin replaces the legacy ``phdb.adapters.raindrop`` module
deleted in the same commit per Phase 0 Q14 (no shim).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.bookmark_upserts import (
    emit_bookmark_triples,
    upsert_bookmark,
    upsert_web_page,
)
from phdb.formats.raindrop import parse as parse_raindrop
from phdb.log import get_logger
from phdb.records import BookmarkEvent

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.plugins.raindrop")


@dataclass
class IngestSummary:
    """Result of one ``run()`` call — mirrors the legacy IngestReport surface."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)


def _register_source_file(
    conn: sqlite3.Connection,
    source_path: Path,
    *,
    source_kind: str = "raindrop",
    file_kind: str = "csv",
) -> int:
    """Insert (or refresh) a source_files row for the given path.

    Equivalent to the legacy Adapter._register_source — copied here so
    plugins don't need to inherit from the deprecated Adapter base.
    Phase 7 will lift this into a shared phdb.core.sources helper as
    more plugins port.
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


class RaindropPlugin(PhdbSourcePlugin):
    """Raindrop.io bookmarks plugin — Phase 5 pilot."""

    SOURCE_KIND = "raindrop"
    FILE_KIND = "csv"
    BATCH_SIZE = 500

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every Raindrop CSV."""
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*.csv")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[BookmarkEvent]:
        """Yield BookmarkEvent records from one Raindrop source file."""
        yield from parse_raindrop(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: BookmarkEvent,
        *,
        source_file_id: int | None = None,
    ) -> int:
        """Upsert the WebPage entity + BookmarkAction row; return bookmark id.

        Also emits the four bookmark-relationship triples
        (taggedWith / inFolder / mentions / relatesTo) per WPEF
        follow-on brief 100.
        """
        sf_id = source_file_id if source_file_id is not None else 0
        wp_id = upsert_web_page(
            conn, record.url, record.normalized_url,
            title=record.title, excerpt=record.excerpt,
            cover_url=record.cover_url,
            sighted=record.date_added or None,
            source_file_id=sf_id or None,
        )
        bm_id = upsert_bookmark(conn, sf_id, record, web_page_id=wp_id)
        emit_bookmark_triples(
            conn,
            bookmark_id=bm_id, web_page_id=wp_id,
            event=record, provenance="raindrop-emitted",
        )
        return bm_id

    def register_cli(self, parser: Any) -> None:
        """Register the ``phdb plugin ingest raindrop`` subcommand."""
        # Phase 5: registration happens via the generic
        # ``phdb plugin ingest <name> <path>`` command (see cli.py).
        # Plugin-specific subcommands land in Phase 7 as plugins port.
        return None

    def register_tools(self, server: Any) -> None:
        """Register MCP tools — raindrop has none Phase 5; future bookmark tools land later."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one source file.

        Mirrors the legacy ``RaindropAdapter.run`` surface — tests +
        ``phdb plugin ingest`` CLI both consume this entry point.
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
            self.ingest_row(conn, record, source_file_id=source_file_id)
            report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[raindrop] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
