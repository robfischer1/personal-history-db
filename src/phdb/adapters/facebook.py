"""Facebook Messenger adapter — ingests threads from a Facebook Data Export zip.

Source: facebook-*.zip with per-thread message_1.html files under
your_facebook_activity/messages/{inbox,filtered_threads,message_requests}/.
Each message becomes a schema_type='Message' row. Direction inferred by the
base class via identity settings (sender_address = sender name lowercased).
Per-thread threads.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.facebook")

_MAX_BODY_LEN = 50_000
_INBOX_PATTERNS = [
    "your_facebook_activity/messages/inbox/",
    "your_facebook_activity/messages/filtered_threads/",
    "your_facebook_activity/messages/message_requests/",
]
_FB_TS_RE = re.compile(
    r"^([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4}\s+\d{1,2}:\d{2}:\d{2}\s+(?:am|pm))$"
)


def _parse_fb_timestamp(text: str | None) -> str | None:
    if not text:
        return None
    s = text.strip()
    if not _FB_TS_RE.match(s):
        return None
    try:
        return datetime.strptime(s, "%b %d, %Y %I:%M:%S %p").isoformat()
    except ValueError:
        return None


def _parse_message_html(html: str) -> tuple[str | None, list[dict[str, object]]]:
    soup = BeautifulSoup(html, "lxml")
    title = None
    h1 = soup.find("h1")
    if h1:
        title = h1.get_text(strip=True)

    messages: list[dict[str, object]] = []
    for sec in soup.select("section._a6-g"):
        h2 = sec.find("h2")
        sender_name = h2.get_text(strip=True) if h2 else None

        body_div = sec.select_one("div._a6-p")
        body_text: str | None = None
        if body_div:
            for d in body_div.find_all("div", recursive=False):
                inner = d.find_all("div", recursive=False)
                if inner:
                    for ii in inner:
                        t = ii.get_text(" ", strip=True)
                        if t:
                            body_text = t
                            break
                else:
                    t = d.get_text(" ", strip=True)
                    if t:
                        body_text = t
                if body_text:
                    break

        has_attachment = bool(sec.find("video") or sec.find("img") or sec.find("a", href=True))
        if has_attachment and not body_text:
            for el, kind in (
                (sec.find("video", src=True), "video"),
                (sec.find("img", src=True), "image"),
                (sec.find("a", href=True), "link"),
            ):
                if el is not None:
                    src = str(el.get("src") or el.get("href") or "")
                    body_text = f"[{kind}] {Path(src).name}" if src else f"[{kind}]"
                    break

        ts_div = sec.select_one("footer ._a72d")
        date_sent = _parse_fb_timestamp(ts_div.get_text(strip=True)) if ts_div else None

        if not body_text and not has_attachment:
            continue
        if body_text and len(body_text) > _MAX_BODY_LEN:
            body_text = body_text[:_MAX_BODY_LEN]

        messages.append({
            "sender_name": sender_name,
            "date_sent": date_sent,
            "body_text": body_text,
            "has_attachment": has_attachment,
        })
    return title, messages


def _list_thread_paths(zf: zipfile.ZipFile) -> list[tuple[str, str, str]]:
    result: list[tuple[str, str, str]] = []
    for name in zf.namelist():
        if not name.endswith("/message_1.html"):
            continue
        for cat_prefix in _INBOX_PATTERNS:
            if name.startswith(cat_prefix):
                rest = name[len(cat_prefix):]
                parts = rest.split("/")
                if len(parts) >= 2:
                    category = cat_prefix.split("/")[2]
                    thread_dir = parts[0]
                    result.append((category, thread_dir, name))
                break
    return sorted(result)


class FacebookAdapter(Adapter):
    """Ingest Facebook Messenger threads from export zips."""

    name = "facebook"
    source_kind = "facebook"
    file_kind = "zip"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        with zipfile.ZipFile(source_path) as zf:
            threads = _list_thread_paths(zf)
            for fi, (category, thread_dir, html_path) in enumerate(threads):
                try:
                    html = zf.read(html_path).decode("utf-8", errors="replace")
                except Exception:
                    continue

                _title, messages = _parse_message_html(html)

                for mi, m in enumerate(messages):
                    body = m.get("body_text")
                    if not body:
                        continue
                    body = str(body)
                    sender_name = m.get("sender_name")
                    sender_addr = (str(sender_name) if sender_name else "unknown").lower()
                    date_sent = m.get("date_sent")
                    has_att = bool(m.get("has_attachment"))

                    dedup_seed = f"facebook|{html_path}|{mi}|{date_sent}|{sender_addr}|{body[:100]}"
                    raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

                    yield AdapterRow(
                        schema_type="Message",
                        rfc822_message_id=f"facebook:{raw_hash}",
                        sender_address=sender_addr,
                        sender_name=str(sender_name) if sender_name else None,
                        direction="unknown",
                        date_sent=str(date_sent) if date_sent else None,
                        body_text=body,
                        body_text_source="facebook-html",
                        has_attachments=1 if has_att else 0,
                        attachment_count=1 if has_att else 0,
                        source_byte_offset=fi,
                        source_byte_length=mi,
                        raw_hash=raw_hash,
                        body_text_hash=hashlib.sha256(body.encode()).hexdigest(),
                        thread_key=f"facebook:{category}:{thread_dir}",
                    )
