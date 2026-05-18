"""Google Activity HTML format parser — yields WebActivity records.

Parses My Activity and YouTube history HTML files from Google Takeout.
Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import re
import zipfile
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

from bs4 import BeautifulSoup

from phdb.records import Provenance, WebActivity

_TS_RE = re.compile(
    r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4},?\s+\d{1,2}:\d{2}:\d{2}\s+(?:AM|PM))"
    r"(?:\s+([A-Z]{2,4}))?"
)

_STREAM_ACTIVITY_TYPE: dict[str, str] = {
    "Search": "search",
    "Image Search": "search",
    "Video Search": "search",
    "YouTube Search": "search",
    "YouTube Watch": "watch",
    "YouTube": "watch",
    "Maps": "visit",
    "Discover": "visit",
    "Books": "visit",
    "Drive": "visit",
    "Gmail": "visit",
    "Google News": "visit",
    "Google TV": "watch",
    "Hotels": "visit",
    "Help": "visit",
    "Developers": "visit",
    "Google Lens": "search",
    "Google Store": "visit",
    "Google Translate": "visit",
    "Ads": "visit",
    "Assistant": "visit",
    "Flights": "visit",
    "AI Mode": "visit",
    "Guidebooks": "visit",
    "Takeout": "visit",
}


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
        action_verb = action_verb.replace("\xa0", " ").strip() or stream_name

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


def parse(source_path: Path) -> Iterator[WebActivity]:
    """Parse Google Activity HTML files, yielding WebActivity records."""
    source_str = str(source_path)

    for fi, (_relpath, html_bytes, stream_label) in enumerate(_yield_activity_files(source_path)):
        try:
            html_str = html_bytes.decode("utf-8", errors="replace")
        except Exception:
            continue

        for ei, entry in enumerate(_parse_activity_entries(html_str, stream_label)):
            stream = entry["stream_name"] or stream_label
            title = entry["title"]
            url = entry["url"]
            ts = entry["timestamp"]

            activity_type = _STREAM_ACTIVITY_TYPE.get(str(stream), "visit")

            dedup_seed = f"google-activity|{fi}|{ei}|{stream}|{ts}|{url or title}"
            raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

            yield WebActivity(
                provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
                activity_type=activity_type,
                date_performed=ts or "",
                platform=f"google:{stream}",
                url=url,
                title=title,
                query=entry["action_verb"] if activity_type == "search" else None,
            )
