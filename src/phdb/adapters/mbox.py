"""Mbox adapter — ingests Gmail .mbox exports (or any RFC 2822 mbox).

Consumes EmailMessage records from phdb.formats.mbox (pure format parser)
and maps them to AdapterRows for DB insertion.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.formats.mbox import parse
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.mbox")


class MboxAdapter(Adapter):
    """Ingest Gmail .mbox exports (or any RFC 2822 mbox file)."""

    name = "mbox"
    source_kind = "gmail"
    file_kind = "mbox"
    schema_type = "EmailMessage"
    dedup_strategy = DedupStrategy.RFC822_MESSAGE_ID
    batch_size = 500

    def __init__(
        self,
        *,
        source_kind: str = "gmail",
        source_org: str = "Google Takeout",
        max_seconds: float | None = None,
    ) -> None:
        self.source_kind = source_kind
        self.source_org = source_org
        self.max_seconds = max_seconds
        self._resume_offset = 0

    def _register_source(
        self, conn: sqlite3.Connection, source_path: Path
    ) -> int:
        file_size = source_path.stat().st_size if source_path.exists() else None
        cur = conn.execute(
            """INSERT INTO source_files (source_path, source_org, file_kind, source_kind, file_size, ingested_at)
               VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
               ON CONFLICT(source_path) DO UPDATE SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               RETURNING id""",
            (str(source_path), self.source_org, self.file_kind, self.source_kind, file_size),
        )
        row = cur.fetchone()
        assert row is not None
        return int(row[0])

    def compute_raw_hash(self, row: AdapterRow) -> str:
        return super().compute_raw_hash(row)

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestReport:
        source_file_id = self._register_source(conn, source_path)
        self._resume_offset = conn.execute(
            "SELECT COALESCE(MAX(source_byte_offset + source_byte_length), 0) "
            "FROM emails WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()[0]
        if self._resume_offset > 0:
            log.info("[%s] Resuming from byte offset %d", self.name, self._resume_offset)
        return super().run(source_path, conn, settings)

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        n_errors = 0
        n_null_msgid = 0
        t_start = time.time()

        for n_processed, record in enumerate(
            parse(source_path, skip_to_offset=self._resume_offset),
            start=1,
        ):
            if (
                self.max_seconds is not None
                and n_processed % 250 == 0
                and (time.time() - t_start) > self.max_seconds
            ):
                log.info("[%s] Time budget reached after %d messages", self.name, n_processed)
                break

            if record.rfc822_message_id and not record.rfc822_message_id.startswith("synth:"):
                pass
            else:
                n_null_msgid += 1

            gmail_labels_json = (
                json.dumps(list(record.gmail_labels)) if record.gmail_labels else None
            )

            recipients = [
                {"address": r.address, "name": r.name or "", "rtype": r.rtype}
                for r in record.recipients
            ]

            attachments = [
                {
                    "filename": a.filename,
                    "content_type": a.content_type,
                    "content_disposition": a.content_disposition,
                    "size_bytes": a.size_bytes,
                }
                for a in record.attachments
            ]

            yield AdapterRow(
                schema_type="EmailMessage",
                rfc822_message_id=record.rfc822_message_id if not record.rfc822_message_id.startswith("synth:") else None,
                in_reply_to=record.in_reply_to,
                references_chain=record.references_chain,
                gmail_thread_id=record.gmail_thread_id,
                gmail_labels=gmail_labels_json,
                subject=record.subject,
                sender_address=record.sender_address if record.sender_address != "unknown" else None,
                sender_name=record.sender_name,
                sender_domain=record.sender_domain,
                direction="unknown",
                date_sent=record.date_sent or None,
                date_received=record.date_received,
                body_text=record.body_text,
                body_html=record.body_html,
                body_text_source=record.body_text_source,
                is_multipart=int(record.is_multipart),
                has_attachments=int(record.has_attachments),
                attachment_count=record.attachment_count,
                is_bulk=int(record.is_bulk),
                bulk_signal=record.bulk_signal,
                source_byte_offset=record.provenance.source_byte_offset,
                source_byte_length=record.provenance.source_byte_length,
                raw_hash=record.provenance.raw_hash,
                recipients=recipients,
                attachments=attachments,
            )

        if n_null_msgid > 0:
            log.warning("[%s] %d messages had no Message-ID (undeduped)", self.name, n_null_msgid)
        if n_errors > 0:
            log.warning("[%s] %d messages failed to parse", self.name, n_errors)
