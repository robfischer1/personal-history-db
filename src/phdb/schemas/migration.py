"""Schema migration generator — dataclass schemas vs live sqlite_master.

Phase 2 deliverable scaffold. Walks the schema registry, queries the
live DB for current columns/indexes, and emits the ALTER TABLE diff
needed to bring the live schema up to the dataclass declaration.

Phase 6 wires this into ``phdb schema regenerate`` + the post-ingest
hook. Phase 2 ships the comparator and the additive-only path
(``ADD COLUMN`` for new fields, ``CREATE INDEX IF NOT EXISTS`` for new
indexes); destructive changes (column drops, type changes) require
explicit ``--allow-destructive`` and emit a SQLite table-rebuild
sequence — wired in Phase 6.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from phdb.schemas.base import FieldSpec, Schema


@dataclass(frozen=True)
class ColumnInfo:
    """Output of PRAGMA table_info() row."""

    name: str
    sql_type: str
    notnull: bool
    default: str | None
    pk: bool


@dataclass(frozen=True)
class SchemaDiff:
    """The diff between a Schema declaration and live sqlite_master."""

    schema_table: str
    missing_columns: list[FieldSpec]
    missing_indexes: list[str]
    extra_columns: list[str]
    extra_indexes: list[str]
    type_mismatches: list[tuple[str, str, str]]  # (col, expected, actual)
    table_missing: bool = False

    @property
    def clean(self) -> bool:
        return not (
            self.missing_columns
            or self.missing_indexes
            or self.extra_columns
            or self.extra_indexes
            or self.type_mismatches
            or self.table_missing
        )


def _table_info(conn: sqlite3.Connection, table: str) -> list[ColumnInfo]:
    rows = conn.execute(f"PRAGMA table_info([{table}])").fetchall()
    out: list[ColumnInfo] = []
    for r in rows:
        out.append(ColumnInfo(
            name=r[1],
            sql_type=r[2].upper(),
            notnull=bool(r[3]),
            default=r[4],
            pk=bool(r[5]),
        ))
    return out


def _table_indexes(conn: sqlite3.Connection, table: str) -> list[str]:
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?"
        " AND name NOT LIKE 'sqlite_%'",
        (table,),
    ).fetchall()
    return [r[0] for r in rows]


def diff_schema(conn: sqlite3.Connection, schema: type[Schema]) -> SchemaDiff:
    """Compare a schema declaration against live sqlite_master."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (schema.table_name,),
    ).fetchone()
    if not exists:
        return SchemaDiff(
            schema_table=schema.table_name,
            missing_columns=list(schema.all_fields()),
            missing_indexes=[idx.name for idx in schema.all_indexes()],
            extra_columns=[],
            extra_indexes=[],
            type_mismatches=[],
            table_missing=True,
        )

    live_cols = {c.name: c for c in _table_info(conn, schema.table_name)}
    live_idx = set(_table_indexes(conn, schema.table_name))

    decl_cols = {f.name: f for f in schema.all_fields()}
    decl_idx = {idx.name for idx in schema.all_indexes()}

    missing_columns = [decl_cols[n] for n in decl_cols if n not in live_cols]
    extra_columns = [n for n in live_cols if n not in decl_cols]
    missing_indexes = list(decl_idx - live_idx)
    extra_indexes = list(live_idx - decl_idx)

    type_mismatches: list[tuple[str, str, str]] = []
    for name, decl in decl_cols.items():
        live = live_cols.get(name)
        if live is None:
            continue
        if live.sql_type != decl.sql_type.upper():
            type_mismatches.append((name, decl.sql_type, live.sql_type))

    return SchemaDiff(
        schema_table=schema.table_name,
        missing_columns=missing_columns,
        missing_indexes=missing_indexes,
        extra_columns=extra_columns,
        extra_indexes=extra_indexes,
        type_mismatches=type_mismatches,
    )


def emit_additive_migration(diff: SchemaDiff, schema: type[Schema]) -> list[str]:
    """Generate the non-destructive ALTER TABLE statements for a diff."""
    out: list[str] = []
    for col in diff.missing_columns:
        out.append(f"ALTER TABLE {diff.schema_table} ADD COLUMN {col.column_ddl()}")
    decl_idx_by_name = {idx.name: idx for idx in schema.all_indexes()}
    for idx_name in diff.missing_indexes:
        idx = decl_idx_by_name.get(idx_name)
        if idx is None:
            continue
        out.append(idx.index_ddl(diff.schema_table))
    return out


__all__ = [
    "ColumnInfo",
    "SchemaDiff",
    "diff_schema",
    "emit_additive_migration",
]
