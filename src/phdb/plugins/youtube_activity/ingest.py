"""YouTube activity ingest — streaming HTML + CSV parsers.

Parses three Google Takeout YouTube export file types:
- watch-history.html  → activity_type='watch'
- search-history.html → activity_type='search'
- subscriptions.csv   → activity_type='subscribe'

HTML files use streaming split on ``<div class="outer-cell`` markers
to avoid loading the full DOM (files can be 16+ MB with a 141 KB
CSS header).
"""

from __future__ import annotations

import csv
import html as html_mod
import re
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO
from urllib.parse import parse_qs, urlparse

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Data record
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class YouTubeRecord:
    """One parsed YouTube activity row ready for DB insert."""

    activity_type: str       # 'watch', 'search', 'subscribe'
    video_id: str | None     # from v= query param (watch only)
    title: str | None        # video title / search query / channel title
    url: str | None          # full URL
    channel: str | None      # channel name
    channel_id: str | None   # from /channel/ID path
    timestamp: int | None    # Unix epoch seconds (None for subscriptions)
    source_file: str         # filename of the source


# ---------------------------------------------------------------------------
# Timezone abbreviation map (common US timezones in Takeout exports)
# ---------------------------------------------------------------------------

_TZ_OFFSETS: dict[str, timezone] = {
    "EST": timezone(timedelta(hours=-5)),
    "EDT": timezone(timedelta(hours=-4)),
    "CST": timezone(timedelta(hours=-6)),
    "CDT": timezone(timedelta(hours=-5)),
    "MST": timezone(timedelta(hours=-7)),
    "MDT": timezone(timedelta(hours=-6)),
    "PST": timezone(timedelta(hours=-8)),
    "PDT": timezone(timedelta(hours=-7)),
    "UTC": timezone.utc,
    "GMT": timezone.utc,
}

# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

_TS_RE = re.compile(
    r"([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4},?\s+\d{1,2}:\d{2}:\d{2}\s+(?:AM|PM))"
    r"\s+([A-Z]{2,4})"
)


def _parse_timestamp(text: str) -> int | None:
    """Parse 'Mar 8, 2026, 7:47:24 PM EDT' → Unix epoch seconds."""
    m = _TS_RE.search(text)
    if not m:
        return None
    dt_str = m.group(1).strip().rstrip(",")
    tz_abbr = m.group(2).strip()

    tz_info = _TZ_OFFSETS.get(tz_abbr)

    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%b %d, %Y %I:%M:%S %p"):
        try:
            dt = datetime.strptime(dt_str, fmt)
            if tz_info is not None:
                dt = dt.replace(tzinfo=tz_info)
            else:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp())
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# URL extraction helpers
# ---------------------------------------------------------------------------

def _extract_video_id(url: str) -> str | None:
    """Extract video_id from https://www.youtube.com/watch?v=VIDEO_ID."""
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    v_list = qs.get("v")
    if v_list:
        return v_list[0]
    return None


