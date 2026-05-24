"""SessionBuddyPlugin — Session Buddy browser session snapshot ingestor.

Parses ``nxs.json.v2`` exports from the Session Buddy Chrome extension and
writes two tables:

- ``browser_sessions`` — one row per snapshot or saved collection
- ``session_tabs``     — one row per tab/link within a session

Design principle: Session Buddy data is cognitive-state dwell-time signal,
not browser history.  A URL appearing across N consecutive snapshots means
it was open for N snapshot intervals.  Snapshots are NOT deduped — each is a
discrete state record.  The ``source_id`` field (Session Buddy's own id) is
the ingest-level dedup key so re-running against the same export is safe.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.session_buddy")

# ─── Record types ────────────────────────────────────────────────────────────


@dataclass
class SessionRecord:
    """One browser session snapshot or saved collection."""

    session_type: str          # snapshot-scheduled | browser-closed | collection
    source_id: str             # Session Buddy id for dedup (string for collections)
    timestamp: int | None      # Unix ms
    window_count: int
    tab_count: int
    source_file: str
    raw_hash: str


@dataclass
class TabRecord:
    """One tab/link within a session — attached to a SessionRecord."""

    session_source_id: str     # FK to SessionRecord.source_id
    window_index: int
    tab_index: int
    url: str
    title: str
    active: bool
    fav_icon_url: str
    raw_hash: str


@dataclass
class ParsedSession:
    """A session + all its tabs, yielded together from the parser."""

    session: SessionRecord
    tabs: list[TabRecord]


# ─── IngestSummary ───────────────────────────────────────────────────────────


@dataclass
class IngestSummary:
    """Result of one ``run()`` call."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0       # parsed sessions
    rows_inserted: int = 0      # sessions inserted (new)
    rows_skipped: int = 0       # sessions skipped (duplicate source_id)
    tabs_inserted: int = 0
    errors: list[str] = field(default_factory=list)
    by_type: dict[str, int] = field(default_factory=dict)


# ─── Parser ──────────────────────────────────────────────────────────────────


def _tab_hash(url: str, title: str) -> str:
    """Stable hash for a single tab record."""
    payload = f"{url}\x00{title}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def _session_hash(source_id: str, tab_count: int) -> str:
    """Stable hash for a session record."""
    payload = f"{source_id}\x00{tab_count}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def parse_session_buddy(path: Path) -> Iterator[ParsedSession]:
    """Yield ParsedSession objects from one nxs.json.v2 file.

    Yields history snapshots first (ordered by timestamp descending as
    Session Buddy exports them), then saved collections.
    """
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    source_file = str(path)

    # ── History snapshots ─────────────────────────────────────────────────
    for entry in data.get("history", []):
        source_id = str(entry["id"])          # integer Unix-ms timestamp id
        session_type = entry.get("type", "snapshot-scheduled")
        timestamp = int(entry["id"])

        tabs: list[TabRecord] = []
        for win_idx, window in enumerate(entry.get("windows", [])):
            for tab_idx, tab in enumerate(window.get("tabs", [])):
                url = tab.get("url", "")
                title = tab.get("title", "")
                tabs.append(TabRecord(
                    session_source_id=source_id,
                    window_index=win_idx,
                    tab_index=tab_idx,
                    url=url,
                    title=title,
                    active=bool(tab.get("active", False)),
                    fav_icon_url=tab.get("favIconUrl", ""),
                    raw_hash=_tab_hash(url, title),
                ))

        window_count = len(entry.get("windows", []))
        tab_count = len(tabs)
        session = SessionRecord(
            session_type=session_type,
            source_id=source_id,
            timestamp=timestamp,
            window_count=window_count,
            tab_count=tab_count,
            source_file=source_file,
            raw_hash=_session_hash(source_id, tab_count),
        )
        yield ParsedSession(session=session, tabs=tabs)

    # ── Saved collections ─────────────────────────────────────────────────
    for coll in data.get("collections", []):
        source_id = str(coll["id"])           # string id for collections
        timestamp_ms = coll.get("created")    # Unix ms (integer)
        timestamp = int(timestamp_ms) if timestamp_ms is not None else None

        tabs: list[TabRecord] = []
        for win_idx, folder in enumerate(coll.get("folders", [])):
            for tab_idx, link in enumerate(folder.get("links", [])):
                url = link.get("url", "")
                title = link.get("title", "")
                tabs.append(TabRecord(
                    session_source_id=source_id,
                    window_index=win_idx,
                    tab_index=tab_idx,
                    url=url,
                    title=title,
                    active=bool(link.get("active", False)),
                    fav_icon_url=link.get("favIconUrl", ""),
                    raw_hash=_tab_hash(url, title),
                ))

        window_count = len(coll.get("folders", []))
        tab_count = len(tabs)
        session = SessionRecord(
            session_type="collection",
            source_id=source_id,
            timestamp=timestamp,
            window_count=window_count,
            tab_count=tab_count,
            source_file=source_file,
            raw_hash=_session_hash(source_id, tab_count),
        )
        yield ParsedSession(session=session, tabs=tabs)


