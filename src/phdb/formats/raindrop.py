"""Raindrop / bookmark format parser — yields BookmarkEvent records.

Supports multiple bookmark export formats: Raindrop CSV, Netscape HTML,
Session Buddy CSV/JSON, Safari SQLite DB.

Pure parser: no DB, no settings, no identity. Yields BookmarkEvent records
that adapters consume.
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path

from phdb.formats.url import (  # re-export for backward compat
    JUNK_PATTERNS,
    SKIP_URL_PATTERNS,
    TRACKING_PARAMS,
    extract_domain,
    is_junk,
    normalize_url,
    should_skip,
)
from phdb.records import BookmarkEvent, Provenance

__all__ = [
    "JUNK_PATTERNS",
    "SKIP_URL_PATTERNS",
    "SOURCE_ORG",
    "TRACKING_PARAMS",
    "extract_domain",
    "is_junk",
    "normalize_url",
    "should_skip",
]

SOURCE_ORG: dict[str, str] = {
    "raindrop_csv": "Raindrop.io",
    "netscape_html": "Browser bookmark export",
    "session_buddy_csv": "Session Buddy",
    "session_buddy_json": "Session Buddy",
    "safari_db": "Apple Safari (iPhone)",
}


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def parse_iso(s: str) -> str | None:
    if not s:
        return None
    try:
        s2 = s[:-1] + "+00:00" if s.endswith("Z") else s
        return datetime.fromisoformat(s2).astimezone(UTC).isoformat()
    except (ValueError, TypeError):
        return None


def unix_seconds_to_iso(s: str) -> str | None:
    if not s:
        return None
    try:
        n = int(s)
        if n <= 0:
            return None
        return datetime.fromtimestamp(n, tz=UTC).isoformat()
    except (ValueError, TypeError):
        return None


def ms_to_iso(n: object) -> str | None:
    try:
        n_int = int(str(n))
        if n_int <= 0:
            return None
        return datetime.fromtimestamp(n_int / 1000.0, tz=UTC).isoformat()
    except (ValueError, TypeError):
        return None


_NSDATE_EPOCH = datetime(2001, 1, 1, tzinfo=UTC)


def nsdate_to_iso(x: object) -> str | None:
    if x is None:
        return None
    try:
        f = float(x)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    if f <= 0:
        return None
    try:
        return (_NSDATE_EPOCH + timedelta(seconds=f)).isoformat()
    except (OverflowError, OSError):
        return None


def apple_int_to_iso(x: object) -> str | None:
    """Heuristic: Unix-seconds first, then NSDate; pick a 1995-2030 year."""
    if x is None:
        return None
    try:
        n = float(x)  # type: ignore[arg-type]
    except (ValueError, TypeError):
        return None
    if n <= 0:
        return None
    try:
        d = datetime.fromtimestamp(n, tz=UTC)
        if 1995 <= d.year <= 2030:
            return d.isoformat()
    except (OverflowError, OSError):
        pass
    try:
        d = _NSDATE_EPOCH + timedelta(seconds=n)
        if 1995 <= d.year <= 2030:
            return d.isoformat()
    except (OverflowError, OSError):
        pass
    return None


# ---------------------------------------------------------------------------
# Fallback date derivation
# ---------------------------------------------------------------------------

_FN_DATE_PATS = [
    re.compile(r"(?P<y>\d{4})[-_](?P<m>\d{1,2})[-_](?P<d>\d{1,2})"),
    re.compile(r"_(?P<m>\d{1,2})_(?P<d>\d{1,2})_(?P<y>\d{2})(?!\d)"),
]


def derive_fallback_date(path: Path) -> str | None:
    """Filename-date first, file mtime second."""
    name = path.name
    for pat in _FN_DATE_PATS:
        m = pat.search(name)
        if not m:
            continue
        try:
            y, mo, d = int(m.group("y")), int(m.group("m")), int(m.group("d"))
            if y < 100:
                y += 2000
            if 1995 <= y <= 2030 and 1 <= mo <= 12 and 1 <= d <= 31:
                return datetime(y, mo, d, tzinfo=UTC).isoformat()
        except (ValueError, OverflowError):
            continue
    try:
        mtime = path.stat().st_mtime
        return datetime.fromtimestamp(mtime, tz=UTC).isoformat()
    except (OSError, OverflowError):
        return None


def parse_filename_date(name: str) -> str | None:
    """Extract date from filename like 'bookmarks_4_28_24.html'."""
    m = re.search(r"(\d{1,2})_(\d{1,2})_(\d{2,4})", name)
    if not m:
        return None
    try:
        mo, dy, yr = (int(x) for x in m.groups())
        if yr < 100:
            yr += 2000
        return datetime(yr, mo, dy, tzinfo=UTC).isoformat()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# Format detection + instrument derivation
# ---------------------------------------------------------------------------

def detect_kind(path: Path) -> str:
    name = path.name.lower()
    suffix = path.suffix.lower()
    if name.endswith(".db") and "bookmark" in name.lower():
        return "safari_db"
    if "raindrop" in name and suffix == ".csv":
        return "raindrop_csv"
    if "session" in name and "buddy" in name and suffix == ".json":
        return "session_buddy_json"
    if "session_buddy" in name and suffix == ".csv":
        return "session_buddy_csv"
    if suffix in (".html", ".htm"):
        try:
            head = path.read_bytes()[:2048].decode("utf-8", errors="ignore")
        except Exception:
            return "unknown"
        if "NETSCAPE-Bookmark-file" in head:
            return "netscape_html"
    return "unknown"


def derive_instrument(path: Path, kind: str) -> str:
    name = path.name.lower()
    if kind == "raindrop_csv":
        return "raindrop"
    if kind in ("session_buddy_csv", "session_buddy_json"):
        return "session-buddy"
    if kind == "safari_db":
        return "safari"
    if kind == "netscape_html":
        if "toby" in name:
            return "toby"
        if "session" in name and "buddy" in name:
            return "session-buddy"
        if name == "bookmark.htm":
            return "ie-favorites"
        return "chrome-bookmarks"
    return "unknown"


# ---------------------------------------------------------------------------
# Raw dict parsers — each yields dicts with url, title, note, excerpt, etc.
# These are consumed by the BookmarkEvent-yielding wrappers below.
# ---------------------------------------------------------------------------

def parse_raindrop_csv(path: Path) -> Iterator[dict[str, object]]:
    with path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        expected = {"id", "title", "url", "folder", "created"}
        cols = set(reader.fieldnames or [])
        if not expected.issubset(cols):
            raise RuntimeError(
                f"Raindrop CSV missing expected cols. Got: {cols}, need: {expected}")
        for row in reader:
            url = (row.get("url") or "").strip()
            if not url:
                continue
            tags_raw = (row.get("tags") or "").strip()
            tags_list = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []
            yield {
                "url": url,
                "title": (row.get("title") or "").strip(),
                "note": (row.get("note") or "").strip(),
                "excerpt": (row.get("excerpt") or "").strip(),
                "folder": (row.get("folder") or "").strip(),
                "tags": tags_list,
                "cover_url": (row.get("cover") or "").strip(),
                "favorite": str(row.get("favorite", "")).strip().lower() == "true",
                "highlights": (row.get("highlights") or "").strip(),
                "raindrop_id": (row.get("id") or "").strip(),
                "sighted_at": parse_iso((row.get("created") or "").strip()),
            }


class _NetscapeBookmarkParser(HTMLParser):
    def __init__(self, fallback_date: str | None = None) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.current_a: dict[str, str] | None = None
        self.current_h3: dict[str, str] | None = None
        self.current_text: list[str] = []
        self.records: list[dict[str, object]] = []
        self.fallback_date = fallback_date

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        t = tag.lower()
        if t == "a":
            self.current_a = {
                "url": a.get("href") or "",
                "add_date_raw": a.get("add_date") or "",
                "last_modified_raw": a.get("last_modified") or "",
            }
            self.current_text = []
        elif t == "h3":
            self.current_h3 = {"add_date_raw": a.get("add_date") or ""}
            self.current_text = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        text = "".join(self.current_text).strip()
        if t == "a" and self.current_a is not None:
            url = self.current_a["url"]
            ad = unix_seconds_to_iso(self.current_a["add_date_raw"])
            if ad is None and self.fallback_date is not None:
                ad = self.fallback_date
            self.records.append({
                "url": url,
                "title": text,
                "note": "",
                "excerpt": "",
                "folder": "/".join(self.stack),
                "tags": [],
                "cover_url": "",
                "favorite": False,
                "highlights": "",
                "raindrop_id": None,
                "sighted_at": ad,
            })
            self.current_a = None
            self.current_text = []
        elif t == "h3" and self.current_h3 is not None:
            self.stack.append(text)
            self.current_h3 = None
            self.current_text = []
        elif t == "dl":
            if self.stack:
                self.stack.pop()

    def handle_data(self, data: str) -> None:
        self.current_text.append(data)


def parse_netscape_html(path: Path) -> Iterator[dict[str, object]]:
    fallback = parse_filename_date(path.name)
    if not fallback:
        try:
            fallback = datetime.fromtimestamp(
                path.stat().st_mtime, tz=UTC).isoformat()
        except OSError:
            fallback = None
    p = _NetscapeBookmarkParser(fallback_date=fallback)
    p.feed(path.read_text(encoding="utf-8", errors="replace"))
    yield from p.records


def parse_session_buddy_csv(path: Path) -> Iterator[dict[str, object]]:
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = (row.get("URL") or row.get("url") or "").strip()
            if not url:
                continue
            yield {
                "url": url,
                "title": (row.get("Title") or "").strip(),
                "note": "",
                "excerpt": "",
                "folder": (row.get("Window") or "").strip(),
                "tags": [],
                "cover_url": "",
                "favorite": False,
                "highlights": "",
                "raindrop_id": None,
                "sighted_at": None,
            }


def parse_session_buddy_json(path: Path) -> Iterator[dict[str, object]]:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    for collection in data.get("collections", []) or []:
        coll_created = collection.get("created")
        for folder in collection.get("folders", []) or []:
            folder_name = folder.get("name") or folder.get("title") or ""
            for link in folder.get("links", []) or []:
                url = (link.get("url") or "").strip()
                if not url:
                    continue
                yield {
                    "url": url,
                    "title": (link.get("title") or "").strip(),
                    "note": "",
                    "excerpt": "",
                    "folder": folder_name,
                    "tags": [],
                    "cover_url": link.get("favIconUrl", "") or "",
                    "favorite": False,
                    "highlights": "",
                    "raindrop_id": None,
                    "sighted_at": ms_to_iso(coll_created) if coll_created else None,
                }


def parse_safari_db(path: Path) -> Iterator[dict[str, object]]:
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = con.execute(
            """SELECT id, parent, type, title, url, added, last_modified
               FROM bookmarks
               WHERE deleted=0 AND url IS NOT NULL AND url != ''"""
        ).fetchall()
        folders: dict[int, tuple[str, int]] = {}
        for fid, parent, _ftype, title, url, _added, _lm in con.execute(
            "SELECT id, parent, type, title, url, added, last_modified FROM bookmarks WHERE deleted=0"
        ):
            if (url or "") == "" and (title or "") != "":
                folders[fid] = (title, parent)
    finally:
        con.close()

    def folder_path(start: int) -> str:
        names: list[str] = []
        cur = start
        seen: set[int] = set()
        while cur and cur not in seen:
            seen.add(cur)
            ft = folders.get(cur)
            if not ft:
                break
            names.append(ft[0] or "")
            cur = ft[1] or 0
        return "/".join(reversed([n for n in names if n]))

    for _id, parent, _type, title, url, added, lm in rows:
        if not url:
            continue
        yield {
            "url": url,
            "title": title or "",
            "note": "",
            "excerpt": "",
            "folder": folder_path(parent) or "",
            "tags": [],
            "cover_url": "",
            "favorite": False,
            "highlights": "",
            "raindrop_id": None,
            "sighted_at": nsdate_to_iso(lm) if lm else apple_int_to_iso(added),
        }


PARSERS: dict[str, object] = {
    "raindrop_csv": parse_raindrop_csv,
    "netscape_html": parse_netscape_html,
    "session_buddy_csv": parse_session_buddy_csv,
    "session_buddy_json": parse_session_buddy_json,
    "safari_db": parse_safari_db,
}


# ---------------------------------------------------------------------------
# Dict-to-BookmarkEvent conversion
# ---------------------------------------------------------------------------

def _dict_to_event(
    record: dict[str, object],
    source_path: str,
    instrument: str,
    fallback_date: str | None,
) -> BookmarkEvent | None:
    """Convert a raw parser dict to a BookmarkEvent, or None if skip/empty."""
    url = str(record.get("url") or "").strip()
    if not url:
        return None
    if should_skip(url):
        return None

    sighted = record.get("sighted_at")
    date_added = str(sighted) if sighted else (fallback_date or "")

    norm = normalize_url(url)
    tags_val = record.get("tags")
    tags: tuple[str, ...] = ()
    if isinstance(tags_val, list):
        tags = tuple(tags_val)

    dedup_seed = f"{norm}|{instrument}"
    raw_hash = hashlib.sha256(dedup_seed.encode("utf-8")).hexdigest()

    return BookmarkEvent(
        provenance=Provenance(source_path=source_path, raw_hash=raw_hash),
        url=url,
        normalized_url=norm,
        date_added=date_added,
        instrument=instrument,
        title=(str(record.get("title") or "").strip()) or None,
        description=(str(record.get("note") or "").strip()) or None,
        tags=tags,
        folder=(str(record.get("folder") or "").strip()) or None,
        is_dead=None,
        note=str(record.get("note") or "").strip() or None,
        excerpt=str(record.get("excerpt") or "").strip() or None,
        cover_url=str(record.get("cover_url") or "").strip() or None,
        favorite=bool(record.get("favorite")),
        highlights=str(record.get("highlights") or "").strip() or None,
        raindrop_id=str(record["raindrop_id"]) if record.get("raindrop_id") else None,
    )


# ---------------------------------------------------------------------------
# Top-level parse() — auto-detects format and yields BookmarkEvent records
# ---------------------------------------------------------------------------

def parse(source_path: Path) -> Iterator[BookmarkEvent]:
    """Auto-detect bookmark format and yield BookmarkEvent records."""
    kind = detect_kind(source_path)
    if kind == "unknown":
        return

    instrument = derive_instrument(source_path, kind)
    parser_fn = PARSERS.get(kind)
    if parser_fn is None:
        return

    fallback = derive_fallback_date(source_path)
    source_str = str(source_path)

    for record in parser_fn(source_path):  # type: ignore[operator]
        event = _dict_to_event(record, source_str, instrument, fallback)
        if event is not None:
            yield event
