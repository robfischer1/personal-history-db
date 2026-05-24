"""SmsXmlPlugin — ingests SMS Backup & Restore XML exports.

Ported from legacy SmsXmlAdapter.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.chat_upserts import (
    emit_chat_recipient_triples,
    emit_chat_thread_triple,
    upsert_chat_message,
)
from phdb.formats.smsbr_xml import parse_sms
from phdb.log import get_logger
from phdb.records import ChatMessage

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.plugins.sms_xml")


@dataclass
class IngestSummary:
    """Result of one ``run()`` call."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    threads_created: int = 0  # Added to match legacy IngestReport for tests
    errors: list[str] = field(default_factory=list)
class SmsXmlPlugin(PhdbSourcePlugin):
    """SMS Backup & Restore XML plugin."""

    SOURCE_KIND = "sms-xml"
    FILE_KIND = "xml"
    BATCH_SIZE = 500

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Yield XML files."""
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*.xml")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[ChatMessage]:
        """Yield ChatMessage records from XML."""
        yield from parse_sms(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ChatMessage,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Upsert ChatMessage + triples."""
        sf_id = source_file_id if source_file_id is not None else 0

        # Legacy logic for direction
        direction = "unknown"
        sender_address = None
        sender_name = None

        if record.sender_address == "self":
            direction = "outbound"
        elif record.sender_address:
            direction = "inbound"
            sender_address = record.sender_address
            sender_name = record.sender_name

        msg_id = upsert_chat_message(
            conn, sf_id, record,
            direction=direction,
            body_text_source="sms-br-xml",
            sender_address=sender_address,
            sender_name=sender_name,
        )

        if msg_id:
            emit_chat_recipient_triples(conn, self.SOURCE_KIND, msg_id, record)
            if record.thread_key:
                emit_chat_thread_triple(conn, self.SOURCE_KIND, msg_id, record.thread_key)

        return msg_id

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest runner."""
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        # Count threads before to see if new ones created (for legacy test compatibility)
        before_threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            res = self.ingest_row(conn, record, source_file_id=source_file_id)
            if res:
                report.rows_inserted += 1
            else:
                report.rows_skipped += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        # Count threads after
        after_threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]
        report.threads_created = after_threads - before_threads

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
