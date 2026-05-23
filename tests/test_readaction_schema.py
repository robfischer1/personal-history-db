"""Phase 7 WPEF follow-on tests — ReadAction schema + readaction stub plugin.

Brief 102 ships the ReadAction schema (third entity-FK consumer after
BookmarkAction + BrowseAction) plus the stub plugin that awaits a
Pocket / Instapaper format parser. These tests cover:

- Schema applies cleanly to a fresh DB; PRAGMA table_info matches.
- Indexes round-trip.
- Schema is reachable through the canonical registry.
- Stub plugin discovers via the in-tree loader.
- Manifest validates against the schemas registry (zero issues).
- ``parse()`` raises NotImplementedError with the documented message.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from phdb.core.plugin.loader import discover_plugins, load_plugin
from phdb.plugins.readaction import ReadActionPlugin
from phdb.schemas import default_schema_registry
from phdb.schemas.canonical import ACTION_SCHEMAS, ReadAction
from phdb.schemas.ddl import apply_schema
from phdb.schemas.migration import diff_schema
from phdb.schemas.registry import reset_default_schema_registry


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_default_schema_registry()
    yield
    reset_default_schema_registry()


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE source_files (id INTEGER PRIMARY KEY, source_org TEXT,"
        " file_kind TEXT, message_count INTEGER)"
    )
    conn.execute(
        "CREATE TABLE web_pages (id INTEGER PRIMARY KEY, schema_type TEXT,"
        " url TEXT, normalized_url TEXT)"
    )
    return conn


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_read_action_in_action_schemas_registry():
    assert ReadAction in ACTION_SCHEMAS
    reg = default_schema_registry()
    assert reg.get_by_type("ReadAction") is ReadAction
    assert reg.get_by_table("read_actions") is ReadAction


def test_read_action_schema_metadata():
    assert ReadAction.table_name == "read_actions"
    assert ReadAction.schema_type == "ReadAction"
    assert ReadAction.date_column == "date_read"
    # Entity-FK pattern: ReadAction is the third consumer (after
    # BookmarkAction + BrowseAction)
    assert len(ReadAction.entity_refs) == 1
    assert ReadAction.entity_refs[0].entity_table == "web_pages"
    assert ReadAction.entity_refs[0].column_name == "web_page_id"


def test_read_action_ddl_applies_cleanly():
    conn = _fresh_db()
    apply_schema(conn, ReadAction)

    rows = conn.execute("PRAGMA table_info([read_actions])").fetchall()
    live_cols = {r[1] for r in rows}
    expected_cols = {f.name for f in ReadAction.all_fields()}
    assert live_cols == expected_cols

    # Required fields per the brief
    for required in (
        "id", "schema_type", "web_page_id", "date_read", "direction",
        "body_text", "body_text_source", "source_file_id", "raw_hash",
        "created_at",
    ):
        assert required in live_cols, f"missing required column {required}"


def test_read_action_indexes_round_trip():
    conn = _fresh_db()
    apply_schema(conn, ReadAction)
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name=?"
        " AND name NOT LIKE 'sqlite_%'",
        ("read_actions",),
    ).fetchall()
    live = {r[0] for r in rows}
    assert "idx_read_actions_dedup" in live
    assert "idx_read_actions_web_page_id" in live


def test_read_action_dedup_index_is_unique():
    conn = _fresh_db()
    apply_schema(conn, ReadAction)
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?",
        ("idx_read_actions_dedup",),
    ).fetchone()
    assert row is not None
    assert "UNIQUE" in row[0].upper()


def test_read_action_schema_diff_clean_after_fresh_apply():
    conn = _fresh_db()
    apply_schema(conn, ReadAction)
    diff = diff_schema(conn, ReadAction)
    assert diff.clean, (
        f"unexpected diff after fresh apply:"
        f" missing_columns={[f.name for f in diff.missing_columns]}"
        f" missing_indexes={diff.missing_indexes}"
        f" extra_columns={diff.extra_columns}"
        f" extra_indexes={diff.extra_indexes}"
        f" type_mismatches={diff.type_mismatches}"
    )


# ---------------------------------------------------------------------------
# Plugin
# ---------------------------------------------------------------------------


def test_readaction_plugin_discovered_via_in_tree_loader():
    descriptors = discover_plugins()
    names = {d.name for d in descriptors}
    assert "readaction" in names, (
        f"expected readaction plugin in discovery; got {sorted(names)}"
    )


def test_readaction_plugin_manifest_validates_clean():
    descriptors = discover_plugins()
    readaction = next(d for d in descriptors if d.name == "readaction")
    assert readaction.manifest.kind == "source"
    assert readaction.manifest.source is not None
    assert readaction.manifest.source.emits == ["ReadAction"]
    assert readaction.manifest.source.entity_refs == ["web_pages"]
    # Schema validates against the registry — zero issues
    assert readaction.issues == [], (
        f"unexpected manifest issues: {readaction.issues}"
    )


def test_readaction_plugin_loads_and_instantiates():
    descriptors = discover_plugins()
    readaction = next(d for d in descriptors if d.name == "readaction")
    plugin = load_plugin(readaction)
    assert isinstance(plugin, ReadActionPlugin)
    assert plugin.name == "readaction"
    assert plugin.kind == "source"


def test_readaction_plugin_parse_raises_not_implemented():
    descriptors = discover_plugins()
    readaction = next(d for d in descriptors if d.name == "readaction")
    plugin = load_plugin(readaction)
    with pytest.raises(NotImplementedError, match="No Pocket/Instapaper format parser yet"):
        list(plugin.parse(Path("nonexistent.json")))


def test_readaction_plugin_discover_yields_nothing():
    descriptors = discover_plugins()
    readaction = next(d for d in descriptors if d.name == "readaction")
    plugin = load_plugin(readaction)
    # Empty iterator — no format parser means nothing to discover
    assert list(plugin.discover(Path("."))) == []


def test_readaction_plugin_register_methods_are_noops():
    descriptors = discover_plugins()
    readaction = next(d for d in descriptors if d.name == "readaction")
    plugin = load_plugin(readaction)
    # No-op contract: return None, don't raise
    assert plugin.register_cli(object()) is None
    assert plugin.register_tools(object()) is None
