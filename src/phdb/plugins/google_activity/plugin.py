"""GoogleActivityPlugin — Google Takeout MyActivity HTML ingester.

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
from phdb.formats.google_activity_html import parse as parse_google_activity
from phdb.log import get_logger
from phdb.plugins.google_activity.ingest import upsert_web_activity

if TYPE_CHECKING:
    from phdb.records import WebActivity
    from phdb.settings import Settings

log = get_logger("phdb.plugins.google_activity")


@dataclass
class IngestSummary:
    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)


class GoogleActivityPlugin(PhdbSourcePlugin):
    """Google My Activity + YouTube history plugin."""

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield paths to MyActivity.html or *history.html."""
        if root.is_file():
            if root.suffix == ".zip" or root.name.endswith("MyActivity.html") or root.name.endswith("history.html"):
                yield root, "google-activity"
            return

        # Zips are common for Google Takeout
        for path in sorted(root.rglob("*.zip")):
             yield path, "google-activity"

        for path in sorted(root.rglob("MyActivity.html")):
            yield path, "google-activity"
        for path in sorted(root.rglob("*history.html")):
            if "YouTube" in str(path):
                yield path, "google-activity"

    def parse(self, path: Path) -> Iterator[WebActivity]:
        yield from parse_google_activity(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: WebActivity,
        *,
        source_file_id: int | None = None,
    ) -> int:
        sf_id = source_file_id if source_file_id is not None else 0
        return upsert_web_activity(conn, sf_id, record)

    def register_cli(self, parser: Any) -> None:
        """Register subcommands like --backfill-web-pages."""
        # For now, phdb CLI handles discovery.
        # Future: add the backfill command here.
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
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind="google-activity", file_kind="html",
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            row_id = self.ingest_row(conn, record, source_file_id=source_file_id)
            if row_id > 0:
                report.rows_inserted += 1
            else:
                report.rows_skipped += 1

            batch_count += 1
            if batch_count >= 500:
                conn.commit()
                batch_count = 0

        conn.commit()
        return report