def _extract_channel_id(url: str) -> str | None:
    """Extract channel_id from https://www.youtube.com/channel/CHANNEL_ID."""
    parsed = urlparse(url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2 and parts[0] == "channel":
        return parts[1]
    return None


# ---------------------------------------------------------------------------
# HTML link / text extraction (regex, no DOM)
# ---------------------------------------------------------------------------

_LINK_RE = re.compile(r'<a\s+href="([^"]*)"[^>]*>(.*?)</a>', re.DOTALL)


def _extract_links(block: str) -> list[tuple[str, str]]:
    """Return [(href, link_text), ...] from an outer-cell HTML block."""
    results = []
    for m in _LINK_RE.finditer(block):
        href = html_mod.unescape(m.group(1))
        text = html_mod.unescape(re.sub(r"<[^>]+>", "", m.group(2)).strip())
        results.append((href, text))
    return results


# ---------------------------------------------------------------------------
# Streaming HTML parser
# ---------------------------------------------------------------------------

_OUTER_CELL_SPLIT = '<div class="outer-cell'


def _stream_outer_cells(fh: IO[str]) -> Iterator[str]:
    """Yield each outer-cell div block from an HTML file handle.

    Splits on the ``<div class="outer-cell`` marker. The first chunk
    (CSS header) is discarded.
    """
    buf: list[str] = []
    first = True
    for line in fh:
        if _OUTER_CELL_SPLIT in line:
            if not first and buf:
                yield "".join(buf)
            buf = [line]
            first = False
        else:
            buf.append(line)
    if buf and not first:
        yield "".join(buf)


def _parse_watch_block(block: str, source_file: str) -> YouTubeRecord | None:
    """Parse a single outer-cell block from watch-history.html."""
    # Must contain "Watched" action
    body_match = re.search(
        r'<div class="content-cell[^"]*mdl-typography--body-1"[^>]*>(.*?)</div>',
        block, re.DOTALL,
    )
    if body_match is None:
        return None

    body = body_match.group(1)
    text = html_mod.unescape(re.sub(r"<[^>]+>", " ", body)).strip()

    if not text.startswith("Watched"):
        return None

    links = _extract_links(body)
    if not links:
        return None

    video_url, video_title = links[0]
    video_id = _extract_video_id(video_url)

    channel_name: str | None = None
    channel_id: str | None = None
    if len(links) >= 2:
        channel_url, channel_name = links[1]
        channel_id = _extract_channel_id(channel_url)

    ts = _parse_timestamp(text)

    return YouTubeRecord(
        activity_type="watch",
        video_id=video_id,
        title=video_title,
        url=video_url,
        channel=channel_name,
        channel_id=channel_id,
        timestamp=ts,
        source_file=source_file,
    )


def _parse_search_block(block: str, source_file: str) -> YouTubeRecord | None:
    """Parse a single outer-cell block from search-history.html."""
    body_match = re.search(
        r'<div class="content-cell[^"]*mdl-typography--body-1"[^>]*>(.*?)</div>',
        block, re.DOTALL,
    )
    if body_match is None:
        return None

    body = body_match.group(1)
    text = html_mod.unescape(re.sub(r"<[^>]+>", " ", body)).strip()

    if not text.startswith("Searched for"):
        return None

    links = _extract_links(body)
    if not links:
        return None

    search_url, query_text = links[0]
    ts = _parse_timestamp(text)

    return YouTubeRecord(
        activity_type="search",
        video_id=None,
        title=query_text,
        url=search_url,
        channel=None,
        channel_id=None,
        timestamp=ts,
        source_file=source_file,
    )


def _parse_html_block(block: str, source_file: str) -> YouTubeRecord | None:
    """Try parsing a block as watch first, then search."""
    record = _parse_watch_block(block, source_file)
    if record is not None:
        return record
    return _parse_search_block(block, source_file)


def parse_html(path: Path) -> Iterator[YouTubeRecord]:
    """Stream-parse a YouTube Takeout HTML file (watch or search history)."""
    source_file = path.name
    with open(path, encoding="utf-8", errors="replace") as fh:
        for block in _stream_outer_cells(fh):
            record = _parse_html_block(block, source_file)
            if record is not None:
                yield record


# ---------------------------------------------------------------------------
# CSV parser (subscriptions)
# ---------------------------------------------------------------------------

def parse_subscriptions_csv(path: Path) -> Iterator[YouTubeRecord]:
    """Parse a YouTube subscriptions.csv file."""
    source_file = path.name
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            channel_id_val = row.get("Channel Id", "").strip()
            channel_url = row.get("Channel Url", "").strip()
            channel_title = row.get("Channel Title", "").strip()

            # Extract channel_id from URL if the Channel Id column is empty
            cid = channel_id_val or None
            if cid is None and channel_url:
                cid = _extract_channel_id(channel_url)

            yield YouTubeRecord(
                activity_type="subscribe",
                video_id=None,
                title=channel_title or None,
                url=channel_url or None,
                channel=channel_title or None,
                channel_id=cid,
                timestamp=None,
                source_file=source_file,
            )


# ---------------------------------------------------------------------------
# Detect file type and route
# ---------------------------------------------------------------------------

def _detect_file_type(path: Path) -> str | None:
    """Detect file type from filename or content probe.

    Returns 'watch_html', 'search_html', 'mixed_html', 'csv', or None.
    """
    name_lower = path.name.lower()

    if name_lower.endswith(".csv"):
        return "csv"

    if not name_lower.endswith(".html"):
        return None

    if "watch-history" in name_lower:
        return "watch_html"
    if "search-history" in name_lower:
        return "search_html"

    # For generic HTML (e.g. MyActivity.html), probe first few KB
    # to see if it contains YouTube activity
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            head = fh.read(4096)
            if "YouTube" in head:
                return "mixed_html"
    except OSError:
        pass

    return None


def parse_file(path: Path) -> Iterator[YouTubeRecord]:
    """Parse a single YouTube Takeout file, auto-detecting type."""
    file_type = _detect_file_type(path)
    if file_type is None:
        return

    if file_type == "csv":
        yield from parse_subscriptions_csv(path)
    else:
        # All HTML variants use the same streaming parser
        yield from parse_html(path)


def discover_files(root: Path) -> Iterator[Path]:
    """Discover YouTube Takeout files under a directory root.

    Looks for known filenames in the Google Takeout directory structure.
    """
    if root.is_file():
        yield root
        return

    known_files = [
        "watch-history.html",
        "search-history.html",
        "subscriptions.csv",
        "MyActivity.html",
    ]

    for name in known_files:
        for p in sorted(root.rglob(name)):
            # Only yield MyActivity.html if it's under a YouTube-related path
            if name == "MyActivity.html" and "YouTube" not in str(p):
                continue
            yield p
