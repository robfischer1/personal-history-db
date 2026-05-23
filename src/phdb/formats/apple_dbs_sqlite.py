"""Apple iPhone backup SQLite format parser — yields typed records.

Source: a directory produced by ``decrypt_iphone_backup.py`` containing
per-target subdirs (addressbook/, callhistory/, voicemail/, etc.).

Handlers:
  - callhistory  — ZCALLRECORD → CallRecord
  - voicemail    — voicemail table → CallRecord (call_type='voicemail')
  - safari_history — history_visits → WebActivity (activity_type='visit')
  - safari_bookmarks — bookmarks table → WebActivity (activity_type='bookmark')
  - notes — ZICCLOUDSYNCINGOBJECT or Note table → DigitalDocument

Pure parser: no DB (destination), no identity.
"""

from __future__ import annotations

import contextlib
import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import datetime, timedelta
from pathlib import Path
from typing import Union

from phdb.records import CallRecord, ChatMessage, DigitalDocument, Provenance, WebActivity

APPLE_EPOCH = datetime(2001, 1, 1)

# Union of all record types this parser yields.
AppleDbsRecord = Union[CallRecord, WebActivity, DigitalDocument, ChatMessage]

# Handler name → parse function mapping (populated at module level below).
HANDLER_NAMES = (
    "callhistory", "voicemail", "safari_history", "safari_bookmarks", "notes", "imessage"
)


def apple_ts_to_iso(seconds_since_2001: float | None) -> str | None:
    """Convert Apple NSDate timestamp (seconds since 2001-01-01) to ISO 8601."""
    if seconds_since_2001 is None:
        return None
    try:
        return (APPLE_EPOCH + timedelta(seconds=float(seconds_since_2001))).isoformat()
    except (ValueError, OverflowError):
        return None


def normalize_phone(addr: str) -> str:
    """Normalize a phone number to E.164-ish format."""
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


def _iter_callhistory(src_dir: Path, source_str: str) -> Iterator[CallRecord]:
    """Parse CallHistory.storedata, yielding CallRecord per call."""
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

    for r in rows:
        addr = (r["ZADDRESS"] or "").strip()
        normalized = normalize_phone(addr)
        if not normalized:
            continue

        date_iso = apple_ts_to_iso(r["ZDATE"])
        if not date_iso:
            continue

        duration_s = int(r["ZDURATION"]) if r["ZDURATION"] else 0
        outbound = bool(r["ZORIGINATED"])
        answered = bool(r["ZANSWERED"])

        call_type = "answered" if answered else "missed"
        synth_id = f"callhistory:{r['Z_PK']}"
        raw_hash = hashlib.sha256(synth_id.encode()).hexdigest()

        yield CallRecord(
            provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
            caller_address=normalized,
            direction="outbound" if outbound else "inbound",
            date_start=date_iso,
            call_type=call_type,
            duration_seconds=duration_s,
        )
    src.close()


def _iter_voicemail(src_dir: Path, source_str: str) -> Iterator[CallRecord]:
    """Parse voicemail.db, yielding CallRecord per voicemail."""
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

    for r in rows:
        addr = (r["sender"] or r["callback_num"] or "").strip()
        normalized = normalize_phone(addr)
        if not normalized:
            continue

        date_iso = None
        if r["date"]:
            with contextlib.suppress(ValueError, OSError):
                date_iso = datetime.fromtimestamp(r["date"]).isoformat()

        if not date_iso:
            continue

        duration_s = int(r["duration"]) if r["duration"] else 0
        call_type = "voicemail"
        if r["trashed_date"]:
            call_type = "voicemail-trashed"

        synth_id = f"voicemail:{r['ROWID']}"
        raw_hash = hashlib.sha256(synth_id.encode()).hexdigest()

        yield CallRecord(
            provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
            caller_address=normalized,
            direction="inbound",
            date_start=date_iso,
            call_type=call_type,
            duration_seconds=duration_s,
            voicemail_text=f"Voicemail: {duration_s}s" + (" (trashed)" if r["trashed_date"] else ""),
        )
    src.close()


def _iter_safari_history(src_dir: Path, source_str: str) -> Iterator[WebActivity]:
    """Parse History.db, yielding WebActivity per visit."""
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

    for r in rows:
        date_iso = apple_ts_to_iso(r["visit_time"])
        url = r["url"] or ""
        synth_id = f"safari:{url}:{r['visit_time']}"
        raw_hash = hashlib.sha256(synth_id.encode()).hexdigest()

        yield WebActivity(
            provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
            activity_type="visit",
            date_performed=date_iso or "",
            platform="safari",
            url=url,
            title=r["title"],
        )
    src.close()


def _iter_safari_bookmarks(src_dir: Path, source_str: str) -> Iterator[WebActivity]:
    """Parse Bookmarks.db, yielding WebActivity per bookmark."""
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

    for r in rows:
        url = r["url"] or ""
        synth_id = f"safari-bm:{url}"
        raw_hash = hashlib.sha256(synth_id.encode()).hexdigest()

        yield WebActivity(
            provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
            activity_type="bookmark",
            date_performed="",
            platform="safari",
            url=url,
            title=r["title"],
        )
    src.close()


