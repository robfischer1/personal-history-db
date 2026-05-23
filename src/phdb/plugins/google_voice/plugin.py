"""Google Voice plugin — ingests call/text/voicemail HTMLs from Takeout.

Phase 7 of the phdb Plugin Architecture plan.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.google_voice_html import parse as parse_voice
from phdb.log import get_logger
from phdb.records import CallRecord, ChatMessage
from phdb.triples import get_predicate, resolve_node

if TYPE_CHECKING:
    from phdb.core.plugin import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.google_voice")

_MAX_BODY_LEN = 5000


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
    source_kind: str = "google-voice",
    file_kind: str = "html",
) -> int:
    """Insert (or refresh) a source_files row for the given path."""
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


class GoogleVoicePlugin(PhdbSourcePlugin):
    """Google Voice call/text/voicemail plugin."""

    SOURCE_KIND = "google-voice"
    FILE_KIND = "html"
    BATCH_SIZE = 500

    def __init__(self, manifest: PluginManifest) -> None:
        super().__init__(manifest)
        self._predicate_cache: dict[str, int] = {}

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every Google Voice HTML."""
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        if root.suffix == ".zip":
             yield root, self.SOURCE_KIND
             return
        # The parser handles .zip or directory.
        yield root, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[ChatMessage | CallRecord]:
        """Yield ChatMessage or CallRecord records from one Google Voice source."""
        yield from parse_voice(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ChatMessage | CallRecord,
        *,
        source_file_id: int | None = None,
        direction: str = "unknown",
    ) -> tuple[int, str] | None:
        """Insert ChatMessage or CallRecord into typed tables; return (row_id, table_name)."""
        sf_id = source_file_id if source_file_id is not None else 0

        if isinstance(record, ChatMessage):
            body = (record.body_text or "")[:_MAX_BODY_LEN]
            body_hash = hashlib.sha256(body.encode()).hexdigest() if body else None

            cur = conn.execute(
                """INSERT OR IGNORE INTO chat_messages (
                    schema_type, message_key, subject, sender_address, sender_name,
                    direction, date_sent, body_text, body_text_source, body_text_hash,
                    is_multipart, has_attachments, attachment_count,
                    raw_hash, source_file_id
                ) VALUES ('Message', ?, ?, ?, NULL, ?, ?, ?, 'google-voice-html', ?, ?, ?, ?, ?, ?)
                RETURNING id""",
                (
                    f"google-voice:{record.provenance.raw_hash}",
                    f"Text from {record.sender_address}",
                    record.sender_address,
                    direction,
                    record.date_sent or None,
                    body,
                    body_hash,
                    int(record.is_multipart),
                    int(record.has_attachments),
                    record.attachment_count,
                    record.provenance.raw_hash,
                    sf_id,
                ),
            )
            row = cur.fetchone()
            if not row:
                return None
            return int(row[0]), "chat_messages"

        elif isinstance(record, CallRecord):
            kind_label = record.call_type.capitalize() if record.call_type != "voice" else "Received"
            body = f"[{kind_label} call]"
            body_hash = hashlib.sha256(body.encode()).hexdigest()

            cur = conn.execute(
                """INSERT OR IGNORE INTO actions (
                    schema_type, action_key, subject, sender_address,
                    direction, date_performed, body_text, body_text_source, body_text_hash,
                    raw_hash, source_file_id
                ) VALUES ('Action', ?, ?, ?, ?, ?, ?, 'google-voice-html', ?, ?, ?)
                RETURNING id""",
                (
                    f"google-voice:{record.provenance.raw_hash}",
                    f"{kind_label} from {record.caller_address}",
                    record.caller_address,
                    record.direction,
                    record.date_start or None,
                    body,
                    body_hash,
                    record.provenance.raw_hash,
                    sf_id,
                ),
            )
            row = cur.fetchone()
            if not row:
                return None
            return int(row[0]), "actions"

        return None

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
        """End-to-end ingest of Google Voice Takeout."""
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1

            direction = "unknown"
            if isinstance(record, ChatMessage):
                if settings and settings.identity.is_configured:
                    direction = self._infer_direction(record.sender_address, settings)

                # Google Voice parser yields ChatMessage with thread_key
                thread_key = record.thread_key
            else:
                # CallRecord already has direction from parser
                direction = record.direction
                thread_key = f"google-voice:{record.caller_address}"

            res = self.ingest_row(
                conn, record, source_file_id=source_file_id, direction=direction
            )
            if res is None:
                report.rows_skipped += 1
                continue

            row_id, table_name = res
            report.rows_inserted += 1

            if thread_key:
                thread_id, created = self._upsert_thread(conn, thread_key)
                self._link_message_thread(conn, row_id, table_name, thread_id)
                if created:
                    report.threads_created += 1

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
            "[google_voice] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report

    # --------------------------- Private helpers ---------------------------

    def _infer_direction(self, sender_address: str, settings: Settings) -> str:
        """Infer message direction using identity settings."""
        if not sender_address:
            return "unknown"
        if settings.identity.is_me(sender_address):
            return "outbound"
        return "inbound"

    def _resolve_predicate_id(self, conn: sqlite3.Connection, name: str) -> int:
        if name in self._predicate_cache:
            return self._predicate_cache[name]
        pred = get_predicate(conn, name)
        if pred is None:
            raise ValueError(f"Predicate {name!r} not found")
        pid = int(pred["id"])
        self._predicate_cache[name] = pid
        return pid

    def _upsert_thread(self, conn: sqlite3.Connection, thread_key: str) -> tuple[int, bool]:
        label = f"{self.SOURCE_KIND}:{thread_key}"
        existing = conn.execute(
            "SELECT id FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
            (label.lower(),),
        ).fetchone()
        if existing:
            return existing[0], False

        node_id = resolve_node(conn, label, "thread")
        return node_id, True  # type: ignore[return-value]

    def _link_message_thread(
        self, conn: sqlite3.Connection, row_id: int, table_name: str, thread_node_id: int
    ) -> None:
        in_thread_id = self._resolve_predicate_id(conn, "inThread")
        record_label = f"{table_name}:{row_id}"
        record_node_id = resolve_node(
            conn, record_label, "record",
            source_table=table_name, source_id=row_id,
        )

        conn.execute(
            "INSERT OR IGNORE INTO triples"
            " (subject_node_id, predicate_id, object_node_id,"
            "  provenance, source_ref)"
            " VALUES (?, ?, ?, 'plugin', ?)",
            (record_node_id, in_thread_id, thread_node_id, self.SOURCE_KIND),
        )
