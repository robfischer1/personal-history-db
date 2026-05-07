"""Raindrop.io bookmarks adapter — ingests Raindrop CSV + scattered older backups.

Writes to the `bookmarks` table (not messages). Custom run() override.
Supports multiple format parsers: raindrop_csv, netscape_html, session_buddy_csv,
session_buddy_json, safari_db.

URL normalization: lowercase scheme+host, strip default ports, drop fragment,
drop tracking params (utm_*, fbclid, etc.), http->https collapse.
Dedup: UNIQUE(normalized_url, instrument) with ON CONFLICT incrementing
appearance_count and extending [first_seen, last_seen] window.
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
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.raindrop")


# ---------------------------------------------------------------------------
# Tracking params stripped during normalization
# ---------------------------------------------------------------------------

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "utm_name", "utm_brand", "utm_social", "utm_social-type",
    "fbclid", "gclid", "msclkid", "dclid",
    "mc_cid", "mc_eid",
    "_ga", "_gl", "yclid", "ref_src", "ref_url",
    "gad_source", "gad_campaignid", "gbraid", "wbraid",
    "rlz", "oq", "aqs", "sourceid", "ie", "client", "gs_lcrp", "sxsrf",
    "ved", "uact", "ei", "iflsig", "esrc", "sa", "vet", "biw", "bih",
    "psig", "gs_lp", "gs_ssp", "udm",
}


# ---------------------------------------------------------------------------
# Junk URL patterns — matched URLs get excluded=1
# ---------------------------------------------------------------------------

JUNK_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^https?://(www\.)?gmail\.com/?$"), "junk:gmail-root"),
    (re.compile(r"^https?://mail\.google\.com/(mail/?(u/\d+/?)?(\?.*)?(#inbox/?)?)?$"), "junk:gmail-inbox"),
    (re.compile(r"^https?://(www\.)?amazon\.com/?$"), "junk:amazon-root"),
    (re.compile(r"^https?://(www\.)?google\.com/?(\?.*)?$"), "junk:google-root"),
    (re.compile(r"^https?://(www\.)?facebook\.com/?$"), "junk:facebook-root"),
    (re.compile(r"^https?://(www\.)?twitter\.com/?$"), "junk:twitter-root"),
    (re.compile(r"^https?://(www\.)?youtube\.com/?$"), "junk:youtube-root"),
    (re.compile(r"^https?://(www\.)?reddit\.com/?$"), "junk:reddit-root"),
    (re.compile(r"^https?://(www\.)?old\.reddit\.com/?$"), "junk:reddit-root"),
    (re.compile(r"^https?://calendar\.google\.com/calendar/u/\d+/r/?(week|month|day|agenda)?/?$"), "junk:google-calendar-landing"),
    (re.compile(r"^https?://contacts\.google\.com/u/\d+/?$"), "junk:google-contacts-landing"),
    (re.compile(r"^https?://chrome\.google\.com/webstore"), "junk:chrome-webstore"),
    (re.compile(r"^https?://redirect\.hp\.com/"), "junk:hp-factory-redirect"),
    (re.compile(r"^https?://go\.microsoft\.com/fwlink/"), "junk:microsoft-fwlink"),
    (re.compile(r"^chrome://"), "junk:chrome-internal"),
    (re.compile(r"^javascript:"), "junk:javascript-bookmarklet"),
    (re.compile(r"^about:"), "junk:browser-internal"),
    (re.compile(r"^file://"), "junk:local-file"),
    (re.compile(r"^https?://localhost(:\d+)?/?"), "junk:localhost"),
    (re.compile(r"^https?://127\.0\.0\.1(:\d+)?/?"), "junk:localhost"),
]

SKIP_URL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"^https?://(www\.)?google\.com/search\?"), "skip:google-search-result-redundant-with-SearchAction"),
    (re.compile(r"^https?://(www\.)?google\.com/url\?"), "skip:google-redirect-redundant-with-SearchAction"),
]


# ---------------------------------------------------------------------------
# URL normalization
# ---------------------------------------------------------------------------

def normalize_url(raw: str) -> str:
    """Conservative normalization for cross-instrument dedup."""
    if not raw:
        return ""
    raw = raw.strip()
    try:
        parts = urlsplit(raw)
    except ValueError:
        return raw.lower()
    scheme = (parts.scheme or "").lower()
    netloc = parts.netloc.lower()
    if scheme == "http" and netloc.endswith(":80"):
        netloc = netloc[:-3]
    elif scheme == "https" and netloc.endswith(":443"):
        netloc = netloc[:-4]
    if scheme == "http":
        scheme = "https"
    path = (parts.path or "").rstrip("/")
    if parts.query:
        kept = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
                if k.lower() not in TRACKING_PARAMS]
        query = urlencode(kept, doseq=True)
    else:
        query = ""
    return urlunsplit((scheme, netloc, path, query, ""))


def is_junk(url: str) -> str | None:
    """Return junk-reason if URL is junk, else None."""
    if not url:
        return "junk:empty-url"
    for pat, reason in JUNK_PATTERNS:
        if pat.match(url):
            return reason
    return None


def should_skip(url: str) -> str | None:
    """Return skip-reason if URL should not enter the bookmarks table at all."""
    if not url:
        return None
    for pat, reason in SKIP_URL_PATTERNS:
        if pat.match(url):
            return reason
    return None


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


SOURCE_ORG: dict[str, str] = {
    "raindrop_csv": "Raindrop.io",
    "netscape_html": "Browser bookmark export",
    "session_buddy_csv": "Session Buddy",
    "session_buddy_json": "Session Buddy",
    "safari_db": "Apple Safari (iPhone)",
}


# ---------------------------------------------------------------------------
# Parsers — each yields dicts with url, title, note, excerpt, etc.
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
# Bookmark upsert
# ---------------------------------------------------------------------------

def hash_canonical(record: dict[str, object], instrument: str) -> str:
    tags_val = record.get("tags")
    canonical = json.dumps({
        "url": record["url"],
        "instrument": instrument,
        "title": record.get("title") or "",
        "folder": record.get("folder") or "",
        "tags": sorted(tags_val) if isinstance(tags_val, list) else [],
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upsert_bookmark(
    conn: sqlite3.Connection,
    source_file_id: int,
    instrument: str,
    record: dict[str, object],
) -> int:
    """Insert or increment-on-conflict a bookmark row."""
    url = str(record["url"])
    norm = normalize_url(url)
    junk = is_junk(url)
    rh = hash_canonical(record, instrument)
    tags_json = json.dumps(record.get("tags") or [])
    sighted = record.get("sighted_at")
    raindrop_created = sighted if instrument == "raindrop" else None

    cur = conn.execute(
        """INSERT INTO bookmarks
           (schema_type, instrument, raindrop_id, url, normalized_url,
            title, note, excerpt, cover_url, folder, tags, favorite, highlights,
            first_seen_in_instrument, last_seen_in_instrument, raindrop_created,
            appearance_count, excluded, excluded_reason, source_file_id, raw_hash)
           VALUES ('BookmarkAction', ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?,
                   1, ?, ?, ?, ?)
           ON CONFLICT(normalized_url, instrument) DO UPDATE SET
               raindrop_id  = COALESCE(excluded.raindrop_id, bookmarks.raindrop_id),
               title        = COALESCE(NULLIF(excluded.title,''),    bookmarks.title),
               note         = COALESCE(NULLIF(excluded.note,''),     bookmarks.note),
               excerpt      = COALESCE(NULLIF(excluded.excerpt,''),  bookmarks.excerpt),
               cover_url    = COALESCE(NULLIF(excluded.cover_url,''),bookmarks.cover_url),
               folder       = COALESCE(NULLIF(excluded.folder,''),   bookmarks.folder),
               tags         = excluded.tags,
               favorite     = excluded.favorite,
               highlights   = COALESCE(NULLIF(excluded.highlights,''), bookmarks.highlights),
               first_seen_in_instrument = CASE
                   WHEN excluded.first_seen_in_instrument IS NULL THEN bookmarks.first_seen_in_instrument
                   WHEN bookmarks.first_seen_in_instrument IS NULL THEN excluded.first_seen_in_instrument
                   WHEN excluded.first_seen_in_instrument < bookmarks.first_seen_in_instrument THEN excluded.first_seen_in_instrument
                   ELSE bookmarks.first_seen_in_instrument
               END,
               last_seen_in_instrument = CASE
                   WHEN excluded.last_seen_in_instrument IS NULL THEN bookmarks.last_seen_in_instrument
                   WHEN bookmarks.last_seen_in_instrument IS NULL THEN excluded.last_seen_in_instrument
                   WHEN excluded.last_seen_in_instrument > bookmarks.last_seen_in_instrument THEN excluded.last_seen_in_instrument
                   ELSE bookmarks.last_seen_in_instrument
               END,
               raindrop_created = COALESCE(excluded.raindrop_created, bookmarks.raindrop_created),
               appearance_count = bookmarks.appearance_count + 1,
               source_file_id   = excluded.source_file_id
           RETURNING id""",
        (instrument, record.get("raindrop_id"), url, norm,
         record.get("title"), record.get("note"), record.get("excerpt"),
         record.get("cover_url"), record.get("folder"), tags_json,
         1 if record.get("favorite") else 0, record.get("highlights"),
         sighted, sighted, raindrop_created,
         1 if junk else 0, junk, source_file_id, rh),
    )
    return int(cur.fetchone()[0])


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class RaindropAdapter(Adapter):
    """Ingest Raindrop.io bookmarks and scattered older bookmark backups."""

    name = "raindrop"
    source_kind = "raindrop"
    file_kind = "csv"
    schema_type = "BookmarkAction"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        raise NotImplementedError("Use run() directly — writes to bookmarks table")

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestReport:
        report = IngestReport(
            adapter_name=self.name,
            source_path=str(source_path),
            source_file_id=0,
        )

        source_file_id = self._register_source(conn, source_path)
        report.source_file_id = source_file_id

        kind = detect_kind(source_path)
        if kind == "unknown":
            report.errors.append(f"Unknown format: {source_path}")
            return report

        instrument = derive_instrument(source_path, kind)
        parser_fn = PARSERS.get(kind)
        if parser_fn is None:
            report.errors.append(f"No parser for kind={kind}")
            return report

        fallback = derive_fallback_date(source_path)
        batch_count = 0

        for record in parser_fn(source_path):  # type: ignore[operator]
            report.rows_yielded += 1
            url = str(record.get("url") or "").strip()
            if not url:
                report.rows_skipped += 1
                continue
            if should_skip(url):
                report.rows_skipped += 1
                continue
            if not record.get("sighted_at") and fallback:
                record["sighted_at"] = fallback

            upsert_bookmark(conn, source_file_id, instrument, record)
            report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.batch_size:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.name, report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
