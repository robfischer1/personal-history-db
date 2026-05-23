"""Tests for the google_activity adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.plugins.google_activity import GoogleActivityPlugin
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings
from phdb.core.plugin.manifest import load_manifest

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "google_activity"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


def _new_plugin() -> GoogleActivityPlugin:
    manifest_path = Path("src/phdb/plugins/google_activity/plugin.toml").resolve()
    manifest = load_manifest(manifest_path)
    return GoogleActivityPlugin(manifest)


class TestGoogleActivityIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_DIR, conn, settings)
        assert report.rows_inserted >= 2
        assert report.rows_skipped == 0

    def test_all_bulk(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_DIR, conn, settings)
            bulk = conn.execute(
                "SELECT DISTINCT is_bulk FROM search_actions"
                " UNION SELECT DISTINCT is_bulk FROM watch_actions"
                " UNION SELECT DISTINCT is_bulk FROM actions"
            ).fetchall()
        assert all(b[0] == 1 for b in bulk)

    def test_threads_per_stream(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_DIR, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]
        assert threads >= 1

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_DIR, conn, settings)
        with connect(db_path) as conn:
            r2 = _new_plugin().run(FIXTURE_DIR, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_DIR, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
        assert bridge == report.rows_inserted

    def test_web_page_fk(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_DIR, conn, settings)
            searches = conn.execute("SELECT web_page_id FROM search_actions WHERE web_page_id IS NOT NULL").fetchall()
            watches = conn.execute("SELECT web_page_id FROM watch_actions WHERE web_page_id IS NOT NULL").fetchall()
            
        # The fixtures should have at least one of each with a URL
        assert len(searches) + len(watches) >= 1
