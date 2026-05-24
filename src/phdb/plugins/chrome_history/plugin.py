"""ChromeHistoryPlugin — Chrome browser history from Google Takeout.

Consumes ``History.json`` files from Google Takeout Chrome exports.
Each entry is a page visit with a microsecond-precision Unix timestamp,
a URL, a title, a page-transition qualifier, and a client-profile ID.

The plugin converts ``time_usec`` to Unix epoch seconds and inserts
rows into the ``browser_history`` table (migration 0035). Dedup key
is ``(url, timestamp, browser)`` — same URL at the same second from
the same browser is treated as a duplicate.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.chrome_history")


@dataclass(frozen=True)
class BrowserHistoryRecord:
    """One parsed browser-history visit from Chrome Takeout JSON."""

    url: str
    title: str | None
    timestamp: int          # Unix epoch seconds
    page_transition: str | None
    profile: str | None
    favicon_url: str | None
    raw_hash: str


def _hash_entry(entry: dict[str, Any]) -> str:
    """Produce a deterministic hash for dedup/provenance."""
    raw = json.dumps(entry, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def parse_chrome_history(path: Path) -> Iterator[BrowserHistoryRecord]:
    """Yield BrowserHistoryRecord from a Chrome History.json file.

    Uses a streaming-friendly approach: reads the JSON, then yields
    one record per entry. The file is typically 10-20 MB so full-load
    is acceptable; the generator pattern keeps the downstream
    insert loop memory-efficient.
    """
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    entries = data.get("Browser History", [])
    for entry in entries:
        url = entry.get("url", "")
        if not url:
            continue

        time_usec = entry.get("time_usec", 0)
        timestamp = int(time_usec) // 1_000_000  # microseconds → seconds

        yield BrowserHistoryRecord(
            url=url,
            title=entry.get("title"),
            timestamp=timestamp,
            page_transition=entry.get("page_transition_qualifier"),
            profile=entry.get("client_id"),
            favicon_url=entry.get("favicon_url"),
            raw_hash=_hash_entry(entry),
        )


class ChromeHistoryPlugin(PhdbSourcePlugin):
    """Chrome browser history plugin — Google Takeout JSON."""

    SOURCE_KIND = "chrome-history"
    FILE_KIND = "json"
    BATCH_SIZE = 1000

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for Chrome History JSON files."""
        if root.is_file():
            if root.suffix.lower() == ".json":
                yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*.json")):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[BrowserHistoryRecord]:
        """Yield BrowserHistoryRecord objects from one Chrome History.json file."""
        yield from parse_chrome_history(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: BrowserHistoryRecord,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Insert one browser-history row. Returns row id or None on dedup skip."""
        sf_id = source_file_id if source_file_id is not None else 0
        cur = conn.execute(
            """INSERT INTO browser_history
               (schema_type, url, title, timestamp, page_transition,
                browser, profile, source_file, source_file_id, raw_hash)
               VALUES ('BrowserHistory', ?, ?, ?, ?, 'chrome', ?, ?, ?, ?)
               ON CONFLICT(url, timestamp, browser) DO NOTHING
               RETURNING id""",
            (
                record.url,
                record.title,
                record.timestamp,
                record.page_transition,
                record.profile,
                None,  # source_file — set by run() if needed
                sf_id,
                record.raw_hash,
            ),
        )
        row = cur.fetchone()
        if row is not None:
            return int(row[0])
        return None

    def register_cli(self, parser: Any) -> None:
        """Registration via generic ``phdb plugin ingest chrome_history <path>``."""
        return None

    def register_tools(self, server: Any) -> None:
        """No chrome_history-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> Any:
        """End-to-end ingest of one Chrome History.json file."""
        from phdb.core.plugin.summary import IngestSummary

        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            row_id = self.ingest_row(conn, record, source_file_id=source_file_id)
            if row_id is None:
                report.rows_skipped += 1
            else:
                report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[chrome-history] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
