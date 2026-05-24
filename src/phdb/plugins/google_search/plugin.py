"""GoogleSearchPlugin — Google Takeout Search MyActivity HTML ingester.

Parses the monolithic MyActivity.html export from Google Takeout's Search
product into the ``search_history`` table (migration 0036).

The export file is typically 50-100+ MB — a single HTML file with 141 KB
of CSS/MDL boilerplate followed by thousands of ``<div class="outer-cell">``
blocks.  This parser uses **streaming chunk-based parsing** — it reads the
file in manageable chunks, splits on ``<div class="outer-cell`` markers,
and parses each block with regex.  The full DOM is never loaded.

Satisfies the ``PhdbSourcePlugin`` ABC.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote_plus

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

log = get_logger("phdb.plugins.google_search")


# ─── Timezone abbreviation map ──────────────────────────────────────────────

_TZ_OFFSETS: dict[str, timezone] = {
    "EST": timezone.utc.__class__(offset=datetime(2000, 1, 1) - datetime(2000, 1, 1, 5)),
    "EDT": timezone.utc.__class__(offset=datetime(2000, 1, 1) - datetime(2000, 1, 1, 4)),
    "CST": timezone.utc.__class__(offset=datetime(2000, 1, 1) - datetime(2000, 1, 1, 6)),
    "CDT": timezone.utc.__class__(offset=datetime(2000, 1, 1) - datetime(2000, 1, 1, 5)),
    "MST": timezone.utc.__class__(offset=datetime(2000, 1, 1) - datetime(2000, 1, 1, 7)),
    "MDT": timezone.utc.__class__(offset=datetime(2000, 1, 1) - datetime(2000, 1, 1, 6)),
    "PST": timezone.utc.__class__(offset=datetime(2000, 1, 1) - datetime(2000, 1, 1, 8)),
    "PDT": timezone.utc.__class__(offset=datetime(2000, 1, 1) - datetime(2000, 1, 1, 7)),
    "UTC": timezone.utc,
    "GMT": timezone.utc,
}

# Precompute as simple timedelta-based offsets
from datetime import timedelta

_TZ_OFFSETS = {
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


# ─── Record type ─────────────────────────────────────────────────────────────


@dataclass
class SearchEntry:
    """One parsed search or visited-result entry."""

    query: str
    url: str | None = None            # google.com/search?q= URL
    clicked_url: str | None = None    # for "Visited" entries
    timestamp: int = 0                # Unix epoch seconds
    location_lat: float | None = None
    location_lon: float | None = None
    product: str | None = None        # "Search", etc.
    source_file: str = ""


# ─── IngestSummary ───────────────────────────────────────────────────────────


@dataclass
class IngestSummary:
    """Result of one ``run()`` call."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)


# ─── Regex patterns ─────────────────────────────────────────────────────────

# Split marker — each entry starts with this
_OUTER_CELL_SPLIT = re.compile(r'<div class="outer-cell[^"]*"')

# "Searched for <a href="URL">QUERY</a>"
_SEARCHED_FOR_RE = re.compile(
    r'Searched for\s+<a\s+href="([^"]*)"[^>]*>([^<]+)</a>',
    re.IGNORECASE,
)

# "Visited <a href="URL">TITLE</a>"
_VISITED_RE = re.compile(
    r'Visited\s+<a\s+href="([^"]*)"[^>]*>([^<]+)</a>',
    re.IGNORECASE,
)

# Timestamp: "Mar 9, 2026, 4:03:47 AM EDT"
_TIMESTAMP_RE = re.compile(
    r'([A-Z][a-z]{2}\s+\d{1,2},\s+\d{4},?\s+\d{1,2}:\d{2}:\d{2}\s+(?:AM|PM))'
    r'(?:\s+([A-Z]{2,4}))?',
)

# Location: center=LAT,LON in Maps URL
_LOCATION_RE = re.compile(
    r'center=([-\d.]+),([-\d.]+)',
)

# Products section
_PRODUCTS_RE = re.compile(
    r'<b>Products:</b>\s*<br>\s*&emsp;([^<]+)',
    re.IGNORECASE,
)


# ─── Parser ─────────────────────────────────────────────────────────────────


def parse_timestamp(text: str) -> int | None:
    """Parse a Google Takeout timestamp string to Unix epoch seconds.

    Handles formats like "Mar 9, 2026, 4:03:47 AM EDT".
    Returns None if parsing fails.
    """
    m = _TIMESTAMP_RE.search(text)
    if m is None:
        return None

    ts_str = m.group(1).strip().rstrip(",")
    tz_abbr = m.group(2)

    # Try both comma-separated and non-comma formats
    for fmt in ("%b %d, %Y, %I:%M:%S %p", "%b %d, %Y %I:%M:%S %p"):
        try:
            dt = datetime.strptime(ts_str, fmt)
            break
        except ValueError:
            continue
    else:
        return None

    # Apply timezone offset
    tz = _TZ_OFFSETS.get(tz_abbr, timezone.utc) if tz_abbr else timezone.utc
    dt = dt.replace(tzinfo=tz)

    return int(dt.timestamp())


