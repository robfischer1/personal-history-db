"""Adapter base class and AdapterRow.

Every ingester subclasses Adapter and implements iter_rows(). The base class
provides the run() method that handles source_files registration, batched
INSERT OR IGNORE, commit cadence, and progress logging — the ~50 lines of
boilerplate that every legacy ingester repeats.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import IdentitySettings, Settings

log = get_logger("phdb.adapters")


class DedupStrategy(Enum):
    """How the adapter produces dedup keys for INSERT OR IGNORE."""

    RFC822_MESSAGE_ID = "rfc822"
    PLATFORM_SYNTHETIC = "synthetic"
    SOURCE_POSITION = "position"
    CONTENT_HASH = "hash"


@dataclass
class AdapterRow:
    """A single row to insert into the messages table (or a domain table)."""

    schema_type: str = "Message"
    rfc822_message_id: str | None = None
    in_reply_to: str | None = None
    references_chain: str | None = None
    gmail_thread_id: str | None = None
    gmail_labels: str | None = None
    subject: str | None = None
    sender_address: str | None = None
    sender_name: str | None = None
    sender_domain: str | None = None
    direction: str = "unknown"
    date_sent: str | None = None
    date_received: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    body_text_source: str | None = None
    is_multipart: int = 0
    has_attachments: int = 0
    attachment_count: int = 0
    is_bulk: int = 0
    bulk_signal: str | None = None
    source_byte_offset: int | None = None
    source_byte_length: int | None = None
    raw_hash: str | None = None
    body_text_hash: str | None = None

    # AI session message fields (all default None — ignored by existing adapters)
    kind: str | None = None
    role: str | None = None
    parent_uuid: str | None = None
    tool_name: str | None = None
    tool_use_id: str | None = None
    model: str | None = None
    payload: str | None = None  # JSON

    # AI session thread fields
    thread_metadata: str | None = None  # JSON
    thread_cwd: str | None = None

    # Document-specific fields (used when target_table='documents')
    file_path: str | None = None
    file_size: int | None = None
    ctime: str | None = None
    bucket: str | None = None

    recipients: list[dict[str, str]] = field(default_factory=list)
    attachments: list[dict[str, str | int | None]] = field(default_factory=list)
    thread_key: str | None = None
    extra: dict[str, object] = field(default_factory=dict)


@dataclass
class IngestReport:
    """Summary returned by Adapter.run()."""

    adapter_name: str
    source_path: str
    source_file_id: int
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    threads_created: int = 0
    errors: list[str] = field(default_factory=list)


_INSERT_MESSAGE_SQL = """\
INSERT OR IGNORE INTO messages (
    schema_type, rfc822_message_id, in_reply_to, references_chain,
    gmail_thread_id, gmail_labels,
    subject, sender_address, sender_name, sender_domain,
    direction, date_sent, date_received,
    body_text, body_html, body_text_source,
    is_multipart, has_attachments, attachment_count,
    is_bulk, bulk_signal,
    source_file_id, source_byte_offset, source_byte_length,
    raw_hash, body_text_hash,
    kind, role, parent_uuid, tool_name, tool_use_id, model, payload
) VALUES (
    ?, ?, ?, ?,
    ?, ?,
    ?, ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?,
    ?, ?, ?,
    ?, ?,
    ?, ?, ?,
    ?, ?,
    ?, ?, ?, ?, ?, ?, ?
)"""

_INSERT_RECIPIENT_SQL = """\
INSERT INTO recipients (message_id, address, name, rtype)
VALUES (?, ?, ?, ?)"""

_INSERT_ATTACHMENT_SQL = """\
INSERT INTO attachments (schema_type, message_id, filename, content_type,
    content_disposition, size_bytes, on_disk_path, content_hash)
