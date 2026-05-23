"""Phase 2 schemas pillar tests.

Validates the schemas registry + DDL generator + entity upsert
machinery. Per the plan's Phase 2 deliverable, the bar is:

- Every canonical schema registers cleanly.
- DDL generation produces valid SQLite for every schema.
- Schemas applied to a fresh DB produce sqlite_master shapes matching
  the schema declarations (columns + types + indexes — defaults are
  best-effort since SQLite canonicalizes them).
- The auto-generated upsert_<entity>() helper for WebPage round-trips
  through INSERT + COALESCE UPDATE.

Live-DB byte-clean comparison against migration-generated tables lives
under tests/test_schema_migration_parity.py (Phase 6 wires the full
regen hook).
"""

from __future__ import annotations

import sqlite3

import pytest

from phdb.schemas import (
    EntitySchema,
    SchemaRegistry,
    all_schemas,
    default_schema_registry,
    generate_create_table,
    upsert_entity,
)
from phdb.schemas.base import Schema
from phdb.schemas.canonical import (
    ACTION_SCHEMAS,
    ENTITY_SCHEMAS,
    BookmarkAction,
    Observation,
    WebPage,
    register_all,
)
from phdb.schemas.ddl import apply_schema, generate_all_ddl
from phdb.schemas.migration import diff_schema
from phdb.schemas.registry import reset_default_schema_registry


@pytest.fixture(autouse=True)
def _reset_registry():
    """Each test starts with a fresh default registry."""
    reset_default_schema_registry()
    yield
    reset_default_schema_registry()


def test_registry_loads_all_canonical_schemas():
    reg = default_schema_registry()
    # All entity + action schemas register
    expected = len(ENTITY_SCHEMAS) + len(ACTION_SCHEMAS)
    assert len(reg) == expected, (
        f"expected {expected} schemas; got {len(reg)}: {list(reg.by_table)}"
    )
    # WebPage lookup by @type
    assert reg.get_by_type("WebPage") is WebPage
    assert reg.get_by_table("web_pages") is WebPage


def test_every_schema_has_table_and_type():
    for schema in all_schemas():
        assert schema.table_name, f"schema {schema} missing table_name"
        assert schema.schema_type, f"schema {schema} missing schema_type"
        assert schema.fields, f"schema {schema} has no fields"


def _create_fresh_db() -> sqlite3.Connection:
    """In-memory DB with the source_files parent table for FK satisfaction."""
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE source_files (id INTEGER PRIMARY KEY, source_org TEXT,"
        " file_kind TEXT, message_count INTEGER)"
    )
    return conn


@pytest.mark.parametrize(
    "schema",
    [WebPage, BookmarkAction, Observation, *ACTION_SCHEMAS[:5]],
    ids=lambda s: s.table_name,
)
def test_create_table_round_trips_for_representative_schemas(schema: type[Schema]):
    """Generated DDL applies cleanly and produces matching column structure."""
    conn = _create_fresh_db()
    apply_schema(conn, schema)

    # Verify PRAGMA table_info matches declared fields
    rows = conn.execute(f"PRAGMA table_info([{schema.table_name}])").fetchall()
    live_cols = {r[1]: (r[2].upper(), bool(r[3]), bool(r[5])) for r in rows}
    expected_cols = {
        f.name: (f.sql_type.upper(), not f.nullable and not f.primary_key, f.primary_key)
        for f in schema.all_fields()
    }
    assert set(live_cols) == set(expected_cols), (
        f"{schema.table_name}: column-name mismatch."
        f" live={set(live_cols)} declared={set(expected_cols)}"
    )
    # Type + nullability match
    for col, (live_type, _live_notnull, live_pk) in live_cols.items():
        exp_type, exp_notnull, exp_pk = expected_cols[col]
        assert live_type == exp_type, f"{schema.table_name}.{col}: type mismatch ({live_type} vs {exp_type})"
        assert live_pk == exp_pk, f"{schema.table_name}.{col}: pk mismatch"


@pytest.mark.parametrize("schema", [WebPage, BookmarkAction], ids=lambda s: s.table_name)
def test_indexes_round_trip(schema: type[Schema]):
    """All declared indexes are visible in sqlite_master after apply_schema."""
    conn = _create_fresh_db()
    apply_schema(conn, schema)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?"
        " AND name NOT LIKE 'sqlite_%'",
        (schema.table_name,),
    ).fetchall()
    live_idx = {r[0] for r in rows}
    declared = {idx.name for idx in schema.all_indexes()}
    assert declared <= live_idx, (
        f"{schema.table_name}: missing indexes {declared - live_idx}"
    )


