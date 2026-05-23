"""Apple iPhone backup adapter — ingests decrypted backup SQLite databases.

Consumes typed records from phdb.formats.apple_dbs_sqlite.

Source: a directory produced by ``decrypt_iphone_backup.py`` containing
per-target subdirs (addressbook/, callhistory/, voicemail/, etc.).

Handlers:
  - callhistory  — ZCALLRECORD → schema_type='Action'
  - voicemail    — voicemail table → schema_type='Message'
  - safari_history — history_visits → schema_type='WebPage'
  - safari_bookmarks — bookmarks table → schema_type='WebPage'
  - notes — ZICCLOUDSYNCINGOBJECT or Note table → schema_type='DigitalDocument'

Per-handler resume: completed handler names tracked in source_files.notes JSON.
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
from phdb.adapters.raindrop import upsert_bookmark, upsert_web_page
from phdb.formats.apple_dbs_sqlite import (
    HANDLER_NAMES,
    AppleDbsRecord,
    CallRecord,
    DigitalDocument,
    WebActivity,
    apple_ts_to_iso as _apple_ts_to_iso,
    normalize_phone as _normalize_phone,
    parse,
)
from phdb.formats.url import normalize_url
from phdb.log import get_logger
from phdb.records import BookmarkEvent

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.apple_dbs")


def _record_to_adapter_row(rec: AppleDbsRecord) -> AdapterRow:
    """Map a typed record from the format parser to an AdapterRow for DB insert."""
    prov = rec.provenance

    if isinstance(rec, CallRecord):
        body = f"Call: {rec.duration_seconds or 0}s, {rec.call_type}"
        return AdapterRow(
            schema_type="Action",
            rfc822_message_id=f"callhistory:{prov.raw_hash[:12]}"
            if not prov.source_byte_offset
            else f"callhistory:{prov.source_byte_offset}",
            sender_address=rec.caller_address,
            direction=rec.direction,
            date_sent=rec.date_start,
            body_text=rec.voicemail_text or body,
            body_text_source="voicemail" if rec.call_type.startswith("voicemail") else "callhistory",
            raw_hash=prov.raw_hash,
            body_text_hash=hashlib.sha256((rec.voicemail_text or body).encode()).hexdigest(),
            thread_key=(
                f"voicemail:{rec.caller_address}"
                if rec.call_type.startswith("voicemail")
                else f"calls:{rec.caller_address}"
            ),
        )

    if isinstance(rec, DigitalDocument):
        body = rec.body_text or ""
        return AdapterRow(
            schema_type="DigitalDocument",
            rfc822_message_id=f"notes:{prov.raw_hash[:12]}",
            subject=rec.title,
            sender_name="Me",
            direction="self",
            date_sent=rec.created_date,
            body_text=rec.body_text,
            body_text_source="apple-notes-snippet",
            raw_hash=prov.raw_hash,
            body_text_hash=hashlib.sha256(body.encode()).hexdigest() if body else None,
        )

    msg = f"Unexpected record type: {type(rec)}"
    raise TypeError(msg)


class AppleDbsAdapter(Adapter):
    """Ingest decrypted Apple iPhone backup SQLite databases."""

    name = "apple_dbs"
    source_kind = "iphone-backup"
    file_kind = "sqlite"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def __init__(
        self,
        *,
        max_seconds: float | None = None,
        only: list[str] | None = None,
    ) -> None:
        self.max_seconds = max_seconds
        self.only = only

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        yield from ()

    def _get_done_handlers(self, conn: sqlite3.Connection, source_file_id: int) -> set[str]:
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        if not row or not row[0]:
            return set()
        try:
            return set(json.loads(row[0]).get("handlers_done", []))
        except (json.JSONDecodeError, TypeError):
            return set()

    def _mark_handler_done(
        self, conn: sqlite3.Connection, source_file_id: int, handler_name: str
    ) -> None:
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        notes: dict[str, object] = {}
        if row and row[0]:
            try:
                notes = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                notes = {}
        raw_done = notes.get("handlers_done", [])
        done: set[str] = set(raw_done) if isinstance(raw_done, list) else set()
        done.add(handler_name)
        notes["handlers_done"] = sorted(done)
        conn.execute(
            "UPDATE source_files SET notes = ? WHERE id = ?",
            (json.dumps(notes), source_file_id),
        )

    def _handle_web_activity(
        self,
        conn: sqlite3.Connection,
        rec: WebActivity,
        source_file_id: int,
        report: IngestReport,
    ) -> None:
        """Route WebActivity to web_pages entity + bookmarks action."""
        url = rec.url or ""
        if not url:
            report.rows_skipped += 1
            return
        norm = normalize_url(url)
        sighted = rec.date_performed or None
        wp_id = upsert_web_page(
            conn, url, norm,
            title=rec.title, sighted=sighted,
            source_file_id=source_file_id,
        )
        if rec.activity_type == "bookmark":
            event = BookmarkEvent(
                provenance=rec.provenance,
                url=url,
                normalized_url=norm,
                title=rec.title,
                instrument="safari",
                date_added=sighted or "",
                tags=(),
            )
            upsert_bookmark(conn, source_file_id, event, web_page_id=wp_id)
        report.rows_inserted += 1

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

        handlers_to_run = self.only or list(HANDLER_NAMES)
        done_handlers = self._get_done_handlers(conn, source_file_id)
        todo = [h for h in handlers_to_run if h not in done_handlers and h in HANDLER_NAMES]

        log.info(
            "[%s] Handlers: %d total, %d done, %d remaining",
            self.name, len(handlers_to_run), len(done_handlers), len(todo),
        )

        t_start = time.time()
        touched_threads: set[int] = set()
        thread_dates: dict[int, tuple[str, str]] = {}

        for handler_name in todo:
            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                log.info("[%s] Time budget reached", self.name)
                break

            handler_dir = source_path / handler_name
            if not handler_dir.exists():
                handler_dir = source_path

            try:
                for rec in parse(handler_dir, handler_name):
                    report.rows_yielded += 1

                    if isinstance(rec, WebActivity):
                        self._handle_web_activity(
                            conn, rec, source_file_id, report,
                        )
                        continue

                    row = _record_to_adapter_row(rec)

                    if row.body_text and not row.body_text_hash:
                        row.body_text_hash = hashlib.sha256(
                            row.body_text.encode("utf-8")
                        ).hexdigest()

                    message_id = self._insert_row(conn, row, source_file_id)
                    if message_id is None:
                        report.rows_skipped += 1
                        continue

                    report.rows_inserted += 1
                    self._insert_sidecars(conn, message_id, row)

                    if row.thread_key:
                        thread_id, created = self._upsert_thread(conn, row.thread_key)
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

                self._mark_handler_done(conn, source_file_id, handler_name)
                conn.commit()
                log.info(
                    "[%s:%s] Done: %d yielded so far",
                    self.name, handler_name, report.rows_yielded,
                )

            except Exception:
                log.exception("[%s] Error in handler %s", self.name, handler_name)
                report.errors.append(handler_name)

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
            "[%s] Done: %d yielded, %d inserted, %d skipped, %d threads",
            self.name, report.rows_yielded, report.rows_inserted,
            report.rows_skipped, report.threads_created,
        )
        return report
