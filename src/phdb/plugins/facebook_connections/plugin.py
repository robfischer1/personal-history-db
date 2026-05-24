"""FacebookConnectionsPlugin — ingests FB friends graph.

Satisfies the ``PhdbSourcePlugin`` ABC.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.facebook_connections_html import (
    derive_export_date,
    derive_export_id,
    detect,
    parse,
)
from phdb.log import get_logger
from phdb.plugins.facebook_connections.ingest import (
    post_pass_infer_inactive,
    upsert_connection,
)

if TYPE_CHECKING:
    from phdb.records import Connection
    from phdb.settings import Settings

log = get_logger("phdb.plugins.facebook_connections")


@dataclass
class IngestSummary:
    """Result of one ``run()`` call."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)
class FacebookConnectionsPlugin(PhdbSourcePlugin):
    """Facebook connections plugin."""

    SOURCE_KIND = "facebook-connections"
    FILE_KIND = "zip"
    BATCH_SIZE = 500

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every FB takeout."""
        if root.is_file() and detect(root):
            yield root, self.SOURCE_KIND
            return
        # FB takeout can be a zip or a directory.
        # If it's a directory, we check it.
        if root.is_dir() and detect(root):
             yield root, self.SOURCE_KIND
             return

        # Also look for zips in the root
        if root.is_dir():
            for path in sorted(root.rglob("*.zip")):
                if detect(path):
                    yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[Connection]:
        """Yield Connection records from one Facebook source file."""
        yield from parse(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: Connection,
        *,
        source_file_id: int | None = None,
        export_id: str | None = None,
        export_date: str | None = None,
    ) -> int:
        """Upsert the Connection row; return id."""
        sf_id = source_file_id if source_file_id is not None else 0
        # If export_id/date not provided, we'd have to derive them from record.provenance.source_path
        # but that's expensive. Better to have them passed in.
        eid = export_id or derive_export_id(Path(record.provenance.source_path))
        edate = export_date or derive_export_date(Path(record.provenance.source_path))

        return upsert_connection(
            conn, record,
            export_id=eid,
            export_date=edate,
            source_file_id=sf_id,
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
        """End-to-end ingest of one source file."""
        report = IngestSummary(source_path=str(source_path))

        if not detect(source_path):
            report.errors.append(f"No FB takeout detected at: {source_path}")
            return report

        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        export_date = derive_export_date(source_path)
        export_id = derive_export_id(source_path)

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            self.ingest_row(
                conn, record,
                source_file_id=source_file_id,
                export_id=export_id,
                export_date=export_date,
            )
            report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        # Post-pass: mark missing-from-latest as inactive
        n_inactive = post_pass_infer_inactive(conn, export_id)
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d inferred-inactive",
            self.name, report.rows_yielded, report.rows_inserted, n_inactive,
        )
        return report
