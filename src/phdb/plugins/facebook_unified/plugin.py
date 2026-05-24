"""Facebook Unified plugin — Phase 7 port.

Satisfies the ``PhdbSourcePlugin`` ABC.
"""

from __future__ import annotations

import sqlite3
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.facebook_html import parse as parse_facebook
from phdb.log import get_logger
from phdb.plugins.facebook_unified.ingest import ingest_facebook_record

if TYPE_CHECKING:
    from phdb.records import ChatMessage, Reaction, SocialPost
    from phdb.settings import Settings

log = get_logger("phdb.plugins.facebook_unified")


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
class FacebookUnifiedPlugin(PhdbSourcePlugin):
    """Facebook Unified plugin — messenger, posts, residuals."""

    SOURCE_KIND = "facebook"
    FILE_KIND = "zip"
    BATCH_SIZE = 500

    def __init__(self, manifest: Any = None) -> None:
        super().__init__(manifest)

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every Facebook ZIP."""
        if root.is_file():
            if root.suffix.lower() == ".zip":
                try:
                    with zipfile.ZipFile(root) as zf:
                        # Check for the characteristic subtree
                        if any(n.startswith("your_facebook_activity/") for n in zf.namelist()):
                            yield root, self.SOURCE_KIND
                except zipfile.BadZipFile:
                    pass
            return
        for path in sorted(root.rglob("*.zip")):
            yield from self.discover(path)

    def parse(self, path: Path) -> Iterator[ChatMessage | SocialPost | Reaction]:
        """Yield records from one Facebook export ZIP."""
        yield from parse_facebook(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ChatMessage | SocialPost | Reaction,
        *,
        source_file_id: int,
        settings: Settings | None = None,
    ) -> int | None:
        """Ingest a single Facebook record."""
        return ingest_facebook_record(
            conn, record, source_file_id, settings=settings
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
        """End-to-end ingest of one Facebook export ZIP."""
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        # To track threads created, we'd need to count resolve_node calls for kind='thread'
        # that actually insert. For now, we'll just report rows.

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            row_id = self.ingest_row(
                conn, record, source_file_id=source_file_id, settings=settings
            )

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
        # Since this plugin emits to multiple tables, we aggregate them
        # (Actually source_files.message_count is usually just an informative hint)

        log.info(
            "[facebook_unified] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
