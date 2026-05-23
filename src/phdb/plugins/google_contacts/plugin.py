"""Google Contacts plugin — ingests vCard exports."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.vcard import parse as parse_vcard
from phdb.log import get_logger
from phdb.plugins.google_contacts.ingest import ingest_record

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.records import Contact
    from phdb.settings import Settings

log = get_logger("phdb.plugins.google_contacts")


@dataclass
class IngestSummary:
    """Result of one ``run()`` call."""

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
    source_kind: str = "google-contacts",
    source_org: str = "Google Takeout",
    file_kind: str = "vcf",
) -> int:
    """Insert or refresh a source_files row."""
    file_size = source_path.stat().st_size if source_path.exists() else None
    cur = conn.execute(
        """INSERT INTO source_files
           (source_path, source_org, file_kind, source_kind, session_uuid,
            file_size, ingested_at)
           VALUES (?, ?, ?, ?, NULL, ?,
                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
           ON CONFLICT(source_path) DO UPDATE
             SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                 file_size = excluded.file_size
           RETURNING id""",
        (str(source_path), source_org, file_kind, source_kind, file_size),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


class GoogleContactsPlugin(PhdbSourcePlugin):
    """Google Contacts plugin — Phase 7 port."""

    SOURCE_KIND = "google-contacts"
    FILE_KIND = "vcf"
    BATCH_SIZE = 500

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every vCard."""
        if root.is_file():
            if root.suffix.lower() in (".vcf", ".zip"):
                yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*.vcf")):
            yield path, self.SOURCE_KIND
        for path in sorted(root.rglob("*.zip")):
            # Only yield zip if it looks like a Google Takeout Contacts zip
            # (or just yield and let parse handle it)
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[tuple[Contact, str]]:
        """Yield Contact records from one vCard source file."""
        yield from parse_vcard(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record_tuple: tuple[Contact, str],
        *,
        source_file_id: int,
    ) -> int | None:
        """Ingest a single Contact record."""
        record, group = record_tuple
        return ingest_record(
            conn, record, group, source_file_id,
            source_kind=self.SOURCE_KIND
        )

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
        """End-to-end ingest of one source file."""
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for record_tuple in self.parse(source_path):
            report.rows_yielded += 1
            row_id = self.ingest_row(conn, record_tuple, source_file_id=source_file_id)
            if row_id is not None:
                report.rows_inserted += 1
            else:
                report.rows_skipped += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

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