# ─── DB helpers ──────────────────────────────────────────────────────────────


_INSERT_SESSION = """
INSERT OR IGNORE INTO browser_sessions
    (schema_type, session_type, timestamp, window_count, tab_count,
     source_file, source_id, raw_hash, source_file_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_TAB = """
INSERT INTO session_tabs
    (schema_type, session_id, window_index, tab_index, url, title,
     active, fav_icon_url, raw_hash, source_file_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_FETCH_SESSION_ID = "SELECT id FROM browser_sessions WHERE source_id = ?"


def _ingest_parsed_session(
    conn: sqlite3.Connection,
    parsed: ParsedSession,
    *,
    source_file_id: int,
) -> tuple[bool, int]:
    """Insert one session and its tabs.

    Returns (was_new, session_db_id).  If the session was already present
    (duplicate source_id), returns (False, existing_id) and skips tab
    insertion.
    """
    s = parsed.session
    cur = conn.execute(
        _INSERT_SESSION,
        (
            "BrowserSession",
            s.session_type,
            s.timestamp,
            s.window_count,
            s.tab_count,
            s.source_file,
            s.source_id,
            s.raw_hash,
            source_file_id,
        ),
    )

    if cur.rowcount == 0:
        # Already existed — fetch existing id
        row = conn.execute(_FETCH_SESSION_ID, (s.source_id,)).fetchone()
        assert row is not None
        return False, int(row[0])

    session_db_id = int(cur.lastrowid)  # type: ignore[arg-type]

    for tab in parsed.tabs:
        conn.execute(
            _INSERT_TAB,
            (
                "SessionTab",
                session_db_id,
                tab.window_index,
                tab.tab_index,
                tab.url,
                tab.title,
                1 if tab.active else 0,
                tab.fav_icon_url,
                tab.raw_hash,
                source_file_id,
            ),
        )

    return True, session_db_id


# ─── Plugin ──────────────────────────────────────────────────────────────────


class SessionBuddyPlugin(PhdbSourcePlugin):
    """Session Buddy nxs.json.v2 ingestor.

    Writes to ``browser_sessions`` and ``session_tabs`` (migration 0034).
    """

    SOURCE_KIND = "session_buddy"
    FILE_KIND = "json"
    BATCH_SIZE = 50     # commit every 50 sessions (each session may have 500 tabs)

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ── PhdbSourcePlugin contract ─────────────────────────────────────────

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Yield (path, source_kind) for every Session Buddy JSON export."""
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("session-buddy-export*.json")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[ParsedSession]:
        """Yield ParsedSession records from one nxs.json.v2 file."""
        yield from parse_session_buddy(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ParsedSession,
        *,
        source_file_id: int | None = None,
    ) -> int:
        """Persist one ParsedSession (session + tabs); return browser_sessions.id."""
        sf_id = source_file_id if source_file_id is not None else 0
        _was_new, session_db_id = _ingest_parsed_session(
            conn, record, source_file_id=sf_id,
        )
        return session_db_id

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
        """End-to-end ingest of one Session Buddy export file."""
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND,
            file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for parsed in self.parse(source_path):
            report.rows_yielded += 1

            sf_id = source_file_id
            was_new, _sid = _ingest_parsed_session(
                conn, parsed, source_file_id=sf_id,
            )

            if was_new:
                report.rows_inserted += 1
                report.tabs_inserted += len(parsed.tabs)
                stype = parsed.session.session_type
                report.by_type[stype] = report.by_type.get(stype, 0) + 1
            else:
                report.rows_skipped += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[session_buddy] Done: %d yielded, %d inserted, %d skipped, %d tabs",
            report.rows_yielded,
            report.rows_inserted,
            report.rows_skipped,
            report.tabs_inserted,
        )
        for stype, cnt in sorted(report.by_type.items()):
            log.info("  %s: %d sessions", stype, cnt)
        return report
