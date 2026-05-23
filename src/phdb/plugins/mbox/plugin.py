"""mbox plugin — ingests RFC 5322 mbox archives."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.mbox import parse as parse_mbox
from phdb.log import get_logger
from phdb.plugins.mbox.ingest import ingest_record

if TYPE_CHECKING:
    from phdb.records import EmailMessage
    from phdb.settings import Settings

log = get_logger("phdb.plugins.mbox")


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
    source_kind: str = "gmail",
    source_org: str = "Google Takeout",
    file_kind: str = "mbox",
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


class MboxPlugin(PhdbSourcePlugin):
    """Mbox plugin — Phase 7 port."""

    def __init__(
        self,
        *,
        source_kind: str = "gmail",
        source_org: str = "Google Takeout",
    ) -> None:
        self.source_kind = source_kind
        self.source_org = source_org

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every mbox."""
        if root.is_file():
            if root.suffix.lower() == ".mbox":
                yield root, self.source_kind
            return
        for path in sorted(root.rglob("*.mbox")):
            yield path, self.source_kind

    def parse(self, path: Path, *, skip_to_offset: int = 0) -> Iterator[EmailMessage]:
        """Yield EmailMessage records from one mbox file."""
        yield from parse_mbox(path, skip_to_offset=skip_to_offset)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: EmailMessage,
        *,
        source_file_id: int,
        settings: Settings | None = None,
    ) -> int:
        """Ingest a single email record."""
        return ingest_record(
            conn, record, source_file_id,
            source_kind=self.source_kind, settings=settings
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
        """End-to-end ingest of one mbox file."""
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.source_kind,
            source_org=self.source_org,
        )
        report.source_file_id = source_file_id

        # Resume logic
        resume_offset = conn.execute(
            "SELECT COALESCE(MAX(source_byte_offset + source_byte_length), 0) "
            "FROM emails WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()[0]
        
        if resume_offset > 0:
            log.info("[mbox] Resuming from byte offset %d", resume_offset)

        batch_count = 0
        for record in self.parse(source_path, skip_to_offset=resume_offset):
            report.rows_yielded += 1
            self.ingest_row(conn, record, source_file_id=source_file_id, settings=settings)
            report.rows_inserted += 1

            batch_count += 1
            if batch_count >= 500:  # BATCH_SIZE
                conn.commit()
                batch_count = 0

        conn.commit()
        
        # Update message count in source_files
        conn.execute(
            "UPDATE source_files SET message_count = "
            "(SELECT COUNT(*) FROM emails WHERE source_file_id = ?) "
            "WHERE id = ?",
            (source_file_id, source_file_id),
        )
        conn.commit()

        log.info(
            "[mbox] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
