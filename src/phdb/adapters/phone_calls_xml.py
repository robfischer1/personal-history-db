"""Phone calls XML adapter — ingests SMS Backup & Restore call-log XML.

Source: a call-log XML file with ``<call>`` elements. Type codes:
1=incoming, 2=outgoing, 3=missed, 4=voicemail, 5=rejected, 6=refused.
Stored as schema_type='Action' rows.
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

log = get_logger("phdb.adapters.phone_calls_xml")

_CALL_TYPE_NAME = {
    "1": "incoming", "2": "outgoing", "3": "missed",
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


def _synthesize_body(
    call_type: str, duration_s: int, contact_name: str, number: str
) -> str:
    name = contact_name if contact_name and contact_name not in ("(Unknown)", "null") else number
    label = _CALL_TYPE_NAME.get(call_type, "unknown")
    if call_type in ("1", "2"):
        return f"Call ({label}) with {name} - {duration_s}s"
    if call_type == "3":
        return f"Missed call from {name}"
    if call_type == "4":
        return f"Voicemail from {name} - {duration_s}s"
    if call_type == "5":
        return f"Rejected call from {name}"
    if call_type == "6":
        return f"Refused-list call from {name}"
    return f"Call ({label}) with {name} - {duration_s}s"


class PhoneCallsXmlAdapter(Adapter):
    """Ingest SMS Backup & Restore call-log XML exports."""

    name = "phone_calls_xml"
    source_kind = "calls-xml"
    file_kind = "xml"
    schema_type = "Action"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for _ev, elem in ET.iterparse(str(source_path), events=("end",)):
            if elem.tag == "call":
                row = self._parse_call(elem.attrib)
                if row:
                    yield row
                elem.clear()

    def _parse_call(self, attrs: dict[str, str]) -> AdapterRow | None:
        number = attrs.get("number", "")
        addr_n = _normalize_phone(number)
        if not addr_n:
            return None

        typ = attrs.get("type", "")
        duration_s = int(attrs.get("duration") or 0)
        contact_name = attrs.get("contact_name") or ""
        if contact_name in ("(Unknown)", "null"):
            contact_name = ""

        date_iso = _epoch_ms_to_iso(attrs.get("date"))
        if typ in ("1", "3", "4"):
            direction = "inbound"
        elif typ == "2":
            direction = "outbound"
        else:
            direction = "unknown"

        seed = f"calls-xml|{attrs.get('date', '')}|{addr_n}|{typ}|{duration_s}"
        raw_hash = hashlib.sha256(seed.encode("utf-8")).hexdigest()
        body = _synthesize_body(typ, duration_s, contact_name, addr_n)

        return AdapterRow(
            schema_type="Action",
            rfc822_message_id=f"calls-xml:{raw_hash}",
            sender_address=addr_n if direction == "inbound" else None,
            sender_name=contact_name or addr_n if direction == "inbound" else None,
            direction=direction,
            date_sent=date_iso,
            date_received=date_iso,
            body_text=body,
            body_text_source="sms-br-calls-xml",
            raw_hash=raw_hash,
            body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
            thread_key=f"calls:{addr_n}",
        )
