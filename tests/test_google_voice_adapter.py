"""Tests for the google_voice adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.google_voice import GoogleVoiceAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "google_voice"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestGoogleVoiceIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleVoiceAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
        assert report.rows_inserted == 3

    def test_schema_types(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleVoiceAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            types = {t[0] for t in conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()}
        assert "Message" in types

    def test_threads_per_phone(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleVoiceAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        assert threads >= 1

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleVoiceAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
        with connect(db_path) as conn:
            r2 = GoogleVoiceAdapter().run(FIXTURE_DIR, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = GoogleVoiceAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted
