"""Tests for facebook posts via the unified adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.facebook_unified import FacebookUnifiedAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "facebook_posts" / "facebook_posts_test.zip"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestFacebookPostsIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookUnifiedAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
        assert report.rows_inserted == 2

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookUnifiedAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()
        assert all(t[0] == "SocialMediaPosting" for t in types)

    def test_direction_outbound(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookUnifiedAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            dirs = conn.execute("SELECT DISTINCT direction FROM messages").fetchall()
        assert all(d[0] == "outbound" for d in dirs)

    def test_thread_per_bucket(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookUnifiedAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            keys = conn.execute("SELECT thread_key FROM threads").fetchall()
        assert any("Posts" in k[0] for k in keys)

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookUnifiedAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
        with connect(db_path) as conn:
            r2 = FacebookUnifiedAdapter().run(FIXTURE_ZIP, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookUnifiedAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted
