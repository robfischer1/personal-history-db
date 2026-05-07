"""Tests for the facebook messenger adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.facebook import FacebookAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "facebook" / "facebook_test.zip"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"bob"}),
    )
    return db_path, settings


class TestFacebookIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
        assert report.rows_inserted == 3

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()
        assert all(t[0] == "Message" for t in types)

    def test_direction_inference(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            bob_dir = conn.execute(
                "SELECT direction FROM messages WHERE sender_address = 'bob'"
            ).fetchone()
        if bob_dir:
            assert bob_dir[0] == "outbound"

    def test_thread_per_conversation(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        assert threads >= 1

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
        with connect(db_path) as conn:
            r2 = FacebookAdapter().run(FIXTURE_ZIP, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted
