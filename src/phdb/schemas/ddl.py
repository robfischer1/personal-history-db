"""DDL generator — dataclass schemas → CREATE TABLE / CREATE INDEX SQL.

Phase 2 deliverable. Emits SQLite-compatible DDL from
``Schema`` subclasses. The output is intentionally close to (but not
identical to) the hand-authored migration DDL — column order matches,
types match, defaults match, indexes match. ``sqlite_master`` will
canonicalize some whitespace; the schema-diff comparator in
``phdb.schemas.migration`` strips both sides before comparing.
"""

from __future__ import annotations

from phdb.schemas.base import IndexSpec, Schema


def generate_create_table(schema: type[Schema], *, if_not_exists: bool = True) -> str:
    """Generate the ``CREATE TABLE`` SQL for a schema.

    Column order matches the schema's ``fields`` declaration; the
    schema author is responsible for putting fields in the canonical
    order (typically ``id``, ``schema_type``, identity fields, content,
    metadata, provenance).
    """
    cols = [f.column_ddl() for f in schema.all_fields()]
    ine = "IF NOT EXISTS " if if_not_exists else ""
    indent = "    "
    body = ",\n".join(indent + c for c in cols)
    return f"CREATE TABLE {ine}{schema.table_name} (\n{body}\n)"


def generate_indexes(schema: type[Schema]) -> list[str]:
    """Generate the ``CREATE INDEX`` SQL statements for a schema."""
    return [idx.index_ddl(schema.table_name) for idx in schema.all_indexes()]


def generate_sidecar_table(schema: type[Schema]) -> list[tuple[str, list[str]]]:
    """Generate DDL for all sidecars declared on a schema.

    Returns a list of ``(create_table_sql, [index_sql, ...])`` tuples.
    """
    out: list[tuple[str, list[str]]] = []
    for sidecar in schema.sidecars:
        cols = [f.column_ddl() for f in sidecar.field_specs()]
        body = ",\n".join("    " + c for c in cols)
        create = f"CREATE TABLE IF NOT EXISTS {sidecar.table_name} (\n{body}\n)"
        idxs = [idx.index_ddl(sidecar.table_name) for idx in sidecar.indexes]
        out.append((create, idxs))
    return out


def generate_all_ddl(schema: type[Schema]) -> list[str]:
    """Generate the full DDL bundle for a schema — table, indexes, sidecars."""
    out: list[str] = [generate_create_table(schema)]
    out.extend(generate_indexes(schema))
    for sidecar_create, sidecar_idxs in generate_sidecar_table(schema):
        out.append(sidecar_create)
        out.extend(sidecar_idxs)
    return out


def apply_schema(conn: object, schema: type[Schema]) -> None:
    """Execute the full DDL bundle for ``schema`` against ``conn``.

    Convenience helper — used by tests and the Phase 6 regen hook.
    """
    for stmt in generate_all_ddl(schema):
        conn.execute(stmt)  # type: ignore[attr-defined]


# Re-exported helpers
__all__ = [
    "apply_schema",
    "generate_all_ddl",
    "generate_create_table",
    "generate_indexes",
    "generate_sidecar_table",
]
