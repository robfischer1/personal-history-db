"""Mbox format parser — yields EmailMessage records from RFC 2822 mbox files.

Pure parser: no DB, no settings, no identity. Yields one EmailMessage per
message in the mbox stream. Handles resume via byte offset.
"""

from __future__ import annotations

import contextlib
import email.message
import hashlib
import re
from collections.abc import Iterator
from datetime import UTC
from email import message_from_bytes
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from pathlib import Path

import html2text

from phdb.records import Attachment, EmailMessage, Provenance, Recipient

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


def is_bulk_message(
    msg: email.message.Message, sender_addr: str
) -> tuple[bool, str | None]:
    """Detect bulk/automated messages from headers and sender patterns."""
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
    msg: email.message.Message, source_path: str, raw_hash: str
) -> tuple[Attachment, ...]:
    out: list[Attachment] = []
    if not msg.is_multipart():
        return ()
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
        content_hash = hashlib.sha256(raw_payload).hexdigest() if isinstance(raw_payload, bytes) else None
        out.append(Attachment(
            provenance=Provenance(source_path=source_path, raw_hash=content_hash or ""),
            parent_id=raw_hash,
            filename=filename,
            content_type=part.get_content_type(),
            content_disposition=cd,
            size_bytes=size_bytes,
            content_hash=content_hash,
        ))
    return tuple(out)


def stream_raw_messages(
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


def parse(
    source_path: Path,
    *,
    skip_to_offset: int = 0,
) -> Iterator[EmailMessage]:
    """Parse an mbox file, yielding EmailMessage records.

    Pure format parser — no DB, no identity, no direction inference.
    """
    source_str = str(source_path)

    for raw_bytes, byte_offset, byte_length in stream_raw_messages(source_path, skip_to_offset):
        try:
            msg = message_from_bytes(raw_bytes)

            rfc_msgid_raw = (msg.get("Message-ID") or msg.get("Message-Id") or "").strip()
            rfc_msgid: str | None = rfc_msgid_raw.strip("<>") if rfc_msgid_raw else None

            in_reply_to = (msg.get("In-Reply-To") or "").strip().strip("<>") or None
            references = msg.get("References") or None

            gmail_thread_id = msg.get("X-GM-THRID") or None
            gmail_labels_raw = msg.get("X-Gmail-Labels") or ""
            gmail_labels: tuple[str, ...] = tuple(
                label.strip() for label in gmail_labels_raw.split(",") if label.strip()
            ) if gmail_labels_raw else ()

            subject = _decode_h(msg.get("Subject"))

            from_raw = msg.get("From") or ""
            sender_name_raw, sender_addr = parseaddr(from_raw)
            sender_addr = _normalize_addr(sender_addr)
            sender_name = _decode_h(sender_name_raw) if sender_name_raw else None
            sender_domain = sender_addr.split("@", 1)[1] if "@" in sender_addr else None

            recipients: list[Recipient] = []
            for header, rtype in [("To", "to"), ("Cc", "cc"), ("Bcc", "bcc")]:
                for nm, ad in getaddresses(msg.get_all(header) or []):
                    a = _normalize_addr(ad)
                    if a:
                        recipients.append(Recipient(
                            address=a,
                            name=_decode_h(nm) or None,
                            rtype=rtype,
                        ))

            date_sent = _parse_date_iso(msg.get("Date"))
            date_received = _first_received_date(msg)

            bulk_flag, bulk_sig = is_bulk_message(msg, sender_addr)
            body_text, body_html, body_src = _extract_body(msg, bulk_flag)

            raw_hash = hashlib.sha256(raw_bytes).hexdigest()
            attachments = _extract_attachments(msg, source_str, raw_hash)

            if not sender_addr and not rfc_msgid:
                continue

            yield EmailMessage(
                provenance=Provenance(
                    source_path=source_str,
                    raw_hash=raw_hash,
                    source_byte_offset=byte_offset,
                    source_byte_length=byte_length,
                ),
                rfc822_message_id=rfc_msgid or f"synth:{raw_hash[:16]}",
                sender_address=sender_addr or "unknown",
                date_sent=date_sent or "",
                in_reply_to=in_reply_to,
                references_chain=references,
                subject=subject,
                sender_name=sender_name,
                sender_domain=sender_domain,
                date_received=date_received,
                body_text=body_text,
                body_html=body_html,
                body_text_source=body_src,
                is_bulk=bulk_flag,
                bulk_signal=bulk_sig,
                is_multipart=msg.is_multipart(),
                has_attachments=bool(attachments),
                attachment_count=len(attachments),
                gmail_thread_id=gmail_thread_id,
                gmail_labels=gmail_labels,
                recipients=tuple(recipients),
                attachments=attachments,
            )

        except Exception:
            continue
