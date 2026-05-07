"""Tests for the amazon adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.amazon import AmazonAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "amazon" / "amazon_export.zip"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestAmazonIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AmazonAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
        assert report.rows_inserted == 3
        assert report.rows_skipped == 0

    def test_all_bulk(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AmazonAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            bulk = conn.execute("SELECT DISTINCT is_bulk FROM messages").fetchall()
        assert all(b[0] == 1 for b in bulk)

    def test_direction_self(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AmazonAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            dirs = conn.execute("SELECT DISTINCT direction FROM messages").fetchall()
        assert all(d[0] == "self" for d in dirs)

    def test_threads_created(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AmazonAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        assert threads >= 1

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AmazonAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
        with connect(db_path) as conn:
            r2 = AmazonAdapter().run(FIXTURE_ZIP, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AmazonAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted
