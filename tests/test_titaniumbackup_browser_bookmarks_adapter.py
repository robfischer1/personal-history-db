"""Tests for the titaniumbackup_browser_bookmarks adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.titaniumbackup_browser_bookmarks import (
    INSTRUMENT_BOOKMARK,
    TitaniumBackupBrowserBookmarksAdapter,
)
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_TAR = (
    Path(__file__).parent
    / "fixtures"
    / "titaniumbackup_browser_bookmarks"
    / "com.android.browser-test.tar.gz"
)


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestTitaniumBackupBrowserIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = TitaniumBackupBrowserBookmarksAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_TAR, conn, settings)
        assert report.rows_yielded == 3
        # reddit.com/ is junk -> skipped
        assert report.rows_inserted == 2
        assert report.rows_skipped == 1

    def test_bookmark_count(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = TitaniumBackupBrowserBookmarksAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
        assert count == 2

    def test_instrument_values(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = TitaniumBackupBrowserBookmarksAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            instruments = conn.execute(
                "SELECT DISTINCT instrument FROM bookmarks ORDER BY instrument"
            ).fetchall()
        instrument_set = {r[0] for r in instruments}
        # Only bookmarks survive (history entry is reddit = junk)
        assert INSTRUMENT_BOOKMARK in instrument_set

    def test_junk_urls_skipped(self, tmp_path: Path) -> None:
        """reddit.com/ root is a junk URL and should be skipped entirely."""
        db_path, settings = _setup(tmp_path)
        adapter = TitaniumBackupBrowserBookmarksAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            reddit = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE url LIKE '%reddit%'"
            ).fetchone()[0]
        assert reddit == 0

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = TitaniumBackupBrowserBookmarksAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
        with connect(db_path) as conn:
            r2 = TitaniumBackupBrowserBookmarksAdapter().run(FIXTURE_TAR, conn, settings)
        # INSERT OR IGNORE -> all duplicates skipped
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_url_normalization(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = TitaniumBackupBrowserBookmarksAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_TAR, conn, settings)
            row = conn.execute(
                "SELECT normalized_url FROM bookmarks WHERE title='Python Docs'"
            ).fetchone()
        assert row is not None
        assert row[0] == "https://docs.python.org/3"
