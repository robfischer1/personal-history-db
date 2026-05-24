"""Parsers for browser bookmark export formats.

Two parsers are exported:

``parse_netscape_html(path)``
    Handles the Netscape Bookmark File format (``<!DOCTYPE NETSCAPE-Bookmark-file-1>``)
    exported by Chrome, Firefox, Edge, and most other browsers.  Uses only
    stdlib ``html.parser`` — no third-party deps.  Folder hierarchy is tracked
    via a stack and stored as the ``folder`` field (slash-joined path).

``parse_chrome_json(path)``
    Handles the ``Bookmarks`` file from a Chrome profile directory.  JSON with
    ``roots.bookmark_bar``, ``roots.other``, ``roots.synced`` subtrees.  Each
    node has ``type`` (``url`` or ``folder``), ``name``, ``url``, and
    ``date_added`` (WebKit timestamp: microseconds since 1601-01-01 UTC).
    The instrument is always ``'chrome'`` for this format.

Both functions yield ``BookmarkEvent`` records ready for ``ingest_row``.
"""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path

from phdb.formats.url import is_junk, normalize_url, should_skip

# Schemes that cannot be resolved to a web page — skip entirely at parse time.
# These appear in bookmark exports as bookmarklets or internal browser URLs.
_SKIP_SCHEMES = frozenset({"javascript", "about", "chrome", "file", "data", "blob"})
from phdb.records import BookmarkEvent, Provenance

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Timestamp utilities
# ---------------------------------------------------------------------------

# WebKit epoch: 1601-01-01 00:00:00 UTC in Unix epoch microseconds
_WEBKIT_EPOCH_OFFSET_US: int = 11_644_473_600 * 1_000_000


def _webkit_us_to_iso(webkit_us: int | str) -> str | None:
    """Convert a WebKit microsecond timestamp to an ISO 8601 string (UTC).

    Returns ``None`` on invalid input rather than raising.
    """
    try:
        us = int(webkit_us)
    except (TypeError, ValueError):
        return None
    if us <= 0:
        return None
    unix_us = us - _WEBKIT_EPOCH_OFFSET_US
    unix_s = unix_us / 1_000_000
    try:
        dt = datetime.fromtimestamp(unix_s, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, OverflowError, ValueError):
        return None


def _unix_s_to_iso(unix_s: int | str) -> str | None:
    """Convert a Unix epoch (seconds) timestamp to ISO 8601 string (UTC).

    The ADD_DATE attribute in Netscape HTML exports is Unix epoch seconds.
    Returns ``None`` on invalid input.
    """
    try:
        s = int(unix_s)
    except (TypeError, ValueError):
        return None
    if s <= 0:
        return None
    try:
        dt = datetime.fromtimestamp(s, tz=timezone.utc)
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, OverflowError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Raw bookmark hash (for Provenance)
# ---------------------------------------------------------------------------

def _raw_hash(url: str, instrument: str, folder: str | None) -> str:
    canonical = f"{instrument}|{url}|{folder or ''}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Netscape HTML parser
# ---------------------------------------------------------------------------

def _instrument_from_path(path_str: str) -> str:
    """Guess browser instrument from file path name (best-effort)."""
    lower = path_str.lower()
    if "chrome" in lower:
        return "chrome"
    if "firefox" in lower or "mozilla" in lower:
        return "firefox"
    if "edge" in lower:
        return "edge"
    if "safari" in lower:
        return "safari"
    if "opera" in lower:
        return "opera"
    # Default for Netscape HTML is firefox (most common exporter)
    return "firefox"


