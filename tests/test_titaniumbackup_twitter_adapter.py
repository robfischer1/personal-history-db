"""Tests for the TitaniumBackup Twitter adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.titaniumbackup_twitter import TitaniumBackupTwitterAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_TAR = (
    Path(__file__).parent / "fixtures" / "titaniumbackup_twitter" / "test_twitter.tar.gz"
)

OWNER_USER_ID = 99999


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


def _make_adapter() -> TitaniumBackupTwitterAdapter:
    adapter = TitaniumBackupTwitterAdapter()
    adapter.owner_user_id = OWNER_USER_ID
    adapter.db_filename = "72437370.db"
    return adapter


class TestTitaniumBackupTwitterIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        """3 statuses + 2 stories + 2 DMs = 7 rows."""
        db_path, settings = _setup(tmp_path)
        adapter = _make_adapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_TAR, conn, settings)
        assert report.rows_inserted == 7

    def test_schema_types(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _make_adapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            types = {
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT schema_type FROM messages"
                ).fetchall()
            }
        assert "SocialMediaPosting" in types
        assert "Message" in types

    def test_direction_inference(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _make_adapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            outbound = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE direction = 'outbound'"
            ).fetchone()[0]
            inbound = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE direction = 'inbound'"
            ).fetchone()[0]
        assert outbound >= 2
        assert inbound >= 2

    def test_three_threads(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _make_adapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        assert threads == 3

    def test_stories_are_bulk(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _make_adapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            bulk = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE is_bulk = 1"
            ).fetchone()[0]
        assert bulk == 2

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _make_adapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
        with connect(db_path) as conn:
            r2 = _make_adapter().run(FIXTURE_TAR, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _make_adapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_TAR, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted

    def test_story_body_has_content(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = _make_adapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            stories = conn.execute(
                "SELECT body_text FROM messages WHERE body_text_source = 'twitter-android-story'"
            ).fetchall()
        assert len(stories) == 2
        assert all(len(s[0]) > 0 for s in stories)
