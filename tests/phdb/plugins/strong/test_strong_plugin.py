"""Tests for the strong (workout) plugin."""

from __future__ import annotations

from pathlib import Path

from phdb.core.plugin.manifest import load_manifest
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.strong.plugin import StrongPlugin
from phdb.settings import IdentitySettings, Settings

FIXTURE_DB = Path(__file__).parent.parent.parent.parent / "fixtures" / "strong" / "Strong4.sqlite"


def _new_plugin() -> StrongPlugin:
    manifest_path = Path("src/phdb/plugins/strong/plugin.toml").resolve()
    manifest = load_manifest(manifest_path)
    return StrongPlugin(manifest)


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestStrongPluginIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_DB, conn, settings)
        assert report.rows_inserted == 2
        assert report.rows_skipped == 0

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_DB, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM exercise_actions").fetchall()
        assert all(t[0] == "ExerciseAction" for t in types)

    def test_direction_self(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_DB, conn, settings)
            dirs = conn.execute("SELECT DISTINCT direction FROM exercise_actions").fetchall()
        assert all(d[0] == "self" for d in dirs)

    def test_single_thread(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_DB, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]
        assert threads == 1

    def test_body_contains_exercises(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_DB, conn, settings)
            bodies = conn.execute("SELECT body_text FROM exercise_actions ORDER BY date_performed").fetchall()
        assert "Bench Press" in bodies[0][0]
        assert "Squat" in bodies[0][0]

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_DB, conn, settings)
        with connect(db_path) as conn:
            r2 = _new_plugin().run(FIXTURE_DB, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == 2

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_DB, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
        assert bridge == report.rows_inserted
