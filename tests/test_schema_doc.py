"""Phase 6 schema_doc tests — DB_SCHEMA.md regeneration.

Validates the regenerator output structure, the live-vs-declared diff,
and that the post-ingest hook produces a usable artifact.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from phdb.schemas import all_schemas
from phdb.schemas.canonical import BookmarkAction, WebPage
from phdb.schemas.ddl import apply_schema
from phdb.tools.schema_doc import (
    DEFAULT_OUTPUT_PATH,
    diff_against_live,
    regenerate,
    write_to_file,
)


def _fresh_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    conn.execute(
        "CREATE TABLE source_files (id INTEGER PRIMARY KEY, source_org TEXT,"
        " file_kind TEXT, message_count INTEGER)"
    )
    return conn


def test_regenerate_without_conn_produces_structural_doc():
    content = regenerate(None)
    assert "# DB_SCHEMA.md" in content
    assert "## Entity tables" in content
    assert "## Action / document tables" in content
    assert "## Facet plugins" in content
    assert "## Column detail" in content
    # Schema metadata
    assert "`web_pages`" in content
    assert "`bookmarks`" in content
    assert "BookmarkAction" in content
    # Facet plugins from Phase 4
    assert "`people`" in content
    assert "`places`" in content


def test_regenerate_with_conn_includes_row_counts():
    conn = _fresh_db()
    apply_schema(conn, WebPage)
    apply_schema(conn, BookmarkAction)
    # Insert a couple of rows
    conn.execute(
        "INSERT INTO web_pages (url, normalized_url, source_file_id)"
        " VALUES ('http://x.example.com/', 'x.example.com', NULL)"
    )
    conn.execute(
        "INSERT INTO web_pages (url, normalized_url, source_file_id)"
        " VALUES ('http://y.example.com/', 'y.example.com', NULL)"
    )
    conn.commit()
    content = regenerate(conn)
    # Row counts render (2 web_pages, 0 bookmarks)
    assert "2" in content


def test_diff_against_live_reports_missing_table():
    conn = _fresh_db()
    drift = diff_against_live(conn)
    # All declared schemas are missing from this empty DB
    assert drift, "expected drift output for a DB missing all tables"
    assert any("web_pages" in line for line in drift)


def test_diff_against_live_is_clean_after_applying_schemas():
    conn = _fresh_db()
    for schema in all_schemas():
        apply_schema(conn, schema)
    drift = diff_against_live(conn)
    # If the schemas match the declarations exactly, drift is empty.
    # Any non-empty drift is a regression in either schemas or the comparator.
    assert drift == [], f"unexpected drift after applying all schemas: {drift[:10]}"


def test_write_to_file_round_trip(tmp_path: Path):
    target = tmp_path / "DB_SCHEMA.md"
    written = write_to_file(target, None)
    assert written == target
    assert target.exists()
    text = target.read_text(encoding="utf-8")
    assert "# DB_SCHEMA.md" in text


def test_default_output_path_is_repo_root_relative():
    """DEFAULT_OUTPUT_PATH targets DB_SCHEMA.md at cwd."""
    assert str(DEFAULT_OUTPUT_PATH) == "DB_SCHEMA.md"


def test_regenerated_doc_includes_raindrop_plugin_attribution():
    """The raindrop source plugin's emits = ['BookmarkAction'] surfaces in the doc."""
    content = regenerate(None)
    # The bookmarks row should attribute to the raindrop plugin
    assert "raindrop" in content
