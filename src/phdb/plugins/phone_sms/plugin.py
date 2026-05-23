"""PhoneSmsPlugin — ingests Android mmssms.db.

Ported from legacy PhoneSmsAdapter.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.chat_upserts import (
    emit_chat_recipient_triples,
    emit_chat_thread_triple,
    upsert_chat_attachment,
    upsert_chat_message,
)
from phdb.formats.phone_sms_sqlite import parse as parse_phone_sms
from phdb.log import get_logger
from phdb.records import ChatMessage

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.plugins.phone_sms")


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
    source_kind: str = "phone-sms",
    file_kind: str = "sqlite",
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


class PhoneSmsPlugin(PhdbSourcePlugin):
    """Android SMS/MMS plugin."""

    SOURCE_KIND = "phone-sms"
    FILE_KIND = "sqlite"
    BATCH_SIZE = 500

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Yield mmssms.db files."""
        if root.is_file():
            if root.name == "mmssms.db":
                yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("mmssms.db")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[ChatMessage]:
        """Yield ChatMessage records from mmssms.db."""
        yield from parse_phone_sms(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ChatMessage,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Upsert ChatMessage + triples + attachments."""
        sf_id = source_file_id if source_file_id is not None else 0

        # Replicate legacy adapter logic
        is_mms = record.is_multipart or record.has_attachments or (
            record.body_text is not None and (
                record.body_text.startswith("(MMS with")
                or record.body_text.startswith("(empty MMS)")
            )
        )
        body_text_source = "phone-mms" if is_mms else "phone-sms"
        rfc_prefix = "phone-mms" if is_mms else "phone-sms"
        platform_id = f"{rfc_prefix}:{record.provenance.raw_hash}"

        if record.sender_address == "self":
            direction = "outbound"
        elif record.sender_address == "unknown":
            direction = "unknown"
        else:
            direction = "inbound"

        # Update record with platform_id
        record = replace(record, platform_id=platform_id)

        msg_id = upsert_chat_message(
            conn, sf_id, record,
            direction=direction,
            body_text_source=body_text_source,
        )

        if msg_id:
            emit_chat_recipient_triples(conn, self.SOURCE_KIND, msg_id, record)
            if record.thread_key:
                emit_chat_thread_triple(conn, self.SOURCE_KIND, msg_id, record.thread_key)
            for att in record.attachments:
                upsert_chat_attachment(conn, msg_id, att)

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