VALUES ('DigitalDocument', ?, ?, ?, ?, ?, ?, ?)"""

_INSERT_DOCUMENT_SQL = """\
INSERT OR IGNORE INTO documents (
    schema_type, rfc822_message_id, subject,
    file_path, file_size, mtime, ctime,
    body_text, body_text_source, body_text_hash,
    raw_hash, is_bulk, source_file_id, bucket
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


class Adapter(ABC):
    """Base class for personal-history-db ingesters."""

    name: str
    source_kind: str
    file_kind: str
    schema_type: str = "Message"
    target_table: str = "messages"
    dedup_strategy: DedupStrategy = DedupStrategy.CONTENT_HASH
    batch_size: int = 500
    _settings: Settings | None = None

    def owner_sender(self, platform: str) -> tuple[str, str]:
        """Return (sender_address, sender_name) for the database owner.

        Uses the first entry in IdentitySettings.owner_names when available,
        falls back to "owner" when no identity is configured.
        """
        if self._settings and self._settings.identity.owner_names:
            name = next(iter(self._settings.identity.owner_names))
            return f"{platform}:{name.lower()}", name
        return f"{platform}:owner", "owner"

    @abstractmethod
    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        """Yield structured rows from the source file."""

    def parse_date(self, raw: str) -> str | None:
        """Parse a raw date string to ISO-8601. Override for source-specific formats."""
        return raw

    def compute_raw_hash(self, row: AdapterRow) -> str:
        """Compute the dedup hash for a row."""
        seed = f"{self.source_kind}|{row.rfc822_message_id or ''}|{row.date_sent or ''}|{(row.body_text or '')[:200]}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def detect_bulk(self, row: AdapterRow) -> tuple[bool, str | None]:
        """Detect if a row is bulk/automated. Override for source-specific rules."""
        return False, None

    def infer_direction(self, row: AdapterRow, identity: IdentitySettings) -> str:
        """Infer message direction from sender address and identity config."""
        if not row.sender_address:
            return "unknown"
        if identity.is_me(row.sender_address):
            if row.recipients and any(identity.is_me(r.get("address", "")) for r in row.recipients):
                return "self"
            return "outbound"
        return "inbound"

    def compute_session_uuid(self, source_path: Path) -> str | None:
        """Compute a stable session UUID for this source, if available.

        Override in adapters that ingest one-file-per-session formats (e.g.
        Claude Code's `<session-uuid>.jsonl`). Returning a non-None value
        opts the source into UUID-based dedup at the source_files level —
        the same session ingested under a renamed/moved path will update
        the existing row rather than register a new one.

        Default: None (path-based dedup, current behavior).
        """
        return None

    def validate_source_path(self, source_path: Path) -> None:
        """Raise to refuse ingest of a path that violates an adapter rule.

        Default: no-op. Adapters that have a canonical source location can
        override to reject other locations (see ClaudeCodeAdapter).
        """
        return None

    def _register_source(
        self, conn: sqlite3.Connection, source_path: Path
    ) -> int:
        """Register the source file and return its ID.

        Uses a dual-conflict-target UPSERT so that adapters which provide a
        session_uuid via compute_session_uuid() get UUID-based dedup, while
        adapters that don't keep the original path-based behavior.
        """
        session_uuid = self.compute_session_uuid(source_path)
        cur = conn.execute(
            """INSERT INTO source_files (source_path, source_org, file_kind, source_kind, session_uuid, ingested_at)
               VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
               ON CONFLICT(source_path) DO UPDATE
                 SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                     session_uuid = COALESCE(source_files.session_uuid, excluded.session_uuid)
               ON CONFLICT(source_kind, session_uuid) WHERE session_uuid IS NOT NULL
                 DO UPDATE SET source_path = excluded.source_path,
                               ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               RETURNING id""",
            (str(source_path), None, self.file_kind, self.source_kind, session_uuid),
        )
        row = cur.fetchone()
        assert row is not None
        return int(row[0])

    def _insert_row(
        self, conn: sqlite3.Connection, row: AdapterRow, source_file_id: int
    ) -> int | None:
        """Insert a single row. Routes to messages or documents based on target_table."""
        if self.target_table == "documents":
            return self._insert_document(conn, row, source_file_id)
        cur = conn.execute(
            _INSERT_MESSAGE_SQL,
            (
                row.schema_type, row.rfc822_message_id, row.in_reply_to, row.references_chain,
                row.gmail_thread_id, row.gmail_labels,
                row.subject, row.sender_address, row.sender_name, row.sender_domain,
                row.direction, row.date_sent, row.date_received,
                row.body_text, row.body_html, row.body_text_source,
                row.is_multipart, row.has_attachments, row.attachment_count,
                row.is_bulk, row.bulk_signal,
                source_file_id, row.source_byte_offset, row.source_byte_length,
                row.raw_hash, row.body_text_hash,
                row.kind, row.role, row.parent_uuid, row.tool_name, row.tool_use_id,
                row.model, row.payload,
            ),
        )
        if cur.rowcount == 0:
            return None
        return cur.lastrowid

    def _insert_document(
        self, conn: sqlite3.Connection, row: AdapterRow, source_file_id: int
    ) -> int | None:
        """Insert a single row into the documents typed table. Returns doc ID or None if skipped."""
        cur = conn.execute(
            _INSERT_DOCUMENT_SQL,
            (
                row.schema_type, row.rfc822_message_id, row.subject,
                row.file_path, row.file_size, row.date_sent, row.ctime,
                row.body_text, row.body_text_source, row.body_text_hash,
                row.raw_hash, row.is_bulk, source_file_id, row.bucket,
            ),
        )
        if cur.rowcount == 0:
            return None
        return cur.lastrowid

    def _insert_sidecars(
        self, conn: sqlite3.Connection, message_id: int, row: AdapterRow
    ) -> None:
        """Insert recipients and attachments for a message."""
        for r in row.recipients:
            conn.execute(
                _INSERT_RECIPIENT_SQL,
                (message_id, r.get("address", ""), r.get("name"), r.get("rtype", "to")),
            )
        for a in row.attachments:
            conn.execute(
                _INSERT_ATTACHMENT_SQL,
                (
                    message_id,
                    a.get("filename"),
                    a.get("content_type"),
                    a.get("content_disposition"),
                    a.get("size_bytes"),
                    a.get("on_disk_path"),
                    a.get("content_hash"),
                ),
            )

    def _upsert_thread(
        self,
        conn: sqlite3.Connection,
        thread_key: str,
        participants: list[str] | None = None,
        metadata: str | None = None,
        cwd: str | None = None,
    ) -> tuple[int, bool]:
        """Find or create a thread by (source_kind, thread_key). Returns (thread_id, created)."""
        existing = conn.execute(
            "SELECT id FROM threads WHERE source_kind = ? AND thread_key = ?",
            (self.source_kind, thread_key),
        ).fetchone()
        if existing:
            return existing[0], False
        cur = conn.execute(
            """INSERT INTO threads (schema_type, source_kind, thread_key, message_count,
                                   participants, metadata, cwd)
               VALUES ('Conversation', ?, ?, 0, ?, ?, ?)""",
            (
                self.source_kind, thread_key,
                json.dumps(sorted(participants)) if participants else None,
                metadata,
                cwd,
            ),
        )
        return cur.lastrowid, True  # type: ignore[return-value]

    def _link_message_thread(
        self, conn: sqlite3.Connection, message_id: int, thread_id: int
    ) -> None:
        """Insert into message_threads bridge table."""
        conn.execute(
            "INSERT OR IGNORE INTO message_threads (message_id, thread_id) VALUES (?, ?)",
            (message_id, thread_id),
        )

    def _update_thread_aggregates(
        self, conn: sqlite3.Connection, thread_id: int
    ) -> None:
        """Refresh message_count, date_first, date_last on a thread."""
        conn.execute(
            """UPDATE threads SET
                   message_count = (SELECT COUNT(*) FROM message_threads WHERE thread_id = ?),
                   date_first = (SELECT MIN(m.date_sent) FROM messages m
                                 JOIN message_threads mt ON mt.message_id = m.id
                                 WHERE mt.thread_id = ? AND m.date_sent IS NOT NULL),
                   date_last  = (SELECT MAX(m.date_sent) FROM messages m
                                 JOIN message_threads mt ON mt.message_id = m.id
                                 WHERE mt.thread_id = ? AND m.date_sent IS NOT NULL)
               WHERE id = ?""",
            (thread_id, thread_id, thread_id, thread_id),
        )

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestReport:
        """Full ingest pipeline: register source -> iter_rows -> batch insert -> commit."""
        report = IngestReport(
            adapter_name=self.name,
            source_path=str(source_path),
            source_file_id=0,
        )

        self._settings = settings
        self.validate_source_path(source_path)
        source_file_id = self._register_source(conn, source_path)
        report.source_file_id = source_file_id
        log.info("[%s] Source registered: id=%d path=%s", self.name, source_file_id, source_path)

        _touched_threads: set[int] = set()
        _is_document = self.target_table == "documents"
        batch_count = 0
        for row in self.iter_rows(source_path):
            report.rows_yielded += 1

            if not row.raw_hash:
                row.raw_hash = self.compute_raw_hash(row)
            if row.body_text and not row.body_text_hash:
                row.body_text_hash = hashlib.sha256(row.body_text.encode("utf-8")).hexdigest()

            is_bulk, signal = self.detect_bulk(row)
            if is_bulk:
                row.is_bulk = 1
                row.bulk_signal = signal

            if not _is_document:
                has_identity = (
                    settings.identity.owner_names
                    or settings.identity.owner_emails
                    or settings.identity.owner_phones
                    or settings.identity.owner_handles
                )
                if row.direction == "unknown" and has_identity:
                    row.direction = self.infer_direction(row, settings.identity)

            row_id = self._insert_row(conn, row, source_file_id)
            if row_id is None:
                report.rows_skipped += 1
                continue

            report.rows_inserted += 1

            if not _is_document:
                self._insert_sidecars(conn, row_id, row)

                if row.thread_key:
                    thread_id, created = self._upsert_thread(
                        conn, row.thread_key,
                        metadata=row.thread_metadata,
                        cwd=row.thread_cwd,
                    )
                    self._link_message_thread(conn, row_id, thread_id)
                    if created:
                        report.threads_created += 1
                        _touched_threads.add(thread_id)
                    else:
                        _touched_threads.add(thread_id)

            batch_count += 1
            if batch_count >= self.batch_size:
                conn.commit()
                batch_count = 0

        conn.commit()

        if _touched_threads:
            for tid in _touched_threads:
                self._update_thread_aggregates(conn, tid)
            conn.commit()

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.name,
            report.rows_yielded,
            report.rows_inserted,
            report.rows_skipped,
        )
        return report
