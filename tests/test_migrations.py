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

    assert len(migrations) == 13
    assert migrations[0].migration_id == "0001_init"
    assert migrations[-1].migration_id == "0013_articles_table"


def test_apply_all_to_fresh_db(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        runner = MigrationRunner(conn)
        applied = runner.apply_pending()

    assert len(applied) == 13
    assert applied[0] == "0001_init"

    with connect(db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "messages" in tables
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


def test_skip_already_applied(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        runner = MigrationRunner(conn)
        first_run = runner.apply_pending()
        second_run = runner.apply_pending()

    assert len(first_run) == 13
    assert len(second_run) == 0


def test_compat_with_legacy_3digit_ids(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
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
    with connect(db_path) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
        status = runner.status()

    assert len(status) == 13
    assert all(is_applied for _, is_applied in status)


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
    with connect(db_path) as conn:
        runner = MigrationRunner(conn, instance_dir=instance_dir)
        applied = runner.apply_pending()

    assert "1000_custom" in applied
    with connect(db_path) as conn:
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "custom_table" in tables
