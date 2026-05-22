"""iMessage adapter — ingests imessage-exporter HTML output.

Source: a directory of .html files produced by ``imessage-exporter``, one per
contact or group chat.  Filenames encode participants (comma-separated).

Two-pass strategy:
  Pass 1: 1-on-1 files — build a contact display-name -> phone lookup.
  Pass 2: group files — resolve display names via the lookup.

Threads are created per conversation file (keyed on sorted participants).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.formats.imessage_html import (
    discover_html_files,
    is_bulk_sender,
    normalize_addr,
    parse_file,
    parse_filename_participants,
)
from phdb.log import get_logger
from phdb.records import ChatMessage

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.imessage")

_MAX_BODY_LEN = 50_000


def _record_to_row(
    record: ChatMessage,
    direction: str,
) -> AdapterRow:
    """Map a ChatMessage record to an AdapterRow for DB insert."""
    body = record.body_text
    if body and len(body) > _MAX_BODY_LEN:
        body = body[:_MAX_BODY_LEN]

    body_hash = hashlib.sha256(body.encode()).hexdigest() if body else None

    sender_domain: str | None = None
    if record.sender_address and "@" in record.sender_address:
        sender_domain = record.sender_address.split("@", 1)[1]

    bulk_flag, bulk_sig = is_bulk_sender(record.sender_address) if record.sender_address else (False, None)

    recipients: list[dict[str, str]] = [
        {"address": r.address, "name": r.name or "", "rtype": r.rtype}
        for r in record.recipients
    ]

    attachments: list[dict[str, str | int | None]] = [
        {
            "filename": a.filename,
            "content_type": a.content_type,
            "content_disposition": a.content_disposition,
            "size_bytes": a.size_bytes,
            "on_disk_path": a.on_disk_path,
            "content_hash": a.content_hash,
        }
        for a in record.attachments
    ]

    return AdapterRow(
        schema_type="Message",
        rfc822_message_id=record.platform_id,
        sender_address=record.sender_address or None,
        sender_name=record.sender_name,
        sender_domain=sender_domain,
        direction=direction,
        date_sent=record.date_sent or None,
        body_text=body or None,
        body_text_source="imessage-html",
        body_text_hash=body_hash,
        is_multipart=int(record.is_multipart),
        has_attachments=int(record.has_attachments),
        attachment_count=record.attachment_count,
        is_bulk=int(bulk_flag),
        bulk_signal=bulk_sig,
        source_byte_offset=record.provenance.source_byte_offset,
        source_byte_length=record.provenance.source_byte_length,
        raw_hash=record.provenance.raw_hash,
        recipients=recipients,
        attachments=attachments,
        thread_key=record.thread_key,
    )


class IMessageAdapter(Adapter):
    """Ingest imessage-exporter HTML directories."""

    name = "imessage"
    source_kind = "imessage"
    file_kind = "html"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def __init__(
        self,
        *,
        max_seconds: float | None = None,
    ) -> None:
        self.max_seconds = max_seconds
        self._name_to_phone: dict[str, str] = {}

    def detect_bulk(self, row: AdapterRow) -> tuple[bool, str | None]:
        return is_bulk_sender(row.sender_address) if row.sender_address else (False, None)

    def compute_raw_hash(self, row: AdapterRow) -> str:
        seed = (
            f"{row.thread_key or ''}|{row.source_byte_offset or 0}"
            f"|{row.date_sent or ''}|{row.sender_address or ''}"
            f"|{(row.body_text or '')[:100]}"
        )
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        """Not used directly — run() drives per-file iteration."""
        yield from ()

    def _get_done_files(self, conn: sqlite3.Connection, source_file_id: int) -> set[str]:
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        if not row or not row[0]:
            return set()
        try:
            return set(json.loads(row[0]))
        except (json.JSONDecodeError, TypeError):
            return set()

    def _mark_file_done(
        self, conn: sqlite3.Connection, source_file_id: int, filename: str
    ) -> None:
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        done = set(json.loads(row[0])) if row and row[0] else set()
        done.add(filename)
        conn.execute(
            "UPDATE source_files SET notes = ? WHERE id = ?",
            (json.dumps(sorted(done)), source_file_id),
        )

    def _rebuild_name_lookup(
        self, conn: sqlite3.Connection, source_file_id: int
    ) -> None:
        """Rebuild name->phone lookup from previously ingested rows (for resume)."""
        for r in conn.execute(
            """SELECT sender_name, sender_address FROM chat_messages
               WHERE sender_address IS NOT NULL AND sender_name IS NOT NULL
                 AND sender_address LIKE '+%' AND source_file_id = ?
               GROUP BY sender_name""",
            (source_file_id,),
        ):
            self._name_to_phone[r[0]] = r[1]

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestReport:
        report = IngestReport(
            adapter_name=self.name,
            source_path=str(source_path),
            source_file_id=0,
        )

        source_file_id = self._register_source(conn, source_path)
        report.source_file_id = source_file_id
        log.info("[%s] Source registered: id=%d path=%s", self.name, source_file_id, source_path)

        owner_phones = settings.identity.owner_phones
        owner_names = settings.identity.owner_names
        owner_phone = next(iter(owner_phones), None)

        one_on_one, groups = discover_html_files(source_path)
        ordered = one_on_one + groups

        done_files = self._get_done_files(conn, source_file_id)
        todo = [f for f in ordered if f.name not in done_files]
        log.info(
            "[%s] Files: %d total (%d 1-on-1, %d group), %d done, %d remaining",
            self.name, len(one_on_one) + len(groups), len(one_on_one), len(groups),
            len(done_files), len(todo),
        )

        self._name_to_phone = {}
        self._rebuild_name_lookup(conn, source_file_id)
        if self._name_to_phone:
            log.info("[%s] Resumed contact lookup with %d entries", self.name, len(self._name_to_phone))

        has_identity = settings.identity.is_configured
        t_start = time.time()
        files_done = 0
        touched_threads: set[int] = set()
        thread_dates: dict[int, tuple[str, str]] = {}

        for html_file in todo:
            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                log.info("[%s] Time budget reached after %d files", self.name, files_done)
                break

            try:
                for record in parse_file(
                    html_file,
                    owner_phone=owner_phone,
                    name_to_phone=self._name_to_phone,
                    owner_names=owner_names,
                ):
                    report.rows_yielded += 1

                    # Derive direction: format parser sets sender_address
                    # to owner_phone on sent messages
                    if (
                        record.sender_address
                        and owner_phone
                        and normalize_addr(record.sender_address) == normalize_addr(owner_phone)
                    ):
                        direction = "outbound"
                    elif record.sender_address:
                        direction = "inbound"
                    else:
                        direction = "unknown"

                    row = _record_to_row(record, direction)

                    if row.body_text and not row.body_text_hash:
                        row.body_text_hash = hashlib.sha256(row.body_text.encode("utf-8")).hexdigest()

                    if row.direction == "unknown" and has_identity:
                        row.direction = self.infer_direction(row, settings.identity)

                    message_id = self._insert_row(conn, row, source_file_id)
                    if message_id is None:
                        report.rows_skipped += 1
                        continue

                    report.rows_inserted += 1
                    self._insert_sidecars(conn, message_id, row)

                    if row.thread_key:
                        participants = parse_filename_participants(html_file.name)
                        thread_id, created = self._upsert_thread(conn, row.thread_key, participants)
                        self._link_message_thread(conn, message_id, thread_id)
                        if created:
                            report.threads_created += 1
                        touched_threads.add(thread_id)
                        rd = row.date_sent
                        if rd and thread_id in thread_dates:
                            lo, hi = thread_dates[thread_id]
                            thread_dates[thread_id] = (min(lo, rd), max(hi, rd))
                        elif rd:
                            thread_dates[thread_id] = (rd, rd)

                self._mark_file_done(conn, source_file_id, html_file.name)
                conn.commit()
                files_done += 1

            except Exception:
                log.exception("[%s] Error processing %s", self.name, html_file.name)
                report.errors.append(html_file.name)

        for tid in touched_threads:
            dates = thread_dates.get(tid)
            self._update_thread_aggregates(
                conn, tid,
                dates[0] if dates else None,
                dates[1] if dates else None,
            )

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d files, %d yielded, %d inserted, %d skipped, %d threads",
            self.name, files_done, report.rows_yielded, report.rows_inserted,
            report.rows_skipped, report.threads_created,
        )
        return report
