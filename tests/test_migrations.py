"""Tests for the migration runner."""

from __future__ import annotations

from pathlib import Path

from phdb.db import connect
from phdb.migrations.runner import Migration, MigrationRunner


def test_discover_finds_project_migrations() -> None:
    runner_mod = __import__("phdb.migrations.runner", fromlist=["_default_project_dir"])
    project_dir = runner_mod._default_project_dir()
    assert project_dir.is_dir()

    # Use a dummy connection (we only need discover, not DB access)
    import sqlite3

    conn = sqlite3.connect(":memory:")
    runner = MigrationRunner(conn)
    migrations = runner.discover()
    conn.close()

    assert len(migrations) >= 30
    assert migrations[0].migration_id == "0001_init"
    # Note: don't assert the last migration_id — that's a parallel-session
    # gridlock trap. discover() correctness is covered by the count floor +
    # first-element check; identity of the tail migrates with every new file.


def test_apply_all_to_fresh_db(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        applied = runner.apply_pending()

    assert len(applied) >= 14
    assert applied[0] == "0001_init"

    with connect(db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "messages" not in tables  # dropped by 0022
        assert "source_files" in tables
        assert "chunks" in tables
        assert "documents" in tables
        assert "bookmarks" in tables
        assert "connections" in tables
        assert "geo_traces" in tables
        assert "nodes" in tables
        assert "predicates" in tables
        assert "triples" in tables
        assert "qualifiers" in tables
        assert "articles" in tables
        # Typed tables created by 0019-0021
        assert "emails" in tables
        assert "chat_messages" in tables
        assert "actions" in tables
        assert "web_pages" in tables
        assert "digital_documents" in tables
        assert "observations" in tables


def test_skip_already_applied(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        first_run = runner.apply_pending()
        second_run = runner.apply_pending()

    assert len(first_run) >= 22
    # 0022_drop_messages is idempotent (DROP TABLE IF EXISTS) but does not
    # self-register in schema_migrations, so it re-runs on second pass.
    unregistered = {"0022_drop_messages"}
    assert set(second_run) <= unregistered


def test_compat_with_legacy_3digit_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()

        # Simulate a legacy DB that used 3-digit IDs
        conn.execute("INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('001_init')")
        conn.commit()

        applied_ids = runner.applied()
        assert "0001_init" in applied_ids or "001_init" in applied_ids

        # The compat check should recognize 0001_init as applied via 001_init
        assert runner._is_applied("0001_init", applied_ids)


def test_status_shows_all_migrations(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
        status = runner.status()

    assert len(status) >= 22
    # 0022_drop_messages does not self-register in schema_migrations
    unregistered = {"0022_drop_messages"}
    for mid, is_applied in status:
        if mid not in unregistered:
            assert is_applied, f"{mid} should be applied"


def test_migration_from_path() -> None:
    m = Migration.from_path(Path("0001_init.sql"))
    assert m is not None
    assert m.number == 1
    assert m.migration_id == "0001_init"

    m2 = Migration.from_path(Path("not_a_migration.sql"))
    assert m2 is None


def test_instance_migrations(tmp_path: Path) -> None:
    instance_dir = tmp_path / "instance_migrations"
    instance_dir.mkdir()

    (instance_dir / "1000_custom.sql").write_text(
        "CREATE TABLE IF NOT EXISTS custom_table (id INTEGER PRIMARY KEY);\n"
        "INSERT OR IGNORE INTO schema_migrations(migration_id) VALUES ('1000_custom');\n",
        encoding="utf-8",
    )

    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn, instance_dir=instance_dir)
        applied = runner.apply_pending()

    assert "1000_custom" in applied
    with connect(db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "custom_table" in tables