def _iter_notes(src_dir: Path, source_str: str) -> Iterator[DigitalDocument]:
    """Parse NoteStore.sqlite (or legacy notes.sqlite), yielding DigitalDocument per note."""
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

    for r in rows:
        if is_modern:
            title = r["ZTITLE1"] or "(untitled)"
            snippet = r["ZSNIPPET"] or ""
            created_iso = apple_ts_to_iso(r["ZCREATIONDATE1"])
            modified_iso = apple_ts_to_iso(r["ZMODIFICATIONDATE1"])
            folder = r["folder"]
            pk = r["Z_PK"]
        else:
            title = r["title"] or "(untitled)"
            snippet = r["summary"] or r["body"] or ""
            created_iso = None
            modified_iso = None
            folder = None
            pk = r["ROWID"]

        body = snippet
        if folder:
            body = f"[Folder: {folder}]\n{body}"

        synth_id = f"notes:{pk}"
        raw_hash = hashlib.sha256(synth_id.encode()).hexdigest()

        yield DigitalDocument(
            provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
            title=title,
            body_text=body or None,
            body_text_source="apple-notes-snippet",
            created_date=created_iso or modified_iso,
            modified_date=modified_iso,
            bucket=folder,
        )
    src.close()


def _iter_imessage(src_dir: Path, source_str: str) -> Iterator[ChatMessage]:
    """Parse chat.db, yielding ChatMessage per message."""
    src_db = src_dir / "chat.db"
    if not src_db.exists():
        return

    src = sqlite3.connect(f"file:{src_db}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    try:
        # Standard iMessage query for chat.db
        rows = list(src.execute("""
            SELECT
                m.ROWID,
                m.text,
                m.date,
                h.id AS sender_address,
                m.is_from_me,
                c.chat_identifier
            FROM message m
            LEFT JOIN handle h ON m.handle_id = h.ROWID
            LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
            LEFT JOIN chat c ON cmj.chat_id = c.ROWID
            WHERE m.text IS NOT NULL OR m.cache_has_attachments = 1
        """))
    except sqlite3.OperationalError:
        src.close()
        return

    for r in rows:
        raw_date = r["date"]
        # iMessage date is nanoseconds since 2001 in recent versions
        if raw_date and raw_date > 1_000_000_000_000:
            date_iso = apple_ts_to_iso(raw_date / 1_000_000_000)
        else:
            date_iso = apple_ts_to_iso(raw_date)

        if not date_iso:
            continue

        sender_addr = r["sender_address"]
        if r["is_from_me"]:
            sender_addr = "self"
        elif sender_addr:
            sender_addr = normalize_phone(sender_addr)

        body = r["text"] or ""
        chat_id = r["chat_identifier"] or sender_addr or "unknown"

        synth_id = f"imessage:{r['ROWID']}"
        raw_hash = hashlib.sha256(synth_id.encode()).hexdigest()

        yield ChatMessage(
            provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
            sender_address=sender_addr or "unknown",
            date_sent=date_iso,
            body_text=body or None,
            thread_key=f"imessage:{chat_id}",
            platform_id=synth_id,
        )
    src.close()


# Dispatch table: handler name → (parse function, source_str is passed).
_HANDLER_FUNCS: dict[str, object] = {
    "callhistory": _iter_callhistory,
    "voicemail": _iter_voicemail,
    "safari_history": _iter_safari_history,
    "safari_bookmarks": _iter_safari_bookmarks,
    "notes": _iter_notes,
    "imessage": _iter_imessage,
}


def parse(source_path: Path, handler: str) -> Iterator[AppleDbsRecord]:
    """Parse a single handler's SQLite database from an Apple backup directory.

    Parameters
    ----------
    source_path:
        Directory containing the handler's SQLite file (e.g., the
        ``callhistory/`` subdir of a decrypted backup).
    handler:
        One of ``HANDLER_NAMES``.

    Yields typed records (CallRecord | WebActivity | DigitalDocument).
    """
    func = _HANDLER_FUNCS.get(handler)
    if func is None:
        raise ValueError(f"Unknown handler: {handler!r}; expected one of {HANDLER_NAMES}")
    source_str = str(source_path)
    yield from func(source_path, source_str)  # type: ignore[operator]


def parse_all(source_path: Path) -> Iterator[tuple[str, AppleDbsRecord]]:
    """Parse all handlers from a decrypted Apple backup directory.

    Yields (handler_name, record) tuples. Each handler's subdir is
    tried; missing subdirs fall through to the parent directory.
    """
    source_str = str(source_path)
    for handler_name in HANDLER_NAMES:
        func = _HANDLER_FUNCS[handler_name]
        handler_dir = source_path / handler_name
        if not handler_dir.exists():
            handler_dir = source_path
        for rec in func(handler_dir, source_str):  # type: ignore[operator]
            yield handler_name, rec
