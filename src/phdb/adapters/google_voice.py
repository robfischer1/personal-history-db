"""Google Voice adapter — ingests call/text/voicemail HTMLs from Takeout.

Source: a Takeout zip or directory with Voice/Calls/*.html files.
Filename pattern: +1NNNNNNNNNN - Text|Voicemail|Missed|Received|Placed - YYYY-MM-DDTHH_MM_SSZ.html
Text/Voicemail -> Message; Missed/Received/Placed -> Action.
Per-phone threads.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterator
from pathlib import Path

from bs4 import BeautifulSoup

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.google_voice")

_MAX_BODY_LEN = 5000
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
                yield {"phone": phone, "ts": ts, "body": body, "kind": "Message"}
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
        yield {"phone": phone, "ts": ts, "body": body, "kind": "Message"}
    else:
        ts_el = soup.find("abbr", class_="published") or soup.find("abbr", class_="dt")
        ts = str(ts_el.get("title")) if ts_el else fallback_ts
        phone_el = soup.select_one("a.tel")
        phone = str(phone_el.get("href", "")).replace("tel:", "") if phone_el else fallback_phone
        yield {"phone": phone, "ts": ts, "body": f"[{kind} call]", "kind": "Action"}


def _yield_voice_files(source_path: Path) -> Iterator[tuple[str, bytes]]:
    if source_path.is_file() and source_path.suffix == ".zip":
        with zipfile.ZipFile(source_path) as zf:
            for name in sorted(zf.namelist()):
                if name.startswith("Takeout/Voice/Calls/") and name.endswith(".html"):
                    yield name, zf.read(name)
    elif source_path.is_dir():
        for p in sorted(source_path.rglob("*.html")):
            yield str(p.relative_to(source_path)), p.read_bytes()


class GoogleVoiceAdapter(Adapter):
    """Ingest Google Voice call/text/voicemail HTMLs."""

    name = "google_voice"
    source_kind = "google-voice"
    file_kind = "html"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for fi, (relpath, html_bytes) in enumerate(_yield_voice_files(source_path)):
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
                body = (evt["body"] or "")[:_MAX_BODY_LEN]
                ts = evt["ts"]
                schema_t = str(evt["kind"])

                dedup_seed = f"google-voice|{relpath}|{ei}|{ts}|{phone}|{body[:50]}"
                raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

                yield AdapterRow(
                    schema_type=schema_t,
                    rfc822_message_id=f"google-voice:{raw_hash}",
                    subject=f"{kind} from {phone}",
                    sender_address=str(phone),
                    direction="unknown",
                    date_sent=str(ts) if ts else None,
                    body_text=body,
                    body_text_source="google-voice-html",
                    source_byte_offset=fi,
                    source_byte_length=ei,
                    raw_hash=raw_hash,
                    body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                    thread_key=f"google-voice:{phone}",
                )
