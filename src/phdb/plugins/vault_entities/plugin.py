"""VaultEntitiesPlugin — Vault Entities/ named-object ingestion.

Source: 5 Entities/ subdirectories (People, Organizations, Places,
Software, Supplements). Each file with a recognized ``@type`` becomes
one row in the corresponding typed table. Unlike consumed-media
dissolution, the vault files are NOT deleted — the DB holds a
queryable copy of the structured metadata.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.vault_entity_md import VaultEntityRecord
from phdb.formats.vault_entity_md import parse as parse_vault_entity_md
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.vault_entities")


@dataclass
class IngestSummary:
    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    by_table: dict[str, int] = field(default_factory=dict)


_TABLE_COLUMNS: dict[str, tuple[str, ...]] = {
    "people": (
        "schema_type", "additional_type", "name", "identifier",
        "email", "telephone", "address", "birth_date", "works_for",
        "url", "same_as", "tags", "file_path", "raw_hash", "source_file_id",
    ),
    "organizations": (
        "schema_type", "additional_type", "name", "identifier",
        "url", "tags", "file_path", "raw_hash", "source_file_id",
    ),
    "entity_places": (
        "schema_type", "name", "identifier", "address", "geo",
        "telephone", "url", "tags", "file_path", "raw_hash", "source_file_id",
    ),
    "software_applications": (
        "schema_type", "name", "identifier", "url", "categories",
        "tags", "file_path", "raw_hash", "source_file_id",
    ),
    "supplements": (
        "schema_type", "additional_type", "name", "identifier",
        "description", "status", "categories", "tags",
        "file_path", "raw_hash", "source_file_id",
    ),
}


def _build_insert_sql(table_name: str) -> str:
    cols = _TABLE_COLUMNS[table_name]
    placeholders = ", ".join("?" for _ in cols)
    col_names = ", ".join(cols)
    return f"INSERT OR IGNORE INTO {table_name} ({col_names}) VALUES ({placeholders})"


def _build_params(
    record: VaultEntityRecord,
    source_file_id: int,
) -> list[object]:
    table = record.table_name
    cols = _TABLE_COLUMNS[table]
    params: list[object] = []
    for col in cols:
        if col == "source_file_id":
            params.append(source_file_id)
        elif col == "raw_hash":
            params.append(record.provenance.raw_hash)
        else:
            params.append(getattr(record, col, None))
    return params


class VaultEntitiesPlugin(PhdbSourcePlugin):
    """Vault Entities/ named-object plugin — multi-type."""

    SOURCE_KIND = "vault-entities"
    FILE_KIND = "md"
    TARGET_TABLE = "people"
    SCHEMA_TYPE = "Person"
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

    def parse(self, path: Path) -> Iterator[VaultEntityRecord]:
        yield from parse_vault_entity_md(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: VaultEntityRecord,
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
            "[vault_entities] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        for tbl, cnt in sorted(report.by_table.items()):
            log.info("  %s: %d rows", tbl, cnt)
        return report
