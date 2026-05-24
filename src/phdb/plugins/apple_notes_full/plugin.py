"""AppleNotesFullPlugin — Phase 7 port of the Apple Notes full-body adapter.

Source: ``NoteStore.sqlite`` from a decrypted iPhone backup. The
companion ``apple_dbs`` adapter ingests the truncated ``ZSNIPPET`` body
during its initial Apple-DBs sweep; this plugin runs second and
upgrades those rows with the full text decoded from the gzip-compressed
``ZICNOTEDATA.ZDATA`` protobuf.

UPDATE-oriented: when a ``documents`` row already exists keyed by the
synthetic ``notes:{Z_PK}`` id, the plugin replaces its body_text with
the full proto-decoded text (if longer). Otherwise it inserts a fresh
DigitalDocument row directly.

The proto extraction path is:

    gunzip -> field 2 (Document) -> field 3 (Note) -> field 2 (NoteText)

per ``feedback_apple_notes_proto_path`` — a tested-and-fragile
invariant lifted verbatim from the legacy adapter via
``phdb.formats.apple_notes_sqlite``. Do not "simplify" to 2->2->1.

The pre-port adapter lived at ``phdb.adapters.apple_notes_full`` and
was deleted in the same commit per Phase 0 Q14 (no shim).
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
from phdb.formats.apple_notes_sqlite import parse as parse_apple_notes
from phdb.log import get_logger
from phdb.records import DigitalDocument

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.apple_notes_full")


@dataclass
class IngestSummary:
    """Result of one ``run()`` call — mirrors the legacy IngestReport surface."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)
_INSERT_DOCUMENT_SQL = """\
INSERT OR IGNORE INTO documents (
    schema_type, rfc822_message_id, subject,
    file_path, file_size, mtime, ctime,
    body_text, body_text_source, body_text_hash,
    raw_hash, is_bulk, source_file_id, bucket
) VALUES (
    'DigitalDocument', ?, ?,
    NULL, NULL, ?, NULL,
    ?, ?, ?,
    ?, 0, ?, NULL
)"""


class AppleNotesFullPlugin(PhdbSourcePlugin):
    """Apple Notes full-body ingester — Phase 7 port."""

    SOURCE_KIND = "apple_notes_full"
    FILE_KIND = "sqlite"
    TARGET_TABLE = "documents"
    BATCH_SIZE = 100

    def __init__(
        self,
        manifest: PluginManifest | None = None,
    ) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every NoteStore.sqlite."""
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("NoteStore.sqlite")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[tuple[str, DigitalDocument]]:
        """Yield (synthetic_id, DigitalDocument) tuples from one NoteStore.sqlite.

        The synthetic_id is the ``notes:{Z_PK}`` key used by the apple_dbs
        adapter for dedup, so the caller can use it for UPDATE-vs-INSERT
        logic against the existing ``documents`` row.
        """
        yield from parse_apple_notes(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: tuple[str, DigitalDocument],
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Upgrade an existing ``documents`` row or insert a fresh one.

        Returns the documents row id when a write occurred (INSERT or
        UPDATE), or None when the existing body was already at least as
        long as the candidate body (no-op skip).
        """
        msg_id_key, doc = record
        sf_id = source_file_id if source_file_id is not None else 0

        body_text = doc.body_text or ""
        body_source = doc.body_text_source or "apple-notes-snippet"

        existing = conn.execute(
            "SELECT id, body_text FROM documents WHERE rfc822_message_id = ?",
            (msg_id_key,),
        ).fetchone()

        if existing:
            old_len = len(existing[1] or "")
            new_len = len(body_text)
            if new_len > old_len:
                conn.execute(
                    "UPDATE documents SET body_text = ?, body_text_source = ? WHERE id = ?",
                    (body_text, body_source, existing[0]),
                )
                return int(existing[0])
            return None

        raw_hash = doc.provenance.raw_hash
        body_text_hash = hashlib.sha256(body_text.encode()).hexdigest()
        cur = conn.execute(
            _INSERT_DOCUMENT_SQL,
            (
                msg_id_key,                # rfc822_message_id
                doc.title,                 # subject
                doc.created_date,          # mtime  (legacy used date_sent slot)
                body_text,                 # body_text
                body_source,               # body_text_source
                body_text_hash,            # body_text_hash
                raw_hash,                  # raw_hash
                sf_id,                     # source_file_id
            ),
        )
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def register_cli(self, parser: Any) -> None:
        """Phase 7: registration via generic ``phdb plugin ingest apple_notes_full <path>``."""
        return None

    def register_tools(self, server: Any) -> None:
        """No apple_notes_full-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one NoteStore.sqlite.

        Mirrors the legacy ``AppleNotesFullAdapter.run`` surface — the
        ported tests consume this entry point. ``rows_inserted`` counts
        both fresh INSERTs and body-upgrade UPDATEs (legacy semantics);
        ``rows_skipped`` counts no-op cases where the existing body is
        already at least as long as the candidate body.
        """
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for msg_id_key, doc in self.parse(source_path):
            report.rows_yielded += 1
            row_id = self.ingest_row(
                conn, (msg_id_key, doc),
                source_file_id=source_file_id,
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
            "[apple_notes_full] Done: %d yielded, %d inserted/updated, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
