"""Phone SMS adapter — ingests Android mmssms.db (TitaniumBackup or standalone).

Source: a single mmssms.db file. For TitaniumBackup tarballs, extract the DB
before passing it to this adapter.
Reads sms table for SMS and pdu/addr/part tables for MMS.
Per-address threads.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import sqlite3
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.phone_sms")

_MAX_BODY_LEN = 50_000
_TYPE_INBOUND = 1
_TYPE_OUTBOUND = 2
_MMS_ADDR_FROM = 137
_MMS_ADDR_TO = 151
_MMS_ADDR_CC = 130
_MMS_ADDR_BCC = 129


def _normalize_phone(addr: str | None) -> str | None:
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
    if not date_ms:
        return None
    try:
        return datetime.fromtimestamp(date_ms / 1000.0).isoformat()
    except (ValueError, OSError):
        return None


def _iter_sms(src: sqlite3.Connection, source_label: str) -> Iterator[AdapterRow]:
    rows = src.execute("SELECT _id, thread_id, address, date, body, type FROM sms").fetchall()
    for sms_id, _thread_id, address, date_ms, body, sms_type in rows:
        if not body:
            continue
        addr = _normalize_phone(address)
        if not addr:
            continue

        date_iso = _epoch_ms_to_iso(date_ms)

        if sms_type == _TYPE_OUTBOUND:
            direction = "outbound"
            sender_addr = "self"
        elif sms_type == _TYPE_INBOUND:
            direction = "inbound"
            sender_addr = addr
        else:
            direction = "unknown"
            sender_addr = addr

        if len(body) > _MAX_BODY_LEN:
            body = body[:_MAX_BODY_LEN]

        dedup_seed = f"phone-sms|{source_label}|{sms_id}|{addr}|{date_ms}|{body[:100]}"
        raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

        recipients = [{"address": addr, "rtype": "to"}] if direction == "outbound" else []

        yield AdapterRow(
            schema_type="Message",
            rfc822_message_id=f"phone-sms:{raw_hash}",
            sender_address=sender_addr,
            direction=direction,
            date_sent=date_iso,
            body_text=body,
            body_text_source="phone-sms",
            source_byte_offset=sms_id,
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
            thread_key=f"phone-sms:{addr}",
            recipients=recipients,
        )


def _iter_mms(src: sqlite3.Connection, source_label: str) -> Iterator[AdapterRow]:
    try:
        pdus = src.execute(
            "SELECT _id, m_id, msg_box, date, sub FROM pdu"
        ).fetchall()
    except sqlite3.OperationalError:
        return

    for pid, m_id, msg_box, date_s, sub in pdus:
        date_iso = None
        if date_s:
            with contextlib.suppress(ValueError, OSError, OverflowError):
                date_iso = datetime.fromtimestamp(int(date_s)).isoformat()

        addrs = src.execute("SELECT address, type FROM addr WHERE msg_id = ?", (pid,)).fetchall()
        from_addr = next((_normalize_phone(a[0]) for a in addrs if a[1] == _MMS_ADDR_FROM), None)
        to_norms = [
            _normalize_phone(a[0])
            for a in addrs
            if a[1] in (_MMS_ADDR_TO, _MMS_ADDR_CC, _MMS_ADDR_BCC) and a[0]
        ]
        to_norms = [a for a in to_norms if a]

        if msg_box == 2:
            direction = "outbound"
            sender_addr = "self"
            primary_other = to_norms[0] if to_norms else (from_addr or "")
        elif msg_box == 1:
            direction = "inbound"
            sender_addr = from_addr or "unknown"
            primary_other = from_addr or ""
        else:
            direction = "unknown"
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

        recipients: list[dict[str, str]] = [{"address": a, "rtype": "to"} for a in to_norms if a]
        attachments: list[dict[str, str | int | None]] = [
            {"filename": name or "", "content_type": ct, "content_hash": hashlib.sha256(f"{pid}|{ct}|{name or ''}".encode()).hexdigest()}
            for ct, name in att_list
        ]

        yield AdapterRow(
            schema_type="Message",
            rfc822_message_id=f"phone-mms:{raw_hash}",
            subject=sub or None,
            sender_address=sender_addr,
            direction=direction,
            date_sent=date_iso,
            body_text=body,
            body_text_source="phone-mms",
            is_multipart=1 if att_list else 0,
            has_attachments=1 if att_list else 0,
            attachment_count=len(att_list),
            source_byte_offset=pid,
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
            thread_key=f"phone-sms:{primary_other}",
            recipients=recipients,
            attachments=attachments,
        )


class PhoneSmsAdapter(Adapter):
    """Ingest Android SMS/MMS from mmssms.db."""

    name = "phone_sms"
    source_kind = "phone-sms"
    file_kind = "sqlite"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        source_label = source_path.name
        try:
            yield from _iter_sms(src, source_label)
            yield from _iter_mms(src, source_label)
        finally:
            src.close()