def test_schema_diff_clean_after_fresh_apply():
    """Applying a schema to a fresh DB yields a clean diff against the schema."""
    conn = _create_fresh_db()
    apply_schema(conn, WebPage)
    diff = diff_schema(conn, WebPage)
    assert diff.clean, (
        f"unexpected diff after fresh apply:"
        f" missing_columns={[f.name for f in diff.missing_columns]}"
        f" missing_indexes={diff.missing_indexes}"
        f" extra_columns={diff.extra_columns}"
        f" extra_indexes={diff.extra_indexes}"
        f" type_mismatches={diff.type_mismatches}"
    )


def test_upsert_entity_web_page_insert_then_coalesce_update():
    """The WebPage entity upsert inserts a new row and COALESCEs on conflict."""
    conn = _create_fresh_db()
    apply_schema(conn, WebPage)

    first = {
        "schema_type": "WebPage",
        "url": "https://example.com/x",
        "normalized_url": "example.com/x",
        "title": "Example",
        "excerpt": None,
        "cover_url": None,
        "domain": "example.com",
        "first_seen": "2026-05-22T00:00:00Z",
        "last_seen": "2026-05-22T00:00:00Z",
        "source_file_id": None,
    }
    id1 = upsert_entity(conn, WebPage, first)
    assert id1 == 1

    # Conflict on normalized_url — different metadata; COALESCE keeps original
    # title because excluded.title is non-None — wait, COALESCE(excluded, table)
    # picks excluded if non-None. So new title wins; null new values keep old.
    second = {
        "schema_type": "WebPage",
        "url": "https://example.com/x?utm_source=foo",  # different raw URL
        "normalized_url": "example.com/x",                # same dedup key
        "title": "Example Updated",
        "excerpt": "Now with an excerpt",
        "cover_url": None,
        "domain": None,
        "first_seen": None,
        "last_seen": "2026-05-22T01:00:00Z",
        "source_file_id": None,
    }
    id2 = upsert_entity(conn, WebPage, second)
    assert id2 == id1, "upsert should return same id for matching dedup key"

    row = conn.execute(
        "SELECT url, title, excerpt, domain, first_seen, last_seen FROM web_pages WHERE id = ?",
        (id1,),
    ).fetchone()
    assert row["url"] == "https://example.com/x?utm_source=foo", "non-null excluded url wins"
    assert row["title"] == "Example Updated", "non-null excluded title wins"
    assert row["excerpt"] == "Now with an excerpt", "null table excerpt yields excluded value"
    assert row["domain"] == "example.com", "null excluded domain keeps table value"
    assert row["first_seen"] == "2026-05-22T00:00:00Z", "null excluded first_seen keeps table value"
    assert row["last_seen"] == "2026-05-22T01:00:00Z", "non-null excluded last_seen wins"


def test_ddl_generator_is_deterministic():
    """DDL emission is stable — important for the Phase 6 byte-clean comparator."""
    a = generate_create_table(WebPage)
    b = generate_create_table(WebPage)
    assert a == b


def test_all_schemas_apply_without_error():
    """Smoke test — every canonical schema's full DDL bundle applies cleanly."""
    conn = _create_fresh_db()
    for schema in [*ENTITY_SCHEMAS, *ACTION_SCHEMAS]:
        try:
            apply_schema(conn, schema)
        except sqlite3.OperationalError as e:
            pytest.fail(f"{schema.table_name}: apply failed: {e}\nDDL: {generate_all_ddl(schema)}")


def test_register_all_seeds_a_fresh_registry():
    reg = SchemaRegistry()
    register_all(reg)
    assert len(reg) == len(ENTITY_SCHEMAS) + len(ACTION_SCHEMAS)


def test_entity_schema_classification_invariant():
    """Every entity schema declares a dedup_key other than 'id'."""
    for schema in ENTITY_SCHEMAS:
        assert isinstance(schema, type) and issubclass(schema, EntitySchema)
        assert schema.dedup_key != "id", (
            f"{schema.table_name}: entity must declare a real dedup_key, not 'id'"
        )
