"""Entity upsert generator — auto-generates ``upsert_<entity>()`` helpers.

Per the WebPage Entity Factoring precedent (raindrop adapter,
2026-05-22): every entity table needs an ``upsert_<entity>()`` helper
that takes the dedup_key + a kwargs dict, INSERTs a new row if the
dedup_key is unseen, and updates existing rows using COALESCE
last-write-wins on metadata fields.

This module generates that helper at registration time from any
``EntitySchema`` so plugins don't have to author per-entity boilerplate.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from phdb.schemas.base import EntitySchema, FieldSpec


_NON_COALESCE_FIELDS: frozenset[str] = frozenset({
    "id", "created_at", "schema_type",
})


def _resolve_coalesce_fields(schema: type[EntitySchema]) -> list[str]:
    """Pick which columns get COALESCE last-write-wins on upsert.

    If the schema declares ``coalesce_fields``, that list is used
    verbatim. Otherwise: every column except id / created_at /
    schema_type / dedup_key.
    """
    if schema.coalesce_fields:
        return list(schema.coalesce_fields)
    out: list[str] = []
    for f in schema.all_fields():
        if f.name in _NON_COALESCE_FIELDS:
            continue
        if f.name == schema.dedup_key:
            continue
        out.append(f.name)
    return out


def build_upsert_sql(schema: type[EntitySchema]) -> str:
    """Build the parameterized ON CONFLICT upsert SQL for an entity table.

    The SQL uses the dedup_key as the conflict target and COALESCE last-
    write-wins for the columns listed in ``_resolve_coalesce_fields``.
    Static SQL — schema is fixed at module load.
    """
    insert_columns = [f.name for f in schema.all_fields() if f.name != "id" and f.name != "created_at"]
    placeholders = ", ".join("?" * len(insert_columns))
    column_list = ", ".join(insert_columns)

    coalesce_cols = _resolve_coalesce_fields(schema)
    set_clauses = ",\n  ".join(
        f"{col} = COALESCE(excluded.{col}, {schema.table_name}.{col})"
        for col in coalesce_cols
    )
    if not set_clauses:
        set_clauses = f"{schema.dedup_key} = excluded.{schema.dedup_key}"

    return (
        f"INSERT INTO {schema.table_name} ({column_list})\n"
        f"VALUES ({placeholders})\n"
        f"ON CONFLICT({schema.dedup_key}) DO UPDATE SET\n"
        f"  {set_clauses}\n"
        f"RETURNING id"
    )


def upsert_entity(
    conn: sqlite3.Connection,
    schema: type[EntitySchema],
    values: dict[str, Any],
) -> int:
    """Insert or COALESCE-update an entity row; return the entity id.

    ``values`` is a dict keyed on column names. Missing columns get None.
    The schema's ``dedup_key`` column must be present and non-None.
    """
    if schema.dedup_key not in values:
        raise ValueError(
            f"upsert_entity for {schema.table_name} requires {schema.dedup_key} in values"
        )

    sql = build_upsert_sql(schema)
    insert_columns = [f.name for f in schema.all_fields() if f.name != "id" and f.name != "created_at"]
    row = tuple(values.get(col) for col in insert_columns)
    cur = conn.execute(sql, row)
    result = cur.fetchone()
    if result is None:
        # ON CONFLICT DO UPDATE always RETURNING but defend anyway
        existing = conn.execute(
            f"SELECT id FROM {schema.table_name} WHERE {schema.dedup_key} = ?",
            (values[schema.dedup_key],),
        ).fetchone()
        if existing is None:
            raise RuntimeError(f"upsert_entity({schema.table_name}) returned no row")
        return int(existing[0])
    return int(result[0])


__all__ = ["build_upsert_sql", "upsert_entity"]
