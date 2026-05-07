"""Google Activity adapter — ingests My Activity + YouTube history HTMLs.

Source: a Takeout zip or directory with My Activity/*/MyActivity.html files
and YouTube history HTMLs.
Per-stream threads. All is_bulk=1. Stream -> schema_type mapping.
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

log = get_logger("phdb.adapters.google_activity")

_MAX_BODY_LEN = 2000

_STREAM_SCHEMA_TYPE: dict[str, str] = {
    "Search": "SearchAction",
    "Image Search": "SearchAction",
    "Video Search": "SearchAction",
    "YouTube Search": "SearchAction",
    "YouTube Watch": "WatchAction",
    "YouTube": "WatchAction",
    "Maps": "Action",
    "Discover": "Action",
    "Books": "Action",
    "Drive": "Action",
    "Gmail": "Action",
    "Google News": "Action",
    "Google TV": "WatchAction",
    "Hotels": "Action",
    "Help": "Action",
    "Developers": "Action",
    "Google Lens": "SearchAction",
    "Google Store": "Action",
    "Google Translate": "Action",
    "Ads": "Action",
    "Assistant": "Action",
    "Flights": "Action",
    "AI Mode": "Action",
    "Guidebooks": "Action",
    "Takeout": "Action",
}

_TS_RE = re.compile(
    r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4},?\s+\d{1,2}:\d{2}:\d{2}\s+(?:AM|PM))"
    r"(?:\s+([A-Z]{2,4}))?"
)


def _parse_ts(text: str) -> str | None:
    if not text:
        return None
    m = _TS_RE.search(text)
    if not m:
        return None
    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%b %d, %Y %I:%M:%S %p"):
        try:
            return datetime.strptime(m.group(1).rstrip(","), fmt).isoformat()
        except ValueError:
            continue
    return None


def _parse_activity_entries(
    html_content: str, default_stream: str
) -> Iterator[dict[str, str | None]]:
    soup = BeautifulSoup(html_content, "lxml")
    for cell in soup.select("div.outer-cell"):
        stream_name = default_stream
        if not stream_name:
            header = cell.select_one(".header-cell p")
            stream_name = header.get_text(" ", strip=True) if header else "Unknown"

        body_cell = None
        for ccell in cell.select("div.content-cell"):
            cls = " ".join(ccell.get("class") or [])
            if "body-1" in cls and "caption" not in cls:
                body_cell = ccell
                break
        if not body_cell:
            continue

        text_full = body_cell.get_text(" ", strip=True)
        if not text_full:
            continue

        links = body_cell.find_all("a", href=True)
        url = str(links[0]["href"]) if links else None
        title = links[0].get_text(strip=True) if links else None
        channel = links[1].get_text(strip=True) if len(links) >= 2 else None

        action_verb = ""
        for child in body_cell.children:
            if getattr(child, "name", None) == "a":
                break
            if isinstance(child, str):
                action_verb += child
            elif hasattr(child, "get_text"):
                action_verb += child.get_text(" ", strip=True)
        action_verb = action_verb.replace(" ", " ").strip() or stream_name

        yield {
            "stream_name": stream_name,
            "action_verb": action_verb,
            "title": title,
            "url": url,
            "channel": channel,
            "timestamp": _parse_ts(text_full),
        }


def _yield_activity_files(source_path: Path) -> Iterator[tuple[str, bytes, str]]:
    if source_path.is_file() and source_path.suffix == ".zip":
        with zipfile.ZipFile(source_path) as zf:
            for name in sorted(zf.namelist()):
                if name.endswith("MyActivity.html") and "My Activity/" in name:
                    parts = name.split("/")
                    stream = parts[-2] if len(parts) >= 2 else "Unknown"
                    yield name, zf.read(name), stream
                elif name.endswith("history.html") and "YouTube" in name:
                    label = "YouTube Watch" if "watch-history" in name else "YouTube Search"
                    yield name, zf.read(name), label
    elif source_path.is_dir():
        for p in sorted(source_path.rglob("MyActivity.html")):
            yield str(p.relative_to(source_path)), p.read_bytes(), p.parent.name
        for p in sorted(source_path.rglob("*history.html")):
            if "YouTube" in str(p):
                label = "YouTube Watch" if "watch-history" in p.name else "YouTube Search"
                yield str(p.relative_to(source_path)), p.read_bytes(), label


class GoogleActivityAdapter(Adapter):
    """Ingest Google My Activity + YouTube history HTMLs."""

    name = "google_activity"
    source_kind = "google-activity"
    file_kind = "html"
    schema_type = "Action"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 1000

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for fi, (_relpath, html_bytes, stream_label) in enumerate(_yield_activity_files(source_path)):
            try:
                html_str = html_bytes.decode("utf-8", errors="replace")
            except Exception:
                continue

            for ei, entry in enumerate(_parse_activity_entries(html_str, stream_label)):
                stream = entry["stream_name"] or stream_label
                action = entry["action_verb"] or ""
                title = entry["title"] or ""
                url = entry["url"] or ""
                channel = entry["channel"] or ""

                schema_t = _STREAM_SCHEMA_TYPE.get(str(stream), "Action")
                subject = f"{action} {title}".strip()[:200]

                body_parts: list[str] = []
                if title:
                    body_parts.append(f"{action} {title}")
                if channel:
                    body_parts.append(f"Channel: {channel}")
                if url:
                    body_parts.append(f"URL: {url}")
                body_text = ("\n".join(body_parts) or str(stream))[:_MAX_BODY_LEN]

                ts = entry["timestamp"]
                dedup_seed = f"google-activity|{fi}|{ei}|{stream}|{ts}|{url or title}"
                raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

                yield AdapterRow(
                    schema_type=schema_t,
                    rfc822_message_id=f"google-activity:{raw_hash}",
                    subject=subject,
                    sender_address="google:self",
                    sender_name=str(stream),
                    direction="self",
                    date_sent=str(ts) if ts else None,
                    body_text=body_text,
                    body_text_source="google-activity-html",
                    is_bulk=1,
                    bulk_signal="google-activity-event",
                    source_byte_offset=fi,
                    source_byte_length=ei,
                    raw_hash=raw_hash,
                    body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
                    thread_key=f"google-activity:{stream}",
                )

    def detect_bulk(self, row: AdapterRow) -> tuple[bool, str | None]:
        return True, "google-activity-event"
