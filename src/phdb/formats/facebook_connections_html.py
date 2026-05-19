"""Facebook connections HTML format parser — yields Connection records.

Source: Facebook export zip containing connections/friends/*.html files.
Pure parser: no DB, no identity.

Name normalization: NFKD strip + lowercase + collapse whitespace + drop minor punct.
"""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from phdb.records import Connection, Provenance

# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\.,'\"`']")


def normalize_name(name: str) -> str:
    """NFKD strip + lowercase + collapse whitespace + drop minor punctuation."""
    if not name:
        return ""
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower().strip()
    s = _PUNCT_RE.sub("", s)
    s = _WS_RE.sub(" ", s)
    return s


# ---------------------------------------------------------------------------
# Export date / id derivation
# ---------------------------------------------------------------------------

_DATE_PREFIX_RE = re.compile(r"^(\d{4})(?:[-_](\d{2}))?(?:[-_](\d{2}))?")


def derive_export_date(path: Path, label: str | None = None) -> str:
    if label:
        m = _DATE_PREFIX_RE.match(label)
        if m:
            year = m.group(1)
            month = m.group(2) or "01"
            day = m.group(3) or "01"
            if 1995 <= int(year) <= 2100:
                return f"{year}-{month}-{day}"
    m = re.search(r"(\d{4})[-_/](\d{2})[-_/](\d{2})", path.name)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).date().isoformat()
    except OSError:
        return datetime.now(UTC).date().isoformat()


def derive_export_id(path: Path, fallback_label: str | None = None) -> str:
    if fallback_label:
        return fallback_label
    return derive_export_date(path)


# ---------------------------------------------------------------------------
# FB HTML parsing
# ---------------------------------------------------------------------------

_SECTION_RE = re.compile(
    r"<section[^>]*>"
    r".*?<h2[^>]*>(?P<name>.*?)</h2>"
    r".*?<div class=\"_a72d\">(?P<date>.*?)</div>"
    r".*?</section>",
    re.DOTALL,
)

_FB_DATE_RE = re.compile(
    r"^(?P<mon>[A-Z][a-z]{2}) (?P<day>\d{1,2}), (?P<year>\d{4})"
    r"(?: (?P<hour>\d{1,2}):(?P<min>\d{2}):(?P<sec>\d{2}) (?P<ampm>am|pm))?$"
)

_MONTHS: dict[str, int] = {m: i for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}

_FB_FILE_STATUS: dict[str, str] = {
    "your_friends.html": "active",
    "removed_friends.html": "inactive",
    "sent_friend_requests.html": "pending_outbound",
    "received_friend_requests.html": "pending_inbound",
    "rejected_friend_requests.html": "rejected",
}


def parse_fb_date(s: str) -> str | None:
    if not s:
        return None
    m = _FB_DATE_RE.match(s.strip())
    if not m:
        return None
    g = m.groupdict()
    mon = _MONTHS.get(g["mon"])
    if not mon:
        return None
    year = int(g["year"])
    day = int(g["day"])
    if g["hour"] is None:
        return f"{year:04d}-{mon:02d}-{day:02d}"
    h = int(g["hour"])
    mi = int(g["min"])
    se = int(g["sec"])
    if g["ampm"] == "pm" and h != 12:
        h += 12
    if g["ampm"] == "am" and h == 12:
        h = 0
    return f"{year:04d}-{mon:02d}-{day:02d}T{h:02d}:{mi:02d}:{se:02d}"


def _strip_html_tags(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s)).strip()


def _iter_fb_html_sections(content: str) -> Iterator[tuple[str, str | None]]:
    for m in _SECTION_RE.finditer(content):
        name = _strip_html_tags(m.group("name"))
        date = _strip_html_tags(m.group("date"))
        if name:
            yield name, date or None


class _FBTakeoutParser:
    name = "fb-takeout-html"

    def detect(self, path: Path) -> bool:
        try:
            if path.is_dir():
                return ((path / "connections" / "friends" / "your_friends.html").is_file()
                        or (path / "your_friends.html").is_file())
            if path.is_file() and path.suffix.lower() == ".zip":
                with zipfile.ZipFile(path) as z:
                    names = z.namelist()
                    return any(n.endswith("connections/friends/your_friends.html") for n in names)
            if (path.is_file()
                    and path.suffix.lower() in (".html", ".htm")
                    and path.name.lower() in _FB_FILE_STATUS):
                return True
        except (zipfile.BadZipFile, OSError):
            return False
        return False

    def _iter_html_files(self, path: Path) -> Iterator[tuple[str, str]]:
        if path.is_dir():
            for fn in _FB_FILE_STATUS:
                fp = path / "connections" / "friends" / fn
                if not fp.is_file():
                    fp = path / fn
                if fp.is_file():
                    yield fn, fp.read_text(encoding="utf-8", errors="replace")
            return
        if path.suffix.lower() == ".zip":
            with zipfile.ZipFile(path) as z:
                for fn in _FB_FILE_STATUS:
                    target = f"connections/friends/{fn}"
                    candidates = [n for n in z.namelist() if n.endswith(target)]
                    if not candidates:
                        continue
                    with z.open(candidates[0]) as fh:
                        yield fn, fh.read().decode("utf-8", errors="replace")
            return
        if path.suffix.lower() in (".html", ".htm"):
            yield path.name.lower(), path.read_text(encoding="utf-8", errors="replace")

    def iter_connections(self, path: Path) -> Iterator[Connection]:
        source_str = str(path)
        for fname, content in self._iter_html_files(path):
            status = _FB_FILE_STATUS.get(fname.lower())
            if status is None:
                continue
            for raw_name, raw_date in _iter_fb_html_sections(content):
                friends_since = (parse_fb_date(raw_date)
                                 if (raw_date and status == "active") else None)
                removed_date = (parse_fb_date(raw_date)
                                if (raw_date and status == "inactive") else None)
                inactive_reason = ("removed_friends_file"
                                   if (status == "inactive"
                                       and fname.lower() == "removed_friends.html")
                                   else None)

                dedup_seed = f"facebook-conn|{fname.lower()}|{raw_name}|{raw_date}"
                raw_hash = hashlib.sha256(dedup_seed.encode()).hexdigest()

                yield Connection(
                    provenance=Provenance(
                        source_path=source_str,
                        raw_hash=raw_hash,
                    ),
                    display_name=raw_name,
                    platform="facebook",
                    connection_status=status,
                    friends_since=friends_since,
                    removed_date=removed_date,
                    inactive_reason=inactive_reason,
                )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_parser = _FBTakeoutParser()


def detect(path: Path) -> bool:
    """Return True if *path* looks like a Facebook connections takeout."""
    return _parser.detect(path)


def parse(source_path: Path) -> Iterator[Connection]:
    """Parse a Facebook connections takeout, yielding Connection records.

    Supports zip archives, extracted directories, and individual HTML files.
    """
    yield from _parser.iter_connections(source_path)