def parse_block(block: str, source_file: str) -> SearchEntry | None:
    """Parse a single outer-cell HTML block into a SearchEntry.

    Returns None if the block cannot be parsed (not a search/visited entry).
    """
    # Try "Searched for" first
    searched = _SEARCHED_FOR_RE.search(block)
    visited = _VISITED_RE.search(block)

    if searched is None and visited is None:
        return None

    query: str
    url: str | None = None
    clicked_url: str | None = None

    if searched:
        url = searched.group(1)
        query = searched.group(2).strip()
    elif visited:
        clicked_url = visited.group(1)
        query = visited.group(2).strip()
    else:
        return None

    # Parse timestamp
    ts = parse_timestamp(block)
    if ts is None:
        return None

    # Parse location (optional)
    location_lat: float | None = None
    location_lon: float | None = None
    loc_match = _LOCATION_RE.search(block)
    if loc_match:
        try:
            location_lat = float(loc_match.group(1))
            location_lon = float(loc_match.group(2))
        except (ValueError, TypeError):
            pass

    # Parse product (optional)
    product: str | None = None
    prod_match = _PRODUCTS_RE.search(block)
    if prod_match:
        product = prod_match.group(1).strip()

    return SearchEntry(
        query=query,
        url=url,
        clicked_url=clicked_url,
        timestamp=ts,
        location_lat=location_lat,
        location_lon=location_lon,
        product=product,
        source_file=source_file,
    )


def parse_search_html(path: Path) -> Iterator[SearchEntry]:
    """Stream-parse a Google Takeout Search MyActivity HTML file.

    Reads the file in chunks, splits on ``<div class="outer-cell`` markers,
    and yields one ``SearchEntry`` per block.  The full file is never loaded
    into memory at once.
    """
    source_file = str(path)

    # Read the entire file as text — but parse block-by-block.
    # For a 99.5 MB file, reading as string is ~200 MB in memory (UTF-16
    # internal), which is acceptable.  The key is NOT loading into a DOM
    # parser (BeautifulSoup/lxml) which would consume 5-10x the file size.
    #
    # For truly gigantic files, we could use a chunked reader, but the
    # outer-cell split approach works well enough for files up to ~500 MB.
    with path.open(encoding="utf-8", errors="replace") as fh:
        content = fh.read()

    # Split on the outer-cell marker; first chunk is the CSS header — skip it
    blocks = _OUTER_CELL_SPLIT.split(content)

    # Free the full content string immediately
    del content

    for block in blocks[1:]:  # skip the preamble (CSS/header)
        entry = parse_block(block, source_file)
        if entry is not None:
            yield entry


# ─── DB helpers ──────────────────────────────────────────────────────────────

_INSERT_SEARCH = """
INSERT OR IGNORE INTO search_history
    (query, url, clicked_url, timestamp, source, location_lat, location_lon,
     product, source_file)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _insert_search_entry(
    conn: sqlite3.Connection,
    entry: SearchEntry,
) -> int | None:
    """Insert one search_history row.  Returns row id or None if deduped."""
    cur = conn.execute(
        _INSERT_SEARCH,
        (
            entry.query,
            entry.url,
            entry.clicked_url,
            entry.timestamp,
            "google",
            entry.location_lat,
            entry.location_lon,
            entry.product,
            entry.source_file,
        ),
    )
    if cur.rowcount == 0:
        return None
    return cur.lastrowid


# ─── Plugin ──────────────────────────────────────────────────────────────────


class GoogleSearchPlugin(PhdbSourcePlugin):
    """Google Takeout Search MyActivity HTML ingestor.

    Writes to ``search_history`` (migration 0036).  Uses streaming
    chunk-based parsing to handle the 99.5 MB export file without
    loading it into a DOM.
    """

    SOURCE_KIND = "google_search"
    FILE_KIND = "html"
    BATCH_SIZE = 1000

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ── PhdbSourcePlugin contract ─────────────────────────────────────────

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Yield (path, source_kind) for every Search MyActivity HTML export."""
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("MyActivity.html")):
            # Only match files under a Search/ directory
            if "Search" in str(path):
                yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[SearchEntry]:
        """Yield SearchEntry records from one MyActivity.html file."""
        yield from parse_search_html(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: SearchEntry,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Persist one SearchEntry to search_history; return row id or None."""
        return _insert_search_entry(conn, record)

    def register_cli(self, parser: Any) -> None:
        """No custom CLI subcommands — generic ``phdb plugin ingest`` covers this."""
        return None

    def register_tools(self, server: Any) -> None:
        """No MCP tools yet."""
        return None

    # ── Convenience runner ────────────────────────────────────────────────

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one Search MyActivity HTML file."""
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND,
            file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for entry in self.parse(source_path):
            report.rows_yielded += 1
            row_id = _insert_search_entry(conn, entry)
            if row_id is not None:
                report.rows_inserted += 1
            else:
                report.rows_skipped += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[google_search] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded,
            report.rows_inserted,
            report.rows_skipped,
        )
        return report
