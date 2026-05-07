"""Mbox adapter — ingests Gmail .mbox exports (or any RFC 2822 mbox).

Streams mbox by 'From ' line boundary (custom parser, not stdlib mailbox.mbox)
for performance on multi-GB files. Handles resume via byte offsets.
"""

from __future__ import annotations

import contextlib
import email.policy
import hashlib
import json
import re
import sqlite3
import time
from collections.abc import Iterator
from datetime import UTC
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING

import html2text

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.mbox")

_BULK_NOREPLY_PATTERNS = re.compile(
    r"(no-?reply|donotreply|do-not-reply|notification|notifications|alerts?|"
    r"updates?|news|newsletter|marketing|promo|deals?|broadcast|announce|"
    r"automated|mailer|noreply)",
    re.IGNORECASE,
)

_SNIPPET_LEN = 280
_MAX_BODY_LEN = 200_000

_H2T = html2text.HTML2Text()
_H2T.ignore_images = True
_H2T.ignore_emphasis = False
_H2T.ignore_links = False
_H2T.body_width = 0
_H2T.unicode_snob = True


def _normalize_addr(addr: str) -> str:
    return (addr or "").strip().lower()


def _decode_h(value: str | None) -> str | None:
    if value is None:
        return None
    try:
        return str(make_header(decode_header(value)))
    except Exception:
        return str(value)


def _parse_date_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.isoformat()
    except Exception:
        return None


def _first_received_date(msg: email.message.Message) -> str | None:
    received = msg.get_all("Received") or []
    for r in received:
        if ";" in r:
            cand = r.rsplit(";", 1)[1].strip()
            iso = _parse_date_iso(cand)
            if iso:
                return iso
    return None


def _is_bulk_message(
    msg: email.message.Message, sender_addr: str
) -> tuple[bool, str | None]:
    if msg.get("List-Unsubscribe"):
        return True, "List-Unsubscribe"
    if msg.get("List-Id"):
        return True, "List-Id"
    prec = (msg.get("Precedence") or "").strip().lower()
    if prec in ("bulk", "list", "junk"):
        return True, f"Precedence:{prec}"
    auto = (msg.get("Auto-Submitted") or "").strip().lower()
    if auto and auto != "no":
        return True, f"Auto-Submitted:{auto}"
    if msg.get("X-Auto-Response-Suppress"):
        return True, "X-Auto-Response-Suppress"
    if sender_addr:
        local = sender_addr.split("@", 1)[0]
        if _BULK_NOREPLY_PATTERNS.search(local):
            return True, "noreply-pattern"
    return False, None


def _extract_body(
    msg: email.message.Message, is_bulk: bool
) -> tuple[str | None, str | None, str | None]:
    """Returns (body_text, body_html, body_text_source)."""
    plain_parts: list[str] = []
    html_parts: list[str] = []

    def _decode_payload(part: email.message.Message) -> str | None:
        raw = part.get_payload(decode=True)
        if not isinstance(raw, bytes):
            return None
        charset = part.get_content_charset() or "utf-8"
        try:
            return raw.decode(charset, errors="replace")
        except (LookupError, UnicodeDecodeError):
            return raw.decode("utf-8", errors="replace")

    if msg.is_multipart():
        for part in msg.walk():
            cd = (part.get("Content-Disposition") or "").lower()
            if "attachment" in cd:
                continue
            ct = part.get_content_type()
            text = _decode_payload(part)
            if text is None:
                continue
            if ct == "text/plain":
                plain_parts.append(text)
            elif ct == "text/html":
                html_parts.append(text)
    else:
        ct = msg.get_content_type()
        text = _decode_payload(msg)
        if text is not None:
            if ct == "text/plain":
                plain_parts.append(text)
            elif ct == "text/html":
                html_parts.append(text)

    plain = "\n\n".join(plain_parts).strip() if plain_parts else ""
    raw_html = "\n".join(html_parts) if html_parts else None

    if is_bulk:
        if plain:
            return plain[:_SNIPPET_LEN], None, "plain-snippet"
        return None, None, "empty"

    if plain:
        return plain[:_MAX_BODY_LEN], raw_html, "plain"
    if raw_html:
        try:
            converted = _H2T.handle(raw_html).strip()
            return converted[:_MAX_BODY_LEN], raw_html, "html2text"
        except Exception:
            return None, raw_html, "html-conv-failed"
    return None, None, "empty"


def _extract_attachments(
    msg: email.message.Message,
) -> list[dict[str, str | int | None]]:
    out: list[dict[str, str | int | None]] = []
    if not msg.is_multipart():
        return out
    for part in msg.walk():
        cd = part.get("Content-Disposition") or ""
        if "attachment" not in cd.lower() and not part.get_filename():
            continue
        if part.is_multipart():
            continue
        filename = part.get_filename()
        if filename:
            with contextlib.suppress(Exception):
                filename = str(make_header(decode_header(filename)))
        raw_payload = part.get_payload(decode=True)
        size_bytes = len(raw_payload) if isinstance(raw_payload, bytes) else None
        out.append({
            "filename": filename,
            "content_type": part.get_content_type(),
            "content_disposition": cd,
            "size_bytes": size_bytes,
        })
    return out


