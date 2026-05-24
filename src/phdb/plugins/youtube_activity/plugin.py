"""YouTubeActivityPlugin — Google Takeout YouTube activity ingester.

Satisfies the ``PhdbSourcePlugin`` ABC. Parses three Takeout file types:

- ``watch-history.html`` — watched videos     → ``watch_actions`` (canonical)
- ``search-history.html`` — YouTube searches  → ``search_actions`` (canonical)
- ``subscriptions.csv``  — channel follows    → ``follow_actions`` (canonical, mig 0040)
- ``MyActivity.html``    — combined activity (may overlap watch-history)

All three record types FK to ``web_pages`` via ``upsert_web_page``. Dedup is
per-canonical-table on ``(source_file_id, raw_hash)``; re-running the same
Takeout file is idempotent. Multi-file ingest: accepts a single file or a
directory of known Takeout filenames.
"""

from __future__ import annotations

import hashlib
import sqlite3
import sys
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.plugin.summary import IngestSummary
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.bookmark_upserts import upsert_web_page
from phdb.formats.url import normalize_url
from phdb.log import get_logger
from phdb.plugins.youtube_activity.ingest import (
    YouTubeRecord,
    discover_files,
    parse_file,
)

if TYPE_CHECKING:
    from phdb.settings import Settings

sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]

log = get_logger("phdb.plugins.youtube_activity")

_PLATFORM = "YouTube"


def _ts_to_iso(ts: int | None) -> str | None:
    """Convert Unix epoch seconds → ISO 8601 UTC string. None → None."""
    if ts is None:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def _raw_hash(record: YouTubeRecord) -> str:
    """Deterministic dedup hash from record identity fields."""
    parts = [
        record.activity_type,
        record.video_id or "",
        record.channel_id or "",
        record.url or "",
        record.title or "",
        str(record.timestamp) if record.timestamp is not None else "",
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()[:16]


def _resolve_web_page(
    conn: sqlite3.Connection,
    record: YouTubeRecord,
    source_file_id: int | None,
) -> int | None:
    """Upsert the WebPage row for a YouTube record's URL; return id or None."""
    if not record.url:
        return None
    normalized = normalize_url(record.url)
    if not normalized:
        return None
    return upsert_web_page(
        conn, record.url, normalized,
        title=record.title,
        sighted=_ts_to_iso(record.timestamp),
        source_file_id=source_file_id,
    )


def _insert_watch(
    conn: sqlite3.Connection,
    record: YouTubeRecord,
    source_file_id: int | None,
    web_page_id: int | None,
    raw_hash: str,
) -> int | None:
    cur = conn.execute(
        """INSERT INTO watch_actions
           (schema_type, watch_key, subject, platform_name,
            direction, date_watched, is_bulk,
            raw_hash, source_file_id, web_page_id)
           VALUES ('WatchAction', ?, ?, ?, 'self', ?, 1, ?, ?, ?)
           ON CONFLICT(source_file_id, raw_hash) DO NOTHING
           RETURNING id""",
        (
            record.video_id,
            record.title,
            _PLATFORM,
            _ts_to_iso(record.timestamp),
            raw_hash,
            source_file_id,
            web_page_id,
        ),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def _insert_search(
    conn: sqlite3.Connection,
    record: YouTubeRecord,
    source_file_id: int | None,
    web_page_id: int | None,
    raw_hash: str,
) -> int | None:
    cur = conn.execute(
        """INSERT INTO search_actions
           (schema_type, action_key, subject, sender_name,
            direction, date_performed, is_bulk,
            raw_hash, source_file_id, web_page_id)
           VALUES ('SearchAction', ?, ?, ?, 'self', ?, 1, ?, ?, ?)
           ON CONFLICT(source_file_id, raw_hash) DO NOTHING
           RETURNING id""",
        (
            raw_hash,  # action_key — opaque per-row key; raw_hash satisfies uniqueness
            record.title,
            _PLATFORM,
            _ts_to_iso(record.timestamp),
            raw_hash,
            source_file_id,
            web_page_id,
        ),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


def _insert_follow(
    conn: sqlite3.Connection,
    record: YouTubeRecord,
    source_file_id: int | None,
    web_page_id: int | None,
    raw_hash: str,
) -> int | None:
    cur = conn.execute(
        """INSERT INTO follow_actions
           (schema_type, follow_key, subject, platform_name, channel_name,
            direction, date_followed, is_bulk,
            raw_hash, source_file_id, web_page_id)
           VALUES ('FollowAction', ?, ?, ?, ?, 'self', ?, 1, ?, ?, ?)
           ON CONFLICT(source_file_id, raw_hash) DO NOTHING
           RETURNING id""",
        (
            record.channel_id,
            record.title,
            _PLATFORM,
            record.channel,
            _ts_to_iso(record.timestamp),
            raw_hash,
            source_file_id,
            web_page_id,
        ),
    )
    row = cur.fetchone()
    return int(row[0]) if row else None


class YouTubeActivityPlugin(PhdbSourcePlugin):
    """Google Takeout YouTube activity plugin (canonical-emitting)."""

    SOURCE_KIND = "youtube-activity"
    FILE_KIND = "html"
    BATCH_SIZE = 500

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every YouTube file."""
        for path in discover_files(root):
            yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[YouTubeRecord]:
        """Yield YouTubeRecord objects from one source file."""
        yield from parse_file(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: YouTubeRecord,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Upsert the WebPage row + route to the right canonical action table.

        Returns the inserted action row id, or None if deduped.
        """
        raw_hash = _raw_hash(record)
        web_page_id = _resolve_web_page(conn, record, source_file_id)

        if record.activity_type == "watch":
            return _insert_watch(conn, record, source_file_id, web_page_id, raw_hash)
        if record.activity_type == "search":
            return _insert_search(conn, record, source_file_id, web_page_id, raw_hash)
        if record.activity_type == "subscribe":
            return _insert_follow(conn, record, source_file_id, web_page_id, raw_hash)
        return None

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one or more YouTube Takeout files.

        Accepts either a single file or a directory. When given a directory,
        discovers and ingests all known YouTube Takeout files within.
        """
        report = IngestSummary(source_path=str(source_path))

        files_to_process = list(discover_files(source_path))
        if not files_to_process:
            return report

        for file_path in files_to_process:
            file_kind = "csv" if file_path.suffix.lower() == ".csv" else "html"
            source_file_id = _register_source_file(
                conn, file_path,
                source_kind=self.SOURCE_KIND,
                file_kind=file_kind,
            )

            batch_count = 0
            for record in parse_file(file_path):
                report.rows_yielded += 1
                row_id = self.ingest_row(
                    conn, record, source_file_id=source_file_id,
                )
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
            "[youtube_activity] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
