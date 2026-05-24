"""Tests for the chrome_history plugin."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.chrome_history.plugin import (
    BrowserHistoryRecord,
    ChromeHistoryPlugin,
    parse_chrome_history,
)

FIXTURE_PATH = Path(__file__).parent.parent / "fixtures" / "chrome_history_sample.json"


def _new_plugin() -> ChromeHistoryPlugin:
    """Instantiate the plugin without loading its manifest from disk."""
    return ChromeHistoryPlugin(manifest=None)


def _setup(tmp_path: Path) -> Path:
    """Create a migrated test DB and return its path."""
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    return db_path


class TestParseChomeHistory:
    """Test the JSON parser / record yielder."""

    def test_parse_yields_records(self) -> None:
        records = list(parse_chrome_history(FIXTURE_PATH))
        # Fixture has 5 entries, all with non-empty URLs
        assert len(records) == 5

    def test_record_fields(self) -> None:
        records = list(parse_chrome_history(FIXTURE_PATH))
        first = records[0]
        assert isinstance(first, BrowserHistoryRecord)
        assert first.url == "https://www.google.com/"
        assert first.title == "Google"
        assert first.page_transition == "FROM_ADDRESS_BAR"
        assert first.profile == "inSOtfpy5kBsXGLBRTqRQQ=="
        assert first.raw_hash  # non-empty

    def test_empty_title_parsed(self) -> None:
        records = list(parse_chrome_history(FIXTURE_PATH))
        # Entry 4 has an empty string title
        empty_title_rec = [r for r in records if r.url == "https://example.com/empty-title"]
        assert len(empty_title_rec) == 1
        assert empty_title_rec[0].title == ""

    def test_empty_url_skipped(self, tmp_path: Path) -> None:
        """Entries with empty URL are skipped."""
        data = {
            "Browser History": [
                {"url": "", "time_usec": 1000000000000000, "title": "empty"},
                {"url": "https://example.com", "time_usec": 1000000000000000, "title": "valid"},
            ]
        }
        fixture = tmp_path / "empty_url.json"
        fixture.write_text(json.dumps(data), encoding="utf-8")
        records = list(parse_chrome_history(fixture))
        assert len(records) == 1
        assert records[0].url == "https://example.com"


class TestTimeConversion:
    """Test time_usec to epoch seconds conversion."""

    def test_microseconds_to_seconds(self) -> None:
        records = list(parse_chrome_history(FIXTURE_PATH))
        first = records[0]
        # 1773000330606420 usec / 1_000_000 = 1773000330 seconds (truncated)
        assert first.timestamp == 1773000330

    def test_exact_conversion(self, tmp_path: Path) -> None:
        """Verify exact integer division (truncation, not rounding)."""
        data = {
            "Browser History": [
                {
                    "url": "https://example.com",
                    "time_usec": 1_500_000_999_999,  # 1500000 seconds + 999999 usec
                    "title": "test",
                }
            ]
        }
        fixture = tmp_path / "exact.json"
        fixture.write_text(json.dumps(data), encoding="utf-8")
        records = list(parse_chrome_history(fixture))
        assert records[0].timestamp == 1_500_000


class TestDedup:
    """Test dedup on (url, timestamp, browser)."""

    def test_duplicate_skipped_on_ingest(self, tmp_path: Path) -> None:
        """Fixture entry 1 and entry 5 share the same URL + time_usec;
        the second should be skipped by the UNIQUE constraint."""
        db_path = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_PATH, conn)

        # 5 entries in fixture, 1 is a duplicate → 4 inserted, 1 skipped
        assert report.rows_yielded == 5
        assert report.rows_inserted == 4
        assert report.rows_skipped == 1

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        """Running the same file twice should skip all rows the second time."""
        db_path = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_PATH, conn)
        with connect(db_path) as conn:
            r2 = _new_plugin().run(FIXTURE_PATH, conn)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == 5


class TestIntegration:
    """End-to-end integration tests."""

    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_PATH, conn)
        assert report.rows_inserted == 4
        assert report.rows_skipped == 1

    def test_schema_type_column(self, tmp_path: Path) -> None:
        db_path = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_PATH, conn)
            types = conn.execute(
                "SELECT DISTINCT schema_type FROM browser_history"
            ).fetchall()
        assert all(t[0] == "BrowserHistory" for t in types)

    def test_browser_default_chrome(self, tmp_path: Path) -> None:
        db_path = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_PATH, conn)
            browsers = conn.execute(
                "SELECT DISTINCT browser FROM browser_history"
            ).fetchall()
        assert all(b[0] == "chrome" for b in browsers)

    def test_profiles_stored(self, tmp_path: Path) -> None:
        db_path = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_PATH, conn)
            profiles = conn.execute(
                "SELECT DISTINCT profile FROM browser_history ORDER BY profile"
            ).fetchall()
        profile_values = {p[0] for p in profiles}
        assert "inSOtfpy5kBsXGLBRTqRQQ==" in profile_values
        assert "abc123def456==" in profile_values

    def test_row_count_in_db(self, tmp_path: Path) -> None:
        db_path = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_PATH, conn)
            count = conn.execute("SELECT COUNT(*) FROM browser_history").fetchone()[0]
        assert count == 4

    def test_source_file_registered(self, tmp_path: Path) -> None:
        db_path = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_PATH, conn)
            sf = conn.execute(
                "SELECT source_kind, file_kind FROM source_files WHERE id = ?",
                (report.source_file_id,),
            ).fetchone()
        assert sf[0] == "chrome-history"
        assert sf[1] == "json"