class _NetscapeParser(HTMLParser):
    """SAX-style parser for the Netscape Bookmark File format.

    Tracks the folder stack via ``<H3>`` tags (folder names) and ``<DL>``
    depth.  Bookmark ``<A>`` tags carry ``HREF``, ``ADD_DATE``, and optional
    ``TAGS``/``SHORTCUTURL`` attributes.

    Design notes:
    - ``<H3>`` immediately before a ``<DL>`` introduces a folder level.
    - ``<DT>`` is a flat separator — we ignore it structurally.
    - ``</DL>`` pops the folder stack.
    - ``<A>`` inside a ``<DL>`` (or at top level) is a bookmark.
    """

    def __init__(self, source_path: str) -> None:
        super().__init__()
        self._source_path = source_path
        self._folder_stack: list[str] = []
        self._pending_folder: str | None = None  # H3 text waiting for next <DL>
        self._in_h3: bool = False
        self._h3_text: str = ""
        self._in_a: bool = False
        self._a_attrs: dict[str, str] = {}
        self._a_text: str = ""
        self.bookmarks: list[BookmarkEvent] = []

    # -- HTMLParser overrides --

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attr_map = {k.upper(): (v or "") for k, v in attrs}

        if tag == "h3":
            self._in_h3 = True
            self._h3_text = ""

        elif tag == "dl":
            # A <DL> following an <H3> starts a new folder level.
            if self._pending_folder is not None:
                self._folder_stack.append(self._pending_folder)
                self._pending_folder = None

        elif tag == "a":
            self._in_a = True
            self._a_attrs = attr_map
            self._a_text = ""

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag == "h3":
            self._in_h3 = False
            self._pending_folder = self._h3_text.strip() or None

        elif tag == "dl":
            # Pop folder stack on </DL>; ignore underflow gracefully.
            if self._folder_stack:
                self._folder_stack.pop()
            else:
                self._pending_folder = None

        elif tag == "a":
            self._in_a = False
            self._emit_bookmark()

    def handle_data(self, data: str) -> None:
        if self._in_h3:
            self._h3_text += data
        elif self._in_a:
            self._a_text += data

    # -- Bookmark emission --

    def _emit_bookmark(self) -> None:
        attrs = self._a_attrs
        url = attrs.get("HREF", "").strip()
        if not url:
            return

        # Skip non-web schemes (javascript:, about:, chrome:, file:, etc.)
        scheme = url.split(":", 1)[0].lower() if ":" in url else ""
        if scheme in _SKIP_SCHEMES:
            return

        instrument = _instrument_from_path(self._source_path)

        skip_reason = should_skip(url)
        if skip_reason:
            return  # skip Google searches etc.

        norm = normalize_url(url)
        title = self._a_text.strip() or None
        folder = "/".join(self._folder_stack) if self._folder_stack else None

        add_date_raw = attrs.get("ADD_DATE", "")
        date_added = _unix_s_to_iso(add_date_raw) if add_date_raw else None

        tags_raw = attrs.get("TAGS", "")
        tags: tuple[str, ...] = tuple(
            t.strip() for t in tags_raw.split(",") if t.strip()
        ) if tags_raw else ()

        rh = _raw_hash(url, instrument, folder)
        prov = Provenance(source_path=self._source_path, raw_hash=rh)

        self.bookmarks.append(BookmarkEvent(
            provenance=prov,
            url=url,
            normalized_url=norm,
            date_added=date_added or "",
            instrument=instrument,
            title=title,
            folder=folder,
            tags=tags,
        ))


def parse_netscape_html(path: Path) -> Iterator[BookmarkEvent]:
    """Parse a Netscape Bookmark File (HTML format).

    Yields ``BookmarkEvent`` records.  The ``instrument`` field is inferred
    from the filename (``chrome``/``firefox``/``edge``/``safari``/``opera``).

    ``javascript:`` and ``about:`` URLs are filtered by ``should_skip``
    before they ever become records.  Folder hierarchy is preserved in
    the ``folder`` field as a slash-joined path (e.g. ``"Bookmarks Bar/Work"``).
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    parser = _NetscapeParser(source_path=str(path))
    parser.feed(text)
    yield from parser.bookmarks


# ---------------------------------------------------------------------------
# Chrome JSON parser
# ---------------------------------------------------------------------------

def _walk_chrome_node(
    node: dict[str, object],
    folder_stack: list[str],
    source_path: str,
    results: list[BookmarkEvent],
) -> None:
    """Recursively walk a Chrome Bookmarks JSON node tree."""
    node_type = node.get("type", "")

    if node_type == "folder":
        folder_name = str(node.get("name", "")).strip()
        new_stack = folder_stack + ([folder_name] if folder_name else [])
        children = node.get("children", [])
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    _walk_chrome_node(child, new_stack, source_path, results)

    elif node_type == "url":
        url = str(node.get("url", "")).strip()
        if not url:
            return

        # Skip non-web schemes (javascript:, chrome-extension:, file:, etc.)
        scheme = url.split(":", 1)[0].lower() if ":" in url else ""
        if scheme in _SKIP_SCHEMES or scheme.startswith("chrome"):
            return

        skip_reason = should_skip(url)
        if skip_reason:
            return

        norm = normalize_url(url)
        title = str(node.get("name", "")).strip() or None
        folder = "/".join(folder_stack) if folder_stack else None

        date_added_raw = node.get("date_added")
        date_added = _webkit_us_to_iso(date_added_raw) if date_added_raw else None  # type: ignore[arg-type]

        rh = _raw_hash(url, "chrome", folder)
        prov = Provenance(source_path=source_path, raw_hash=rh)

        results.append(BookmarkEvent(
            provenance=prov,
            url=url,
            normalized_url=norm,
            date_added=date_added or "",
            instrument="chrome",
            title=title,
            folder=folder,
        ))


def parse_chrome_json(path: Path) -> Iterator[BookmarkEvent]:
    """Parse a Chrome ``Bookmarks`` JSON file.

    Walks the ``roots.bookmark_bar``, ``roots.other``, and ``roots.synced``
    subtrees.  ``instrument`` is always ``'chrome'``.  Timestamps are
    converted from WebKit microseconds to ISO 8601 UTC.

    Unknown ``roots`` keys are silently skipped so the parser doesn't break
    on future Chrome Bookmarks format extensions.
    """
    text = path.read_text(encoding="utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Not valid JSON: {path}") from exc

    roots = data.get("roots", {})
    if not isinstance(roots, dict):
        return

    results: list[BookmarkEvent] = []
    source_path = str(path)

    for root_key in ("bookmark_bar", "other", "synced"):
        root_node = roots.get(root_key)
        if isinstance(root_node, dict):
            _walk_chrome_node(root_node, [], source_path, results)

    yield from results
