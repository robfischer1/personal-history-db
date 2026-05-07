"""Facebook connections adapter — ingests FB friends graph from takeout exports.

Source: Facebook export zip containing connections/friends/*.html files.
Writes to the `connections` table (not messages). Custom run() override.

Name normalization: NFKD strip + lowercase + collapse whitespace + drop minor punct.
Dedupe key: url:{profile_url} if available, else name:{normalized_name}.
Reconciliation: latest sighting wins for status, earliest non-null for friends_since.
Post-pass: mark missing-from-latest as inactive.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import unicodedata
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.facebook_connections")


# ---------------------------------------------------------------------------
# Row model
# ---------------------------------------------------------------------------

@dataclass
class ConnectionRow:
    instrument: str
    display_name: str
    connection_status: str
    source_file_label: str
    friends_since: str | None = None
    profile_url: str | None = None
    profile_id: str | None = None
    vanity_slug: str | None = None
    raw_extra: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Name normalization
# ---------------------------------------------------------------------------

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[\.,'\"`’]")


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


def make_dedupe_key(profile_url: str | None, name_normalized: str) -> str:
    if profile_url:
        return f"url:{profile_url}"
    return f"name:{name_normalized}"


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

    def parse(self, path: Path) -> Iterator[ConnectionRow]:
        for fname, content in self._iter_html_files(path):
            status = _FB_FILE_STATUS.get(fname.lower())
            if status is None:
                continue
            for raw_name, raw_date in _iter_fb_html_sections(content):
                friends_since = (parse_fb_date(raw_date)
                                 if (raw_date and status == "active") else None)
                yield ConnectionRow(
                    instrument="facebook",
                    display_name=raw_name,
                    connection_status=status,
                    source_file_label=fname.lower(),
                    friends_since=friends_since,
                    raw_extra={"raw_date_str": raw_date} if raw_date else {},
                )


# ---------------------------------------------------------------------------
# Upsert + reconciliation
# ---------------------------------------------------------------------------

def hash_row(row: ConnectionRow, dedupe_key: str) -> str:
    canonical = json.dumps({
        "key": dedupe_key,
        "instrument": row.instrument,
        "name": row.display_name,
        "status": row.connection_status,
        "friends_since": row.friends_since,
        "source_file": row.source_file_label,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upsert_connection(
    conn: sqlite3.Connection,
    row: ConnectionRow,
    *,
    export_id: str,
    export_date: str,
    source_file_id: int,
) -> int:
    name_norm = normalize_name(row.display_name)
    dedupe_key = make_dedupe_key(row.profile_url, name_norm)
    rh = hash_row(row, dedupe_key)
    appearance = {
        "export_id": export_id,
        "export_date": export_date,
        "file": row.source_file_label,
        "status": row.connection_status,
        "friends_since": row.friends_since,
        "raw_name": row.display_name,
        "raw_date": row.raw_extra.get("raw_date_str"),
    }

    existing = conn.execute(
        """SELECT id, connection_status, friends_since, friends_since_source,
                  display_name, last_seen_at, appearance_count, appearances_json,
                  inactive_reason
             FROM connections
            WHERE instrument=? AND dedupe_key=?""",
        (row.instrument, dedupe_key),
    ).fetchone()

    now_iso = datetime.now(UTC).isoformat()
    if existing is None:
        appearances_json = json.dumps([appearance], ensure_ascii=False)
        inactive_reason = ("removed_friends_file"
                           if (row.connection_status == "inactive"
                               and row.source_file_label == "removed_friends.html")
                           else None)
        cur = conn.execute(
            """INSERT INTO connections
               (schema_type, instrument, dedupe_key, profile_url, profile_id, vanity_slug,
                display_name, name_normalized, person_link,
                connection_status, inactive_reason,
                friends_since, friends_since_source,
                first_seen_export, last_seen_export, last_seen_at,
                appearance_count, appearances_json, source_file_id, raw_hash,
                ingested_at, updated_at)
               VALUES ('BefriendAction', ?, ?, ?, ?, ?,
                       ?, ?, NULL,
                       ?, ?,
                       ?, ?,
                       ?, ?, ?,
                       1, ?, ?, ?,
                       ?, ?)
               RETURNING id""",
            (row.instrument, dedupe_key, row.profile_url, row.profile_id, row.vanity_slug,
             row.display_name, name_norm,
             row.connection_status, inactive_reason,
             row.friends_since, (export_id if row.friends_since else None),
             export_id, export_id, export_date,
             appearances_json, source_file_id, rh,
             now_iso, now_iso),
        )
        return int(cur.fetchone()[0])

    (row_id, prev_status, prev_fs, prev_fs_src, prev_name,
     prev_last_seen_at, prev_count, prev_app_json, prev_inactive_reason) = existing

    try:
        appearances = json.loads(prev_app_json) if prev_app_json else []
    except json.JSONDecodeError:
        appearances = []
    appearances.append(appearance)
    appearances_json = json.dumps(appearances, ensure_ascii=False)

    if export_date >= (prev_last_seen_at or ""):
        new_status = row.connection_status
        new_display_name = row.display_name
        if row.connection_status == "inactive":
            new_inactive_reason = ("removed_friends_file"
                                   if row.source_file_label == "removed_friends.html"
                                   else prev_inactive_reason)
        else:
            new_inactive_reason = None
        new_last_seen_export = export_id
        new_last_seen_at = export_date
    else:
        new_status = prev_status
        new_display_name = prev_name
        new_inactive_reason = prev_inactive_reason
        new_last_seen_export = None
        new_last_seen_at = None

    candidates = [(prev_fs, prev_fs_src),
                  (row.friends_since, export_id if row.friends_since else None)]
    candidates_filtered = [(d, s) for d, s in candidates if d]
    if candidates_filtered:
        candidates_filtered.sort(key=lambda t: str(t[0]))
        new_fs, new_fs_src = candidates_filtered[0]
    else:
        new_fs, new_fs_src = None, None

    if new_last_seen_export is not None:
        conn.execute(
            """UPDATE connections
                  SET connection_status=?, display_name=?, inactive_reason=?,
                      friends_since=?, friends_since_source=?,
                      last_seen_export=?, last_seen_at=?,
                      appearance_count=appearance_count+1,
                      appearances_json=?, source_file_id=?, raw_hash=?,
                      updated_at=?
                WHERE id=?""",
            (new_status, new_display_name, new_inactive_reason,
             new_fs, new_fs_src,
             new_last_seen_export, new_last_seen_at,
             appearances_json, source_file_id, rh,
             now_iso, row_id),
        )
    else:
        conn.execute(
            """UPDATE connections
                  SET friends_since=?, friends_since_source=?,
                      appearance_count=appearance_count+1,
                      appearances_json=?, updated_at=?
                WHERE id=?""",
            (new_fs, new_fs_src, appearances_json, now_iso, row_id),
        )
    return int(row_id)


def post_pass_infer_inactive(conn: sqlite3.Connection, current_export_id: str) -> int:
    """Flip rows that were active but missing from the latest current export."""
    cur = conn.execute(
        """UPDATE connections
              SET connection_status='inactive',
                  inactive_reason='missing_from_latest_export',
                  updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE instrument='facebook'
              AND connection_status='active'
              AND last_seen_export <> ?""",
        (current_export_id,),
    )
    return cur.rowcount


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
# Adapter
# ---------------------------------------------------------------------------

class FacebookConnectionsAdapter(Adapter):
    """Ingest Facebook friends graph from takeout exports."""

    name = "facebook_connections"
    source_kind = "facebook-connections"
    file_kind = "zip"
    schema_type = "BefriendAction"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        raise NotImplementedError("Use run() directly -- writes to connections table")

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

        parser = _FBTakeoutParser()
        if not parser.detect(source_path):
            report.errors.append(f"No FB takeout detected at: {source_path}")
            return report

        export_date = derive_export_date(source_path)
        export_id = derive_export_id(source_path)

        batch_count = 0
        for row in parser.parse(source_path):
            report.rows_yielded += 1
            upsert_connection(
                conn, row,
                export_id=export_id,
                export_date=export_date,
                source_file_id=source_file_id,
            )
            report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.batch_size:
                conn.commit()
                batch_count = 0

        conn.commit()

        # Post-pass: mark missing-from-latest as inactive
        n_inactive = post_pass_infer_inactive(conn, export_id)
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d inferred-inactive",
            self.name, report.rows_yielded, report.rows_inserted, n_inactive,
        )
        return report
