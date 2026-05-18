"""Phone SMS/MMS SQLite format parser — yields ChatMessage records.

Parses Android mmssms.db (TitaniumBackup or standalone).
Reads sms table for SMS and pdu/addr/part tables for MMS.
Pure parser: no DB (destination), no identity.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from phdb.records import Attachment, ChatMessage, Provenance, Recipient

_MAX_BODY_LEN = 50_000
_MMS_ADDR_FROM = 137
_MMS_ADDR_TO = 151
_MMS_ADDR_CC = 130
_MMS_ADDR_BCC = 129


def _normalize_phone(addr: str | None) -> str | None:
    """Normalize a phone number to E.164-ish format."""
    if not addr:
        return None
    a = re.sub(r"[\s\-().]", "", addr.strip())
    if a.startswith("+"):
        return a
    if re.fullmatch(r"\d{10}", a):
        return "+1" + a
    if re.fullmatch(r"1\d{10}", a):
        return "+" + a
    return a


def _epoch_ms_to_iso(date_ms: int | None) -> str | None:
    """Convert epoch milliseconds to ISO 8601 string."""
    if not date_ms:
        return None
    try:
        return datetime.fromtimestamp(date_ms / 1000.0).isoformat()
    except (ValueError, OSError):
        return None


def _iter_sms(src: sqlite3.Connection, source_label: str, source_str: str) -> Iterator[ChatMessage]:
    """Yield ChatMessage records from the sms table."""
    rows = src.execute("SELECT _id, thread_id, address, date, body, type FROM sms").fetchall()
    for sms_id, _thread_id, address, date_ms, body, sms_type in rows:
        if not body:
            continue
        addr = _normalize_phone(address)
        if not addr:
            continue

        date_iso = _epoch_ms_to_iso(date_ms)
        if not date_iso:
            continue

        sender_addr = "self" if sms_type == 2 else addr  # outbound

        if len(body) > _MAX_BODY_LEN:
            body = body[:_MAX_BODY_LEN]

        dedup_seed = f"phone-sms|{source_label}|{sms_id}|{addr}|{date_ms}|{body[:100]}"
        raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

        recipients: tuple[Recipient, ...] = ()
        if sms_type == 2:  # outbound
            recipients = (Recipient(address=addr, rtype="to"),)

        yield ChatMessage(
            provenance=Provenance(
                source_path=source_str,
                raw_hash=raw_hash,
                source_byte_offset=sms_id,
            ),
            sender_address=sender_addr,
            date_sent=date_iso,
            body_text=body,
            thread_key=f"phone-sms:{addr}",
            recipients=recipients,
        )


def _iter_mms(src: sqlite3.Connection, source_label: str, source_str: str) -> Iterator[ChatMessage]:
    """Yield ChatMessage records from the pdu/addr/part tables."""
    with contextlib.suppress(sqlite3.OperationalError):
        pdus = src.execute(
            "SELECT _id, m_id, msg_box, date, sub FROM pdu"
        ).fetchall()

        for pid, m_id, msg_box, date_s, _sub in pdus:
            date_iso = None
            if date_s:
                with contextlib.suppress(ValueError, OSError, OverflowError):
                    date_iso = datetime.fromtimestamp(int(date_s)).isoformat()

            if not date_iso:
                continue

            addrs = src.execute("SELECT address, type FROM addr WHERE msg_id = ?", (pid,)).fetchall()
            from_addr = next((_normalize_phone(a[0]) for a in addrs if a[1] == _MMS_ADDR_FROM), None)
            to_norms = [
                _normalize_phone(a[0])
                for a in addrs
                if a[1] in (_MMS_ADDR_TO, _MMS_ADDR_CC, _MMS_ADDR_BCC) and a[0]
            ]
            to_norms = [a for a in to_norms if a]

            if msg_box == 2:
                sender_addr = "self"
                primary_other = to_norms[0] if to_norms else (from_addr or "")
            elif msg_box == 1:
                sender_addr = from_addr or "unknown"
                primary_other = from_addr or ""
            else:
                sender_addr = from_addr or "unknown"
                primary_other = from_addr or (to_norms[0] if to_norms else "")

            if not primary_other:
                continue

            try:
                parts = src.execute(
                    "SELECT ct, name, text FROM part WHERE mid = ?", (pid,)
                ).fetchall()
            except sqlite3.OperationalError:
                parts = []

            text_chunks: list[str] = []
            att_list: list[tuple[str, str | None]] = []
            for ct, name, text in parts:
                ct_str = (ct or "").strip()
                if ct_str == "text/plain":
                    t = (text or "").strip()
                    if t:
                        text_chunks.append(t)
                elif ct_str == "application/smil":
                    continue
                elif ct_str:
                    att_list.append((ct_str, name))

            if text_chunks:
                body = "\n".join(text_chunks)
                if att_list:
                    body += f"\n\n[+{len(att_list)} attachment(s): " + ", ".join(ct for ct, _ in att_list) + "]"
            elif att_list:
                body = f"(MMS with {len(att_list)} attachment(s): " + ", ".join(ct for ct, _ in att_list) + ")"
            else:
                body = "(empty MMS)"

            if len(body) > _MAX_BODY_LEN:
                body = body[:_MAX_BODY_LEN]

            seed = f"phone-mms|{source_label}|{m_id or ''}|{pid}|{date_s}"
            raw_hash = hashlib.sha256(seed.encode()).hexdigest()

            recipients = tuple(Recipient(address=a, rtype="to") for a in to_norms if a)

            attachments: tuple[Attachment, ...] = tuple(
                Attachment(
                    provenance=Provenance(
                        source_path=source_str,
                        raw_hash=hashlib.sha256(f"{pid}|{ct}|{name or ''}".encode()).hexdigest(),
                    ),
                    parent_id=raw_hash,
                    filename=name or "",
                    content_type=ct,
                    content_hash=hashlib.sha256(f"{pid}|{ct}|{name or ''}".encode()).hexdigest(),
                )
                for ct, name in att_list
            )

            yield ChatMessage(
                provenance=Provenance(
                    source_path=source_str,
                    raw_hash=raw_hash,
                    source_byte_offset=pid,
                ),
                sender_address=sender_addr,
                date_sent=date_iso,
                is_multipart=bool(att_list),
                has_attachments=bool(att_list),
                attachment_count=len(att_list),
                body_text=body,
                thread_key=f"phone-sms:{primary_other}",
                recipients=recipients,
                attachments=attachments,
            )


def parse(source_path: Path) -> Iterator[ChatMessage]:
    """Open an mmssms.db read-only and yield ChatMessage records for SMS and MMS."""
    source_str = str(source_path)
    source_label = source_path.name
    src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    try:
        yield from _iter_sms(src, source_label, source_str)
        yield from _iter_mms(src, source_label, source_str)
    finally:
        src.close()
