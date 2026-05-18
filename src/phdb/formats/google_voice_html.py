"""Google Voice HTML format parser — yields ChatMessage and CallRecord records.

Parses call/text/voicemail HTML files from Google Takeout.
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

from bs4 import BeautifulSoup

from phdb.records import CallRecord, ChatMessage, Provenance

GoogleVoiceRecord = ChatMessage | CallRecord

_FILENAME_RE = re.compile(
    r"^(\+?\d+|\(?\d.*?\)?\s*\d.*) - "
    r"(Text|Voicemail|Missed|Received|Placed|Recorded) - "
    r"(\d{4}-\d{2}-\d{2}T\d{2}_\d{2}_\d{2}Z)\.html$"
)


def _parse_voice_html(
    html: str, kind: str, fallback_phone: str, fallback_ts: str | None
) -> Iterator[dict[str, str | None]]:
    soup = BeautifulSoup(html, "lxml")
    if kind == "Text":
        for div in soup.select("div.message"):
            ts_el = div.find("abbr", class_="dt")
            ts = str(ts_el.get("title")) if ts_el else fallback_ts
            sender_el = div.select_one("cite.sender a.tel")
            phone = str(sender_el.get("href", "")).replace("tel:", "") if sender_el else fallback_phone
            body_el = div.find("q")
            body = body_el.get_text(" ", strip=True) if body_el else None
            if body:
                yield {"phone": phone, "ts": ts, "body": body, "kind": "Text"}
    elif kind == "Voicemail":
        ts_el = soup.find("abbr", class_="published")
        ts = str(ts_el.get("title")) if ts_el else fallback_ts
        phone_el = soup.select_one("div.contributor a.tel")
        phone = str(phone_el.get("href", "")).replace("tel:", "") if phone_el else fallback_phone
        transcript_el = soup.find("span", class_="full-text")
        if transcript_el:
            body = transcript_el.get_text(" ", strip=True)
        else:
            body = "[voicemail audio - no transcript]"
        yield {"phone": phone, "ts": ts, "body": body, "kind": "Voicemail"}
    else:
        ts_el = soup.find("abbr", class_="published") or soup.find("abbr", class_="dt")
        ts = str(ts_el.get("title")) if ts_el else fallback_ts
        phone_el = soup.select_one("a.tel")
        phone = str(phone_el.get("href", "")).replace("tel:", "") if phone_el else fallback_phone
        yield {"phone": phone, "ts": ts, "body": f"[{kind} call]", "kind": kind}


def _yield_voice_files(source_path: Path) -> Iterator[tuple[str, bytes]]:
    if source_path.is_file() and source_path.suffix == ".zip":
        with zipfile.ZipFile(source_path) as zf:
            for name in sorted(zf.namelist()):
                if name.startswith("Takeout/Voice/Calls/") and name.endswith(".html"):
                    yield name, zf.read(name)
    elif source_path.is_dir():
        for p in sorted(source_path.rglob("*.html")):
            yield str(p.relative_to(source_path)), p.read_bytes()


def parse(source_path: Path) -> Iterator[GoogleVoiceRecord]:
    """Parse Google Voice HTML files, yielding ChatMessage or CallRecord records."""
    source_str = str(source_path)

    for _fi, (relpath, html_bytes) in enumerate(_yield_voice_files(source_path)):
        fname = relpath.split("/")[-1]
        m = _FILENAME_RE.match(fname)
        if m:
            fb_phone, kind, fb_ts_raw = m.group(1), m.group(2), m.group(3)
            fb_ts: str | None = fb_ts_raw.replace("_", ":")
        else:
            fb_phone, kind, fb_ts = "unknown", "Text", None

        try:
            html_str = html_bytes.decode("utf-8", errors="replace")
        except Exception:
            continue

        for ei, evt in enumerate(_parse_voice_html(html_str, kind, fb_phone, fb_ts)):
            phone = evt["phone"] or fb_phone
            body = evt["body"] or ""
            ts = evt["ts"]
            evt_kind = evt["kind"] or kind

            dedup_seed = f"google-voice|{relpath}|{ei}|{ts}|{phone}|{body[:50]}"
            raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

            if evt_kind in ("Text", "Voicemail"):
                yield ChatMessage(
                    provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
                    sender_address=phone,
                    sender_name=None,
                    date_sent=ts or "",
                    body_text=body,
                    thread_key=f"google-voice:{phone}",
                    platform_id=f"google-voice:{raw_hash}",
                )
            else:
                call_type = "voice"
                direction = "unknown"
                if evt_kind == "Missed":
                    call_type = "missed"
                    direction = "inbound"
                elif evt_kind == "Received":
                    direction = "inbound"
                elif evt_kind == "Placed":
                    direction = "outbound"

                yield CallRecord(
                    provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
                    caller_address=phone,
                    direction=direction,
                    date_start=ts or "",
                    call_type=call_type,
                )