def _stream_messages(
    mbox_path: Path, skip_to_offset: int = 0
) -> Iterator[tuple[bytes, int, int]]:
    """Yield (raw_bytes, byte_offset, byte_length) per mbox message."""
    buf = bytearray()
    msg_offset = skip_to_offset
    cur_offset = skip_to_offset
    with open(mbox_path, "rb") as f:
        if skip_to_offset > 0:
            f.seek(skip_to_offset)
        for line in f:
            line_len = len(line)
            if line.startswith(b"From ") and buf:
                yield bytes(buf), msg_offset, len(buf)
                buf.clear()
                msg_offset = cur_offset
            buf.extend(line)
            cur_offset += line_len
        if buf:
            yield bytes(buf), msg_offset, len(buf)


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
        # Overridden — the real raw_hash is set per-message from the raw bytes.
        # This fallback exists only for the base class contract; iter_rows always
        # populates raw_hash directly.
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
            "FROM messages WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()[0]
        if self._resume_offset > 0:
            log.info("[%s] Resuming from byte offset %d", self.name, self._resume_offset)
        return super().run(source_path, conn, settings)

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        n_errors = 0
        n_null_msgid = 0
        t_start = time.time()
        for n_processed, (raw_bytes, byte_offset, byte_length) in enumerate(
            _stream_messages(source_path, skip_to_offset=self._resume_offset),
            start=1,
        ):
            if (
                self.max_seconds is not None
                and n_processed % 250 == 0
                and (time.time() - t_start) > self.max_seconds
            ):
                    log.info("[%s] Time budget reached after %d messages", self.name, n_processed)
                    break

            try:
                msg = message_from_bytes(raw_bytes)

                rfc_msgid_raw = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip()
                rfc_msgid: str | None = rfc_msgid_raw.strip("<>") if rfc_msgid_raw else None
                if rfc_msgid is None:
                    n_null_msgid += 1

                in_reply_to = (msg.get("In-Reply-To") or "").strip().strip("<>") or None
                references = msg.get("References") or None

                gmail_thread_id = msg.get("X-GM-THRID") or None
                gmail_labels_raw = msg.get("X-Gmail-Labels") or ""
                gmail_labels: list[str] = (
                    [label.strip() for label in gmail_labels_raw.split(",") if label.strip()]
                    if gmail_labels_raw
                    else []
                )

                subject = _decode_h(msg.get("Subject"))

                from_raw = msg.get("From") or ""
                sender_name_raw, sender_addr = parseaddr(from_raw)
                sender_addr = _normalize_addr(sender_addr)
                sender_name = _decode_h(sender_name_raw) if sender_name_raw else None
                sender_domain = sender_addr.split("@", 1)[1] if "@" in sender_addr else None

                recipients: list[dict[str, str]] = []
                for header, rtype in [("To", "to"), ("Cc", "cc"), ("Bcc", "bcc")]:
                    for nm, ad in getaddresses(msg.get_all(header) or []):
                        a = _normalize_addr(ad)
                        if a:
                            recipients.append({
                                "address": a,
                                "name": _decode_h(nm) or "",
                                "rtype": rtype,
                            })

                date_sent = _parse_date_iso(msg.get("Date"))
                date_received = _first_received_date(msg)

                bulk_flag, bulk_sig = _is_bulk_message(msg, sender_addr)
                body_text, body_html, body_src = _extract_body(msg, bulk_flag)

                attachments = _extract_attachments(msg)
                raw_hash = hashlib.sha256(raw_bytes).hexdigest()

                yield AdapterRow(
                    schema_type="EmailMessage",
                    rfc822_message_id=rfc_msgid,
                    in_reply_to=in_reply_to,
                    references_chain=references,
                    gmail_thread_id=gmail_thread_id,
                    gmail_labels=json.dumps(gmail_labels) if gmail_labels else None,
                    subject=subject,
                    sender_address=sender_addr or None,
                    sender_name=sender_name,
                    sender_domain=sender_domain,
                    direction="unknown",
                    date_sent=date_sent,
                    date_received=date_received,
                    body_text=body_text,
                    body_html=body_html,
                    body_text_source=body_src,
                    is_multipart=int(msg.is_multipart()),
                    has_attachments=int(bool(attachments)),
                    attachment_count=len(attachments),
                    is_bulk=int(bulk_flag),
                    bulk_signal=bulk_sig,
                    source_byte_offset=byte_offset,
                    source_byte_length=byte_length,
                    raw_hash=raw_hash,
                    recipients=recipients,
                    attachments=attachments,
                )

            except Exception:
                n_errors += 1
                if n_errors <= 10:
                    log.exception("[%s] Error parsing message at offset %d", self.name, byte_offset)

        if n_null_msgid > 0:
            log.warning("[%s] %d messages had no Message-ID (undeduped)", self.name, n_null_msgid)
        if n_errors > 0:
            log.warning("[%s] %d messages failed to parse", self.name, n_errors)
