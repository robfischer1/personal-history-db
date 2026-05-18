"""SMS Backup & Restore XML format parser — yields ChatMessage and CallRecord.

Handles both sms/mms XML (text messages) and call-log XML (phone calls).
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from phdb.records import CallRecord, ChatMessage, Provenance
from phdb.records.common import Recipient

SMSBRRecord = ChatMessage | CallRecord

_CALL_TYPE_NAME = {
    "1": "incoming", "2": "outgoing", "3": "missed",
    "4": "voicemail", "5": "rejected", "6": "refused",
}

_CALL_TYPE_MAP = {
    "1": "voice", "2": "voice", "3": "missed",
    "4": "voicemail", "5": "rejected", "6": "refused",
}


def _normalize_phone(addr: str) -> str:
    if not addr:
        return ""
    addr = addr.strip()
    plus = "+" if addr.startswith("+") else ""
    digits = re.sub(r"[^\d]", "", addr)
    return plus + digits


def _epoch_ms_to_iso(ms: str | None) -> str | None:
    if not ms:
        return None
    try:
        ts = int(ms) / 1000.0
        return datetime.fromtimestamp(ts, tz=UTC).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _parse_mms_parts(elem: ET.Element) -> tuple[str, int]:
    body_parts: list[str] = []
    n_attach = 0
    parts = elem.find("parts")
    if parts is None:
        return "", 0
    for part in parts.findall("part"):
        ct = part.get("ct", "")
        if ct == "text/plain":
            txt = part.get("text") or ""
            if txt and txt != "null":
                body_parts.append(txt)
        elif ct == "application/smil":
            continue
        else:
            n_attach += 1
    return "\n".join(body_parts), n_attach


def _parse_sms_element(elem: ET.Element, source_str: str) -> ChatMessage | None:
    attrs = elem.attrib
    address = attrs.get("address", "")
    primary_addr = address.split("~")[0] if "~" in address else address
    addr_n = _normalize_phone(primary_addr)
    if not addr_n:
        return None

    body = attrs.get("body") or ""
    if body == "null":
        body = ""
    if not body:
        return None

    typ = attrs.get("type", "")
    contact_name = attrs.get("contact_name") or ""
    if contact_name in ("(Unknown)", "null"):
        contact_name = ""

    date_sent = _epoch_ms_to_iso(attrs.get("date"))
    seed = f"sms-xml|{date_sent}|{addr_n}|{'inbound' if typ == '1' else 'outbound'}|{body[:128]}"
    raw_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()

    recipients: list[Recipient] = []
    if "~" in address:
        for extra in address.split("~")[1:]:
            n = _normalize_phone(extra)
            if n:
                recipients.append(Recipient(address=n, name=None, rtype="to"))

    sender_addr = addr_n if typ == "1" else "self"
    sender_name = contact_name or addr_n if typ == "1" else None

    return ChatMessage(
        provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
        sender_address=sender_addr,
        sender_name=sender_name,
        date_sent=date_sent or "",
        body_text=body,
        has_attachments=False,
        attachment_count=0,
        thread_key=f"sms:{addr_n}",
        recipients=tuple(recipients),
        platform_id=f"sms-xml:{raw_hash}",
    )


def _parse_mms_element(elem: ET.Element, source_str: str) -> ChatMessage | None:
    body, n_attach = _parse_mms_parts(elem)
    if not body and n_attach == 0:
        return None

    attrs = elem.attrib
    address = attrs.get("address", "")
    primary_addr = address.split("~")[0] if "~" in address else address
    addr_n = _normalize_phone(primary_addr)
    if not addr_n:
        return None

    typ = attrs.get("msg_box") or attrs.get("type", "")
    contact_name = attrs.get("contact_name") or ""
    if contact_name in ("(Unknown)", "null"):
        contact_name = ""

    date_sent = _epoch_ms_to_iso(attrs.get("date"))
    direction = "inbound" if typ == "1" else "outbound"
    seed = f"sms-xml|{date_sent}|{addr_n}|{direction}|{body[:128]}"
    raw_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()

    sender_addr = addr_n if typ == "1" else "self"
    sender_name = contact_name or addr_n if typ == "1" else None

    return ChatMessage(
        provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
        sender_address=sender_addr,
        sender_name=sender_name,
        date_sent=date_sent or "",
        body_text=body or None,
        is_multipart=True,
        has_attachments=bool(n_attach),
        attachment_count=n_attach,
        thread_key=f"sms:{addr_n}",
        platform_id=f"sms-xml:{raw_hash}",
    )


def _parse_call_element(elem_attrs: dict[str, str], source_str: str) -> CallRecord | None:
    number = elem_attrs.get("number", "")
    addr_n = _normalize_phone(number)
    if not addr_n:
        return None

    typ = elem_attrs.get("type", "")
    duration_s = int(elem_attrs.get("duration") or 0)

    date_iso = _epoch_ms_to_iso(elem_attrs.get("date"))
    if typ in ("1", "3", "4"):
        direction = "inbound"
    elif typ == "2":
        direction = "outbound"
    else:
        direction = "unknown"

    seed = f"calls-xml|{elem_attrs.get('date', '')}|{addr_n}|{typ}|{duration_s}"
    raw_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()

    return CallRecord(
        provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
        caller_address=addr_n,
        direction=direction,
        date_start=date_iso or "",
        call_type=_CALL_TYPE_MAP.get(typ, "unknown"),
        duration_seconds=duration_s,
    )


def parse_sms(source_path: Path) -> Iterator[ChatMessage]:
    """Parse an SMS Backup & Restore XML file, yielding ChatMessage records."""
    source_str = str(source_path)

    try:
        yield from _iter_sms_etree(source_path, source_str)
    except ET.ParseError:
        yield from _iter_sms_lxml(source_path, source_str)


def _iter_sms_etree(source_path: Path, source_str: str) -> Iterator[ChatMessage]:
    for _ev, elem in ET.iterparse(str(source_path), events=("end",)):
        if elem.tag == "sms":
            rec = _parse_sms_element(elem, source_str)
            if rec:
                yield rec
            elem.clear()
        elif elem.tag == "mms":
            rec = _parse_mms_element(elem, source_str)
            if rec:
                yield rec
            elem.clear()


def _iter_sms_lxml(source_path: Path, source_str: str) -> Iterator[ChatMessage]:
    from lxml import etree as lxml_etree  # type: ignore[import-untyped]

    parser = lxml_etree.XMLParser(recover=True, encoding="utf-8")
    tree = lxml_etree.parse(str(source_path), parser)  # noqa: S320
    for elem in tree.iter("sms"):
        std = ET.fromstring(lxml_etree.tostring(elem))
        rec = _parse_sms_element(std, source_str)
        if rec:
            yield rec
    for elem in tree.iter("mms"):
        std = ET.fromstring(lxml_etree.tostring(elem))
        rec = _parse_mms_element(std, source_str)
        if rec:
            yield rec


def parse_calls(source_path: Path) -> Iterator[CallRecord]:
    """Parse a call-log XML file, yielding CallRecord records."""
    source_str = str(source_path)

    for _ev, elem in ET.iterparse(str(source_path), events=("end",)):
        if elem.tag == "call":
            rec = _parse_call_element(elem.attrib, source_str)
            if rec:
                yield rec
            elem.clear()
