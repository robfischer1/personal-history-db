"""SMS XML adapter — ingests SMS Backup & Restore XML exports.

Source: an XML file from SMS Backup & Restore (SyncTech) containing
``<sms>`` and ``<mms>`` elements. SMS type: 1=inbound, 2=outbound.
MMS uses msg_box instead. Per-contact threading.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.sms_xml")


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


class SmsXmlAdapter(Adapter):
    """Ingest SMS Backup & Restore XML exports."""

    name = "sms_xml"
    source_kind = "sms-xml"
    file_kind = "xml"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        try:
            yield from self._iter_etree(source_path)
        except ET.ParseError:
            log.warning("[%s] Standard XML parse failed, retrying with lxml recovery", self.name)
            yield from self._iter_lxml_recovery(source_path)

    def _iter_etree(self, source_path: Path) -> Iterator[AdapterRow]:
        for _ev, elem in ET.iterparse(str(source_path), events=("end",)):
            if elem.tag == "sms":
                row = self._parse_sms(elem)
                if row:
                    yield row
                elem.clear()
            elif elem.tag == "mms":
                row = self._parse_mms(elem)
                if row:
                    yield row
                elem.clear()

    def _iter_lxml_recovery(self, source_path: Path) -> Iterator[AdapterRow]:
        from lxml import etree as lxml_etree  # type: ignore[import-untyped]

        parser = lxml_etree.XMLParser(recover=True, encoding="utf-8")
        tree = lxml_etree.parse(str(source_path), parser)  # noqa: S320
        for elem in tree.iter("sms"):
            std = ET.fromstring(lxml_etree.tostring(elem))
            row = self._parse_sms(std)
            if row:
                yield row
        for elem in tree.iter("mms"):
            std = ET.fromstring(lxml_etree.tostring(elem))
            row = self._parse_mms(std)
            if row:
                yield row

    def _parse_sms(self, elem: ET.Element) -> AdapterRow | None:
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
        direction = "inbound" if typ == "1" else ("outbound" if typ == "2" else "unknown")
        contact_name = attrs.get("contact_name") or ""
        if contact_name in ("(Unknown)", "null"):
            contact_name = ""

        date_sent = _epoch_ms_to_iso(attrs.get("date"))
        seed = f"sms-xml|{date_sent}|{addr_n}|{direction}|{body[:128]}"
        raw_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()

        recipients: list[dict[str, str]] = []
        if "~" in address:
            for extra in address.split("~")[1:]:
                n = _normalize_phone(extra)
                if n:
                    recipients.append({"address": n, "name": "", "rtype": "to"})

        return AdapterRow(
            schema_type="Message",
            rfc822_message_id=f"sms-xml:{raw_hash}",
            sender_address=addr_n if direction == "inbound" else None,
            sender_name=contact_name or addr_n if direction == "inbound" else None,
            direction=direction,
            date_sent=date_sent,
            date_received=date_sent,
            body_text=body,
            body_text_source="sms-br-xml",
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
            recipients=recipients,
            thread_key=f"sms:{addr_n}",
        )

    def _parse_mms(self, elem: ET.Element) -> AdapterRow | None:
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
        direction = "inbound" if typ == "1" else ("outbound" if typ == "2" else "unknown")
        contact_name = attrs.get("contact_name") or ""
        if contact_name in ("(Unknown)", "null"):
            contact_name = ""

        date_sent = _epoch_ms_to_iso(attrs.get("date"))
        seed = f"sms-xml|{date_sent}|{addr_n}|{direction}|{body[:128]}"
        raw_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()

        return AdapterRow(
            schema_type="Message",
            rfc822_message_id=f"sms-xml:{raw_hash}",
            sender_address=addr_n if direction == "inbound" else None,
            sender_name=contact_name or addr_n if direction == "inbound" else None,
            direction=direction,
            date_sent=date_sent,
            date_received=date_sent,
            body_text=body or None,
            body_text_source="sms-br-xml",
            is_multipart=1,
            has_attachments=int(bool(n_attach)),
            attachment_count=n_attach,
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body.encode()).hexdigest() if body else None,
            thread_key=f"sms:{addr_n}",
        )
