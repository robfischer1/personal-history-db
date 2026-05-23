"""ClippingsPlugin — Phase 7 brief 025 port of the vault clippings adapter.

Consumes markdown notes from ``Resources/Clippings/`` and
``Resources/Reddit Posts/`` (frontmatter + body) and writes them to the
``clippings`` typed table (migration 0017). The format parser at
``phdb.formats.clippings_md`` yields ``ClippingRecord`` intermediates;
the plugin's ``ingest_row`` maps each one to a ``clippings`` row via the
helper in ``ingest.py``.

Per migration 0017's design, both ``Quotation`` (Clippings/) and
``Comment`` (Reddit Posts/) live under the same table with the
``schema_type`` column distinguishing them. The format parser reads
``@type`` from frontmatter and defaults to ``Quotation`` — Reddit Posts
that carry ``@type: Comment`` in their frontmatter land with
``schema_type='Comment'`` while everything else lands as ``Quotation``.

The pre-port adapter lived at ``phdb.adapters.clippings`` and was
deleted in the same commit per Phase 0 Q14 (no shim).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.clippings_md import ClippingRecord, parse as parse_clippings_md
from phdb.log import get_logger
from phdb.plugins.clippings.ingest import upsert_clipping

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.clippings")


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
    source_kind: str = "vault-clippings",
    file_kind: str = "md",
) -> int:
    """Insert (or refresh) a source_files row for the given path.

    Mirrors the helper used by raindrop / spotify / goodreads /
    apple_notes_full plugin ports — Phase 7 will lift this into a shared
    ``phdb.core.sources`` helper as more plugins port.
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


class ClippingsPlugin(PhdbSourcePlugin):
    """Vault clippings + reddit-posts markdown plugin — Phase 7 brief 025 port."""

    SOURCE_KIND = "vault-clippings"
    FILE_KIND = "md"
    TARGET_TABLE = "clippings"
    BATCH_SIZE = 100

    def __init__(
        self,
        manifest: PluginManifest | None = None,
    ) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for the clippings root.

        Clippings sources are directories (``Resources/Clippings/`` or
        ``Resources/Reddit Posts/``) walked recursively by the parser —
        ``discover`` yields the directory itself when it contains at
        least one .md file, mirroring how apple_notes_full yields the
        sqlite container path rather than each row.
        """
        if root.is_file():
            return
        if not root.is_dir():
            return
        if any(root.rglob("*.md")):
            yield root, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[ClippingRecord]:
        """Yield ClippingRecord intermediates from one clippings directory."""
        yield from parse_clippings_md(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ClippingRecord,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Insert one ClippingRecord into the ``clippings`` table.

        Returns the inserted row id, or ``None`` when the row was a
        dedup-skip (UNIQUE(source_file_id, raw_hash)).
        """
        sf_id = source_file_id if source_file_id is not None else 0
        return upsert_clipping(conn, sf_id, record)

    def register_cli(self, parser: Any) -> None:
        """Phase 7: registration via generic ``phdb plugin ingest clippings <path>``."""
        return None

    def register_tools(self, server: Any) -> None:
        """No clippings-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one clippings root directory.

        Mirrors the legacy ``ClippingsAdapter.run`` surface — the ported
        tests consume this entry point. ``rows_inserted`` /
        ``rows_skipped`` track ``clippings`` row writes (matching legacy
        semantics).
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
            "[clippings] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
