"""PhoneCallsXmlPlugin — ingests SMS Backup & Restore call-log XML."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.smsbr_xml import parse_calls
from phdb.log import get_logger
from phdb.plugins.phone_calls_xml.ingest import upsert_call

if TYPE_CHECKING:
    from phdb.records import CallRecord
    from phdb.settings import Settings

log = get_logger("phdb.plugins.phone_calls_xml")


@dataclass
class IngestSummary:
    """Result of one ``run()`` call."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)
class PhoneCallsXmlPlugin(PhdbSourcePlugin):
    """Phone calls XML plugin — Phase 7 port."""

    SOURCE_KIND = "phone_calls_xml"
    FILE_KIND = "xml"
    BATCH_SIZE = 500

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every call-log XML."""
        if root.is_file():
            # If it's a file, we assume it's a call-log XML if it ends with .xml
            if root.suffix.lower() == ".xml":
                 yield root, self.SOURCE_KIND
            return

        # Look for calls-*.xml or just *.xml
        for path in sorted(root.rglob("*.xml")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[CallRecord]:
        """Yield CallRecord records from one call-log XML file."""
        yield from parse_calls(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: CallRecord,
        *,
        source_file_id: int | None = None,
    ) -> int:
        """Ingest a single call record."""
        sf_id = source_file_id if source_file_id is not None else 0
        return upsert_call(conn, sf_id, record, source_kind=self.SOURCE_KIND)

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
        """End-to-end ingest of one call-log XML file."""
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
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
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.SOURCE_KIND, report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
