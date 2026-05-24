"""GoogleDrivePlugin — Phase 7 port of the Google Drive Takeout ingester.

Source: a Google Takeout ZIP (or extracted directory) containing
``Drive/`` paths with text-bearing documents. Each extracted file
becomes a ``schema_type='DigitalDocument'`` row in the ``documents``
typed table; body text comes from the shared
``phdb.formats.document_extract.EXTRACTORS`` dispatch (PDF, DOCX,
XLSX, IPYNB, HTML, CSV, JSON, TXT, MD, RTF). External-library
formats gracefully degrade when their dependencies are not installed.

The walk + skip-filters + docx-shadows-pdf logic lives in
``phdb.formats.google_drive_zip`` — the plugin layer just consumes
the ``DigitalDocument`` records that parser yields and persists each
one to ``documents`` with the legacy bulk-filename heuristic applied.

Replaces the legacy ``phdb.adapters.google_drive`` module deleted in
the same commit per Phase 0 Q14 (no shim). Reuses migration 0008's
``documents`` table; no schema changes.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.google_drive_zip import parse as parse_google_drive
from phdb.log import get_logger
from phdb.records import DigitalDocument

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.google_drive")


_BULK_FILENAME_PATTERNS = {
    "subscriptions",
    "youtube_subscriptions",
    "watch-history",
    "watch_history",
    "search-history",
    "search_history",
    "liked_videos",
    "playlists",
}


def _is_bulk_file(filename: str) -> bool:
    """Catalog/export dumps are bulk — they crowd out personal content."""
    stem = Path(filename).stem.lower()
    return stem in _BULK_FILENAME_PATTERNS


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
_INSERT_DOCUMENT_SQL = """\
INSERT OR IGNORE INTO documents (
    schema_type, rfc822_message_id, subject,
    file_path, file_size, mtime, ctime,
    body_text, body_text_source, body_text_hash,
    raw_hash, is_bulk, source_file_id, bucket
) VALUES (
    'DigitalDocument', ?, ?,
    ?, ?, ?, NULL,
    ?, ?, ?,
    ?, ?, ?, ?
)"""


class GoogleDrivePlugin(PhdbSourcePlugin):
    """Google Drive Takeout ZIP plugin — Phase 7 brief 027 port."""

    SOURCE_KIND = "google_drive"
    LEGACY_SOURCE_KIND = "google-drive"  # raw_hash + rfc822_message_id prefix
    FILE_KIND = "zip"
    TARGET_TABLE = "documents"
    BATCH_SIZE = 500

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every Takeout ZIP.

        Accepts a single ZIP file directly, or a directory containing
        Google Takeout export ZIPs. The parser opens the ZIP and walks
        its ``Drive/`` entries — discovery here just locates the
        container. Extracted directories are also supported — when
        ``root`` is itself a directory holding Takeout-shaped paths,
        the parser walks it directly.
        """
        if root.is_file():
            if root.suffix.lower() == ".zip":
                yield root, self.SOURCE_KIND
            return
        zips = sorted(root.rglob("*.zip"))
        if zips:
            for path in zips:
                yield path, self.SOURCE_KIND
            return
        # Fallback: treat the directory itself as an extracted Takeout root
        yield root, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[DigitalDocument]:
        """Yield DigitalDocument records from one Google Drive source path."""
        yield from parse_google_drive(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: DigitalDocument,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Insert one DigitalDocument into ``documents``.

        Returns the inserted row id, or ``None`` if the row was a
        duplicate (INSERT OR IGNORE collapsed). Skips records that
        carry no body text — mirrors the legacy adapter's iter_rows
        filter.
        """
        sf_id = source_file_id if source_file_id is not None else 0
        body = record.body_text or ""
        if not body or not body.strip():
            return None

        filename = record.title or ""
        raw_hash = record.provenance.raw_hash
        body_text_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        is_bulk = 1 if _is_bulk_file(filename) else 0

        cur = conn.execute(
            _INSERT_DOCUMENT_SQL,
            (
                f"{self.LEGACY_SOURCE_KIND}:{raw_hash}",  # rfc822_message_id
                filename[:200],                            # subject
                record.file_path,                          # file_path
                record.file_size,                          # file_size
                record.modified_date,                      # mtime (legacy date_sent slot)
                body,                                      # body_text
                record.body_text_source,                   # body_text_source
                body_text_hash,                            # body_text_hash
                raw_hash,                                  # raw_hash
                is_bulk,                                   # is_bulk
                sf_id,                                     # source_file_id
                record.bucket,                             # bucket
            ),
        )
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def register_cli(self, parser: Any) -> None:
        """Phase 7: registration via generic ``phdb plugin ingest google_drive <path>``."""
        return None

    def register_tools(self, server: Any) -> None:
        """No google_drive-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one Google Drive source path.

        Mirrors the legacy ``GoogleDriveAdapter.run`` surface — the
        ported tests consume this entry point. The summary's
        ``rows_inserted`` / ``rows_skipped`` counts track the
        ``documents`` table only.
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

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[google_drive] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
