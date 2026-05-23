"""OneDrivePlugin — Phase 7 port of the OneDrive local-files ingester.

Source: a local OneDrive root directory (F:\\OneDrive\\ post-2026-05-13
reorg). Walks the ``Outputs/``, ``Reference/`` and ``Records/`` top-
level pillars. Each text-bearing file becomes a
``schema_type='DigitalDocument'`` row in the ``documents`` typed
table; body text comes from the shared
``phdb.formats.document_extract.EXTRACTORS`` dispatch (PDF, DOCX,
XLSX, IPYNB, HTML, CSV, JSON, TXT, MD, RTF). External-library
formats gracefully degrade when their dependencies are not installed.

``Reference/`` has a body-extract allowlist: active-pursuit subdirs
get full body extraction; everything else yields metadata-only rows
(``body_text=None``, ``is_bulk=1``) per the OneDrive Reference/
allowlist policy. The list lives in
``phdb.formats.onedrive_local.REFERENCE_BODY_ALLOWLIST`` and must
not be widened without a propagation pass.

The walk + skip-filters + allowlist logic lives in
``phdb.formats.onedrive_local`` — the plugin layer just consumes the
``DigitalDocument`` records that parser yields and persists each one
to ``documents`` with ``is_bulk`` derived from the file's
top-level-pillar slot.

Replaces the legacy ``phdb.adapters.onedrive`` module deleted in the
same commit per Phase 0 Q14 (no shim). Reuses migration 0008's
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
from phdb.formats.onedrive_local import parse as parse_onedrive
from phdb.log import get_logger
from phdb.records import DigitalDocument

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.onedrive")


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
    source_kind: str = "onedrive",
    file_kind: str = "local-files",
) -> int:
    """Insert (or refresh) a source_files row for the given path.

    Mirrors the helper used by google_drive / apple_notes_full /
    raindrop / spotify / amazon plugin ports — Phase 7 will lift this
    into a shared phdb.core.sources helper as more plugins port.
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


_INSERT_DOCUMENT_SQL = """\
INSERT OR IGNORE INTO documents (
    schema_type, rfc822_message_id, subject,
    file_path, file_size, mtime, ctime,
    body_text, body_text_source, body_text_hash,
    raw_hash, is_bulk, source_file_id, bucket
) VALUES (
    'DigitalDocument', ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?, ?
)"""


class OneDrivePlugin(PhdbSourcePlugin):
    """OneDrive local sync directory plugin — Phase 7 brief 028 port."""

    SOURCE_KIND = "onedrive"
    FILE_KIND = "local-files"
    TARGET_TABLE = "documents"
    BATCH_SIZE = 500

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Yield the OneDrive root path itself; the parser walks from there.

        OneDrive is a single rooted directory tree, not a discoverable
        collection of files — there is no per-file split into source
        units. The parser opens the root and walks its ``Outputs/``,
        ``Reference/``, ``Records/`` subtrees directly.
        """
        if root.is_dir():
            yield root, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[DigitalDocument]:
        """Yield DigitalDocument records from one OneDrive root directory."""
        yield from parse_onedrive(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: DigitalDocument,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Insert one DigitalDocument into ``documents``.

        Returns the inserted row id, or ``None`` if the row was a
        duplicate (INSERT OR IGNORE collapsed). Records under
        ``Reference/`` are marked ``is_bulk=1``; everything else is
        ``is_bulk=0`` regardless of body presence (Reference/-allowlist
        files still flow through with body text but stay flagged bulk).
        Metadata-only records (no body_text) are kept — mirrors the
        legacy adapter, which preserved Reference/ metadata even when
        bodies were skipped by the allowlist.
        """
        sf_id = source_file_id if source_file_id is not None else 0

        # Reconstruct the synthetic msg_id used by the legacy adapter so
        # the rfc822_message_id slot stays stable across the port.
        path_hash = hashlib.sha1(
            record.provenance.source_path.encode()
        ).hexdigest()[:16]
        msg_id = f"onedrive:{path_hash}"

        rel_parts = Path(record.file_path).parts if record.file_path else ()
        is_bulk = 1 if rel_parts and rel_parts[0] == "Reference" else 0

        body = record.body_text
        body_text_hash = (
            hashlib.sha256(body.encode("utf-8")).hexdigest()
            if body
            else None
        )

        cur = conn.execute(
            _INSERT_DOCUMENT_SQL,
            (
                msg_id,                                    # rfc822_message_id
                record.title,                              # subject
                record.file_path,                          # file_path
                record.file_size,                          # file_size
                record.modified_date,                      # mtime
                record.created_date,                       # ctime
                body,                                      # body_text
                record.body_text_source,                   # body_text_source
                body_text_hash,                            # body_text_hash
                record.provenance.raw_hash,                # raw_hash
                is_bulk,                                   # is_bulk
                sf_id,                                     # source_file_id
                record.bucket,                             # bucket
            ),
        )
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid)

    def register_cli(self, parser: Any) -> None:
        """Phase 7: registration via generic ``phdb plugin ingest onedrive <path>``."""
        return None

    def register_tools(self, server: Any) -> None:
        """No onedrive-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one OneDrive root directory.

        Mirrors the legacy ``OneDriveAdapter.run`` surface — the
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
            "[onedrive] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
