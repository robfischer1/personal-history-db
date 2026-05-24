"""StagedMdPlugin — Phase 7 brief 026 port of the staged-markdown ingester.

Consumes a directory of ``.md`` files with YAML frontmatter and writes
one row per file into the ``documents`` typed table. The frontmatter
``@type`` value (filtered against ``phdb.formats.staged_md._VALID_TYPES``)
becomes the ``schema_type`` column — so a single source path can produce
rows with mixed ``schema_type`` values (``CreativeWork``, ``Article``,
``DigitalDocument``, ``Message``, ``SocialMediaPosting``,
``EmailMessage``, ``Book``, ``Observation``) all landing in the same
``documents`` table.

The legacy ``phdb.adapters.staged_md`` adapter set
``target_table='documents'`` on the base ``Adapter`` class so every row
short-circuited the typed-table routing and landed in ``documents``;
this plugin preserves that exact behavior with a direct
``INSERT OR IGNORE INTO documents`` call. No threads are created — the
legacy ``Adapter._link_message_thread`` path was skipped for
document-target adapters, and the ported test asserts
``report.threads_created == 0``.

Replaces the legacy ``phdb.adapters.staged_md`` module deleted in the
same commit per Phase 0 Q14 (no shim). Reuses the ``documents`` typed
table introduced in migration 0008; no schema changes.
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
from phdb.formats.staged_md import parse as parse_staged_md
from phdb.log import get_logger
from phdb.records import DigitalDocument

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.staged_md")


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
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


class StagedMdPlugin(PhdbSourcePlugin):
    """Generic staging-markdown plugin — Phase 7 brief 026 port."""

    SOURCE_KIND = "staged-md"
    FILE_KIND = "md"
    BATCH_SIZE = 200

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Yield (directory, source_kind) tuples for every staging-md cluster.

        A staging-md "source" is a *directory* of ``.md`` files (the
        format parser walks ``rglob('*.md')`` from there). When ``root``
        is itself such a directory — i.e. it contains at least one
        ``.md`` file — yield it directly. Otherwise treat ``root`` as a
        parent and yield each immediate subdirectory that contains
        ``.md`` files.
        """
        if not root.exists():
            return
        if root.is_file():
            # Lone .md file: yield its containing directory.
            if root.suffix.lower() == ".md":
                yield root.parent, self.SOURCE_KIND
            return
        # Directory case: if root itself holds .md files, treat it as a
        # cluster; otherwise scan one level down for child clusters.
        if any(root.glob("*.md")):
            yield root, self.SOURCE_KIND
            return
        for child in sorted(p for p in root.iterdir() if p.is_dir()):
            if any(child.rglob("*.md")):
                yield child, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[DigitalDocument]:
        """Yield DigitalDocument records from one staging-md cluster."""
        yield from parse_staged_md(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: DigitalDocument,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Insert one DigitalDocument into the ``documents`` table.

        Returns the inserted row id, or ``None`` on dedup skip.
        ``schema_type`` is taken from ``record.document_type`` (already
        filtered to ``_VALID_TYPES`` by the format parser); falls back
        to ``CreativeWork`` to match legacy behavior.
        """
        sf_id = source_file_id if source_file_id is not None else 0

        body_text = record.body_text
        body_text_hash = (
            hashlib.sha256(body_text.encode("utf-8")).hexdigest()
            if body_text
            else None
        )
        schema_type = record.document_type or "CreativeWork"

        # Synthetic dedup key derived from the full source path —
        # mirrors the legacy adapter's ``staged-md:<sha256(path)>`` id.
        sub_path = record.provenance.source_path.replace("/", "\\")
        synthetic_msgid = f"staged-md:{hashlib.sha256(sub_path.encode()).hexdigest()}"

        cur = conn.execute(
            _INSERT_DOCUMENT_SQL,
            (
                schema_type,                   # schema_type
                synthetic_msgid,               # rfc822_message_id
                record.title,                  # subject
                record.file_path,              # file_path
                record.file_size,              # file_size
                record.created_date,           # mtime (legacy mapped date_sent -> mtime)
                None,                          # ctime
                body_text,                     # body_text
                record.body_text_source,       # body_text_source
                body_text_hash,                # body_text_hash
                record.provenance.raw_hash,    # raw_hash
                0,                             # is_bulk
                sf_id,                         # source_file_id
                record.bucket,                 # bucket
            ),
        )
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def register_cli(self, parser: Any) -> None:
        """Phase 7: registration via generic ``phdb plugin ingest staged_md <path>``."""
        return None

    def register_tools(self, server: Any) -> None:
        """No staged_md-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one staging-md cluster directory.

        Mirrors the legacy ``StagedMdAdapter.run`` surface — the ported
        test consumes this entry point. No threads are created (the
        legacy adapter routed everything to ``documents`` which
        short-circuited the thread-emission path); ``threads_created``
        stays at 0 for parity with the legacy assertion.
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
            "[staged_md] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
