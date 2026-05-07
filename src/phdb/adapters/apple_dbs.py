"""Apple iPhone backup adapter — ingests decrypted backup SQLite databases.

Source: a directory produced by ``decrypt_iphone_backup.py`` containing
per-target subdirs (addressbook/, callhistory/, voicemail/, etc.).

Handlers (each reads its own source SQLite and yields AdapterRows):
  - callhistory  — ZCALLRECORD → schema_type='Action'
  - voicemail    — voicemail table → schema_type='Message'
  - safari_history — history_visits → schema_type='WebPage'
  - safari_bookmarks — bookmarks table → schema_type='WebPage'
  - notes — ZICCLOUDSYNCINGOBJECT or Note table → schema_type='DigitalDocument'

Per-handler resume: completed handler names tracked in source_files.notes JSON.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.apple_dbs")

APPLE_EPOCH = datetime(2001, 1, 1)


def _apple_ts_to_iso(seconds_since_2001: float | None) -> str | None:
    if seconds_since_2001 is None:
        return None
    try:
        return (APPLE_EPOCH + timedelta(seconds=float(seconds_since_2001))).isoformat()
    except (ValueError, OverflowError):
        return None


def _normalize_phone(addr: str) -> str:
    if not addr:
        return ""
    normalized = "".join(c for c in addr if c.isdigit() or c == "+")
    if not normalized:
        return ""
    if not normalized.startswith("+"):
        if len(normalized) == 10:
            normalized = "+1" + normalized
        elif len(normalized) == 11 and normalized.startswith("1"):
            normalized = "+" + normalized
    return normalized


def _iter_callhistory(src_dir: Path) -> Iterator[AdapterRow]:
    src_db = src_dir / "CallHistory.storedata"
    if not src_db.exists():
        return

    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        rows = list(src.execute(
            "SELECT Z_PK, ZADDRESS, ZDATE, ZDURATION, ZORIGINATED, ZANSWERED FROM ZCALLRECORD"
        ))
    except sqlite3.OperationalError:
        src.close()
        return

    log.info("[apple_dbs:callhistory] %d call records", len(rows))

    for r in rows:
        addr = (r["ZADDRESS"] or "").strip()
        normalized = _normalize_phone(addr)
        if not normalized:
            continue

        date_iso = _apple_ts_to_iso(r["ZDATE"])
        duration_s = int(r["ZDURATION"]) if r["ZDURATION"] else 0
        outbound = bool(r["ZORIGINATED"])
        answered = bool(r["ZANSWERED"])

        body = f"Call: {duration_s}s, {'answered' if answered else 'missed'}"
        synth_id = f"callhistory:{r['Z_PK']}"
        raw_hash = hashlib.sha256(synth_id.encode()).hexdigest()

        yield AdapterRow(
            schema_type="Action",
            rfc822_message_id=synth_id,
            sender_address=normalized,
            direction="outbound" if outbound else "inbound",
            date_sent=date_iso,
            body_text=body,
            body_text_source="callhistory",
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
            thread_key=f"calls:{normalized}",
        )
    src.close()


def _iter_voicemail(src_dir: Path) -> Iterator[AdapterRow]:
    src_db = src_dir / "voicemail.db"
    if not src_db.exists():
        return

    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        rows = list(src.execute(
            "SELECT ROWID, sender, date, duration, callback_num, trashed_date FROM voicemail"
        ))
    except sqlite3.OperationalError:
        src.close()
        return

    log.info("[apple_dbs:voicemail] %d voicemails", len(rows))

    for r in rows:
        addr = (r["sender"] or r["callback_num"] or "").strip()
        normalized = _normalize_phone(addr)
        if not normalized:
            continue

        date_iso = None
        if r["date"]:
            with contextlib.suppress(ValueError, OSError):
                date_iso = datetime.fromtimestamp(r["date"]).isoformat()

        duration_s = int(r["duration"]) if r["duration"] else 0
        body = f"Voicemail: {duration_s}s"
        if r["trashed_date"]:
            body += " (trashed)"
        synth_id = f"voicemail:{r['ROWID']}"
        raw_hash = hashlib.sha256(synth_id.encode()).hexdigest()

        yield AdapterRow(
            schema_type="Message",
            rfc822_message_id=synth_id,
            sender_address=normalized,
            direction="inbound",
            date_sent=date_iso,
            body_text=body,
            body_text_source="voicemail",
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
            thread_key=f"voicemail:{normalized}",
        )
    src.close()


def _iter_safari_history(src_dir: Path) -> Iterator[AdapterRow]:
    src_db = src_dir / "History.db"
    if not src_db.exists():
        return

    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    rows: list[sqlite3.Row] = []
    try:
        rows = list(src.execute(
            "SELECT i.url, v.title, v.visit_time "
            "FROM history_items i JOIN history_visits v ON v.history_item = i.id"
        ))
    except sqlite3.OperationalError:
        try:
            rows = list(src.execute(
                "SELECT i.url, i.title, v.visit_time "
                "FROM history_items i JOIN history_visits v ON v.history_item = i.id"
            ))
        except sqlite3.OperationalError:
            src.close()
            return

    log.info("[apple_dbs:safari_history] %d visits", len(rows))

    for r in rows:
        date_iso = _apple_ts_to_iso(r["visit_time"])
        url = r["url"] or ""
        synth_id = f"safari:{url}:{r['visit_time']}"
        body = f"Visited: {url}"
        raw_hash = hashlib.sha256(synth_id.encode()).hexdigest()

        yield AdapterRow(
            schema_type="WebPage",
            rfc822_message_id=synth_id,
            subject=r["title"],
            direction="self",
            date_sent=date_iso,
            body_text=body,
            body_text_source="safari-visit",
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
        )
    src.close()


def _iter_safari_bookmarks(src_dir: Path) -> Iterator[AdapterRow]:
    src_db = src_dir / "Bookmarks.db"
    if not src_db.exists():
        return

    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        rows = list(src.execute("SELECT title, url FROM bookmarks WHERE url IS NOT NULL"))
    except sqlite3.OperationalError:
        src.close()
        return

    log.info("[apple_dbs:safari_bookmarks] %d bookmarks", len(rows))

    for r in rows:
        url = r["url"] or ""
        synth_id = f"safari-bm:{url}"
        body = f"Bookmark: {url}"
        raw_hash = hashlib.sha256(synth_id.encode()).hexdigest()

        yield AdapterRow(
            schema_type="WebPage",
            rfc822_message_id=synth_id,
            subject=r["title"],
            direction="self",
            body_text=body,
            body_text_source="safari-bookmark",
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
        )
    src.close()


def _iter_notes(src_dir: Path) -> Iterator[AdapterRow]:
    src_db = src_dir / "NoteStore.sqlite"
    if not src_db.exists():
        legacy = src_dir / "notes.sqlite"
        if legacy.exists():
            src_db = legacy
        else:
            return

    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row

    is_modern = True
    try:
        rows = list(src.execute("""
            SELECT n.Z_PK, n.ZTITLE1, n.ZSNIPPET,
                   n.ZCREATIONDATE1, n.ZMODIFICATIONDATE1,
                   f.ZTITLE2 AS folder
              FROM ZICCLOUDSYNCINGOBJECT n
              LEFT JOIN ZICCLOUDSYNCINGOBJECT f ON f.Z_PK = n.ZFOLDER
             WHERE n.ZTITLE1 IS NOT NULL
        """))
    except sqlite3.OperationalError:
        is_modern = False
        try:
            rows = list(src.execute("SELECT ROWID, title, summary, body FROM Note"))
        except sqlite3.OperationalError:
            src.close()
            return

    log.info("[apple_dbs:notes] %d notes (%s schema)", len(rows), "modern" if is_modern else "legacy")

    for r in rows:
        if is_modern:
            title = r["ZTITLE1"] or "(untitled)"
            snippet = r["ZSNIPPET"] or ""
            ts_iso = _apple_ts_to_iso(r["ZCREATIONDATE1"]) or _apple_ts_to_iso(r["ZMODIFICATIONDATE1"])
            folder = r["folder"]
            pk = r["Z_PK"]
        else:
            title = r["title"] or "(untitled)"
            snippet = r["summary"] or r["body"] or ""
            ts_iso = None
            folder = None
            pk = r["ROWID"]

        body = snippet
        if folder:
            body = f"[Folder: {folder}]\n{body}"
        synth_id = f"notes:{pk}"
        raw_hash = hashlib.sha256(synth_id.encode()).hexdigest()

        yield AdapterRow(
            schema_type="DigitalDocument",
            rfc822_message_id=synth_id,
            subject=title,
            sender_name="Me",
            direction="self",
            date_sent=ts_iso,
            body_text=body or None,
            body_text_source="apple-notes-snippet",
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body.encode()).hexdigest() if body else None,
        )
    src.close()


HANDLERS: dict[str, tuple[str, type[Iterator[AdapterRow]]]] = {}

_HANDLER_FUNCS = {
    "callhistory": _iter_callhistory,
    "voicemail": _iter_voicemail,
    "safari_history": _iter_safari_history,
    "safari_bookmarks": _iter_safari_bookmarks,
    "notes": _iter_notes,
}


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

        handlers_to_run = self.only or list(_HANDLER_FUNCS.keys())
        done_handlers = self._get_done_handlers(conn, source_file_id)
        todo = [h for h in handlers_to_run if h not in done_handlers and h in _HANDLER_FUNCS]

        log.info(
            "[%s] Handlers: %d total, %d done, %d remaining",
            self.name, len(handlers_to_run), len(done_handlers), len(todo),
        )

        t_start = time.time()
        touched_threads: set[int] = set()

        for handler_name in todo:
            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                log.info("[%s] Time budget reached", self.name)
                break

            handler_func = _HANDLER_FUNCS[handler_name]
            handler_dir = source_path / handler_name
            if not handler_dir.exists():
                handler_dir = source_path

            try:
                for row in handler_func(handler_dir):
                    report.rows_yielded += 1

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
            self._update_thread_aggregates(conn, tid)

        actual = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (actual, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped, %d threads",
            self.name, report.rows_yielded, report.rows_inserted,
            report.rows_skipped, report.threads_created,
        )
        return report
