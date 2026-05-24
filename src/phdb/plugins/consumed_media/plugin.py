"""ConsumedMediaPlugin — Consumed Media Dissolution.

Source: 7 Entities/ subdirectories (Books, Games, Movies, Podcasts,
TV Series, YouTube Channels, Twitch Channels). Each file with a
recognized ``@type`` becomes one row in the corresponding typed table.
Single multi-type plugin routing by ``@type`` to the correct table
via ``consumed_media_md.TYPE_TO_TABLE`` / ``SUBDIR_TO_TABLE``.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.consumed_media_md import ConsumedMediaRecord
from phdb.formats.consumed_media_md import parse as parse_consumed_media_md
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.consumed_media")


@dataclass
class IngestSummary:
    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    by_table: dict[str, int] = field(default_factory=dict)


_SHARED_COLUMNS = (
    "schema_type", "name", "description", "url", "image",
    "identifier", "alternate_name", "author", "publisher",
    "date_published", "genre", "keywords",
    "file_path", "raw_hash", "source_file_id",
)

_TYPE_SPECIFIC_COLUMNS: dict[str, tuple[str, ...]] = {
    "books": ("isbn", "number_of_pages"),
    "games": ("game_platform",),
    "movies": ("duration", "actor", "director"),
    "tv_series": ("start_date", "actor", "number_of_seasons"),
    "podcasts": ("start_date",),
    "youtube_channels": (),
    "twitch_channels": (),
}


def _build_insert_sql(table_name: str) -> str:
    extra = _TYPE_SPECIFIC_COLUMNS.get(table_name, ())
    cols = list(_SHARED_COLUMNS) + list(extra)
    placeholders = ", ".join("?" for _ in cols)
    col_names = ", ".join(cols)
    return f"INSERT OR IGNORE INTO {table_name} ({col_names}) VALUES ({placeholders})"


def _build_params(
    record: ConsumedMediaRecord,
    source_file_id: int,
) -> list[object]:
    shared: list[object] = [
        record.schema_type,
        record.name,
        record.description,
        record.url,
        record.image,
        record.identifier,
        record.alternate_name,
        record.author,
        record.publisher,
        record.date_published,
        record.genre,
        record.keywords,
        record.file_path,
        record.provenance.raw_hash,
        source_file_id,
    ]

    extra_cols = _TYPE_SPECIFIC_COLUMNS.get(record.table_name, ())
    for col in extra_cols:
        shared.append(getattr(record, col, None))

    return shared


class ConsumedMediaPlugin(PhdbSourcePlugin):
    """Vault Entities/ consumed-media plugin — multi-type."""

    SOURCE_KIND = "vault-consumed-media"
    FILE_KIND = "md"
    TARGET_TABLE = "books"
    SCHEMA_TYPE = "Book"
    BATCH_SIZE = 100

    def __init__(
        self,
        manifest: PluginManifest | None = None,
    ) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]
        self._insert_cache: dict[str, str] = {}

    def _get_insert_sql(self, table_name: str) -> str:
        if table_name not in self._insert_cache:
            self._insert_cache[table_name] = _build_insert_sql(table_name)
        return self._insert_cache[table_name]

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        if root.is_dir():
            yield root, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[ConsumedMediaRecord]:
        yield from parse_consumed_media_md(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ConsumedMediaRecord,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        sf_id = source_file_id if source_file_id is not None else 0
        sql = self._get_insert_sql(record.table_name)
        params = _build_params(record, sf_id)

        cur = conn.execute(sql, params)
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            row_id = self.ingest_row(
                conn, record, source_file_id=source_file_id,
            )
            if row_id is None:
                report.rows_skipped += 1
            else:
                report.rows_inserted += 1
                report.by_table[record.table_name] = (
                    report.by_table.get(record.table_name, 0) + 1
                )

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[consumed_media] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        for tbl, cnt in sorted(report.by_table.items()):
            log.info("  %s: %d rows", tbl, cnt)
        return report
