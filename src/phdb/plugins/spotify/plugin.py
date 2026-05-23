"""Spotify plugin — ingests Spotify Extended Streaming History.

Phase 7 port. Each play event becomes a ``schema_type='ListenAction'``
row in ``listen_actions`` with ``is_bulk=1`` (skip embedding — track
names aren't narrative text). All events bucket into a single
``spotify:listening`` thread.

Reuses ``listen_actions`` (migration 0021); no schema changes. Time
facet projection happens via the standard inThread triple emission —
``run()`` returns when the source zip / directory is fully consumed.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.spotify_json import parse as parse_spotify
from phdb.log import get_logger
from phdb.plugins.spotify.ingest import (
    emit_thread_triple,
    register_source_file,
    upsert_listen_action,
)
from phdb.records import MediaPlay

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.spotify")


@dataclass
class IngestSummary:
    """Result of one ``run()`` call — mirrors the legacy IngestReport surface."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    threads_created: int = 0
    errors: list[str] = field(default_factory=list)


class SpotifyPlugin(PhdbSourcePlugin):
    """Spotify plugin — Phase 7 port."""

    SOURCE_KIND = "spotify"
    FILE_KIND = "json"
    SOURCE_TABLE = "listen_actions"
    THREAD_KEY = "listening"
    BATCH_SIZE = 1000

    def __init__(
        self,
        manifest: PluginManifest | None = None,
        *,
        max_seconds: float | None = None,
    ) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]
        self.max_seconds = max_seconds

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every Spotify export.

        Accepts either a streaming-history zip or a directory containing
        ``Streaming_History_*.json`` files. The legacy adapter ran against
        the directory or zip as a whole — the source_files row is keyed on
        that container path, not on individual JSON shards.
        """
        if root.is_file():
            if root.suffix.lower() == ".zip":
                yield root, self.SOURCE_KIND
            return
        # Directory: yield the directory itself if it holds streaming-history
        # JSON or a packaged zip — the parser walks the structure from there.
        has_json = any(root.rglob("Streaming_History_*.json"))
        has_zip = any(root.glob("*.zip"))
        if has_json or has_zip:
            yield root, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[MediaPlay]:
        """Yield MediaPlay records from one Spotify source path."""
        yield from parse_spotify(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: MediaPlay,
        *,
        source_file_id: int,
    ) -> int | None:
        """Insert one MediaPlay into listen_actions. Returns row id or None."""
        return upsert_listen_action(conn, source_file_id, record)

    def register_cli(self, parser: Any) -> None:
        """Phase 7: registration via generic ``phdb plugin ingest spotify <path>``."""
        return None

    def register_tools(self, server: Any) -> None:
        """No spotify-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one Spotify source path."""
        report = IngestSummary(source_path=str(source_path))

        source_file_id = register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id
        log.info(
            "[spotify] Source registered: id=%d path=%s",
            source_file_id, source_path,
        )

        t_start = time.time()
        batch_count = 0

        for record in self.parse(source_path):
            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                break

            report.rows_yielded += 1

            message_id = self.ingest_row(
                conn, record, source_file_id=source_file_id,
            )
            if message_id is None:
                report.rows_skipped += 1
                continue

            report.rows_inserted += 1

            _, thread_created = emit_thread_triple(
                conn, self.SOURCE_KIND, self.SOURCE_TABLE,
                message_id, self.THREAD_KEY,
            )
            if thread_created:
                report.threads_created += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[spotify] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
