"""Tests for the google_search plugin (migration 0036).

Google Takeout Search MyActivity HTML exports are monolithic HTML files
with repeated ``<div class="outer-cell">`` blocks.  The parser uses
streaming regex-based extraction — no DOM parser.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.google_search import GoogleSearchPlugin
from phdb.plugins.google_search.plugin import (
    SearchEntry,
    parse_block,
    parse_search_html,
    parse_timestamp,
)
from phdb.settings import IdentitySettings, Settings

FIXTURE = Path(__file__).parent / "fixtures" / "google_search_sample.html"

# The fixture has:
#   2 "Searched for" entries with location (obsidian timeline, best pizza near me)
#   1 "Searched for" entry without location (python streaming html parser)
#   1 "Visited" entry without location (html.parser docs)
# Total entries = 4


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


def _new_plugin() -> GoogleSearchPlugin:
    from phdb.core.plugin.manifest import load_manifest

    manifest_path = (
        Path("src/phdb/plugins/google_search/plugin.toml").resolve()
    )
    manifest = load_manifest(manifest_path)
    return GoogleSearchPlugin(manifest)


# ─── Timestamp parsing tests ─────────────────────────────────────────────────


class TestTimestampParsing:
    def test_edt_timestamp(self) -> None:
        ts = parse_timestamp("Mar 9, 2026, 4:03:47 AM EDT")
        assert ts is not None
        # EDT is UTC-4.  2026-03-09 04:03:47 EDT = 2026-03-09 08:03:47 UTC
        assert ts == 1773043427

    def test_est_timestamp(self) -> None:
        ts = parse_timestamp("Mar 8, 2026, 11:22:15 PM EST")
        assert ts is not None
        # EST is UTC-5.  2026-03-09 04:22:15 UTC
        assert ts == 1773030135

    def test_no_timezone(self) -> None:
        ts = parse_timestamp("Mar 9, 2026, 4:03:47 AM")
        assert ts is not None
        # Falls back to UTC

    def test_garbage_returns_none(self) -> None:
        ts = parse_timestamp("not a timestamp at all")
        assert ts is None

    def test_empty_returns_none(self) -> None:
        ts = parse_timestamp("")
        assert ts is None


# ─── Block parsing tests ─────────────────────────────────────────────────────


class TestBlockParsing:
    def test_searched_for_with_location(self) -> None:
        block = '''
        <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">
          Searched for <a href="https://www.google.com/search?q=obsidian+timeline">obsidian timeline</a><br>
          Mar 9, 2026, 4:03:47 AM EDT<br>
        </div>
        <div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">
          <b>Products:</b><br>&emsp;Search<br>
          <b>Locations:</b><br>
          &emsp;At <a href="https://www.google.com/maps/@?api=1&amp;map_action=map&amp;center=40.822750,-74.112250&amp;zoom=12">this general area</a><br>
        </div>
        '''
        entry = parse_block(block, "test.html")
        assert entry is not None
        assert entry.query == "obsidian timeline"
        assert entry.url == "https://www.google.com/search?q=obsidian+timeline"
        assert entry.clicked_url is None
        assert entry.location_lat == pytest.approx(40.82275)
        assert entry.location_lon == pytest.approx(-74.11225)
        assert entry.product == "Search"

    def test_searched_for_without_location(self) -> None:
        block = '''
        <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">
          Searched for <a href="https://www.google.com/search?q=python+html">python html</a><br>
          Mar 8, 2026, 11:22:15 PM EST<br>
        </div>
        <div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">
          <b>Products:</b><br>&emsp;Search<br>
        </div>
        '''
        entry = parse_block(block, "test.html")
        assert entry is not None
        assert entry.query == "python html"
        assert entry.location_lat is None
        assert entry.location_lon is None

    def test_visited_entry(self) -> None:
        block = '''
        <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">
          Visited <a href="https://docs.python.org/3/library/html.parser.html">html.parser docs</a><br>
          Mar 8, 2026, 11:23:02 PM EST<br>
        </div>
        <div class="content-cell mdl-cell mdl-cell--12-col mdl-typography--caption">
          <b>Products:</b><br>&emsp;Search<br>
        </div>
        '''
        entry = parse_block(block, "test.html")
        assert entry is not None
        assert entry.query == "html.parser docs"
        assert entry.url is None
        assert entry.clicked_url == "https://docs.python.org/3/library/html.parser.html"

    def test_unparseable_block_returns_none(self) -> None:
        block = '''
        <div class="content-cell mdl-cell mdl-cell--6-col mdl-typography--body-1">
          Some other activity type<br>
          Mar 8, 2026, 11:23:02 PM EST<br>
        </div>
        '''
        entry = parse_block(block, "test.html")
        assert entry is None


# ─── HTML file parsing tests ────────────────────────────────────────────────


class TestHtmlFileParsing:
    def test_parse_fixture_entry_count(self) -> None:
        entries = list(parse_search_html(FIXTURE))
        assert len(entries) == 4

    def test_first_entry_is_obsidian_timeline(self) -> None:
        entries = list(parse_search_html(FIXTURE))
        assert entries[0].query == "obsidian timeline"

    def test_visited_entry_present(self) -> None:
        entries = list(parse_search_html(FIXTURE))
        visited = [e for e in entries if e.clicked_url is not None]
        assert len(visited) == 1
        assert "docs.python.org" in (visited[0].clicked_url or "")

    def test_entries_with_location(self) -> None:
        entries = list(parse_search_html(FIXTURE))
        with_loc = [e for e in entries if e.location_lat is not None]
        assert len(with_loc) == 2

    def test_entries_without_location(self) -> None:
        entries = list(parse_search_html(FIXTURE))
        without_loc = [e for e in entries if e.location_lat is None]
        assert len(without_loc) == 2


# ─── Plugin integration tests ──────────────────────────────────────────────


class TestGoogleSearchIngest:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE, conn, settings)
        assert report.rows_yielded == 4
        assert report.rows_inserted == 4
        assert report.rows_skipped == 0

    def test_row_count(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM search_history").fetchone()[0]
        assert count == 4

    def test_dedup_on_rerun(self, tmp_path: Path) -> None:
        """Running the same file twice should not duplicate rows."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report1 = plugin.run(FIXTURE, conn, settings)
            report2 = plugin.run(FIXTURE, conn, settings)
        assert report1.rows_inserted == 4
        assert report2.rows_inserted == 0
        assert report2.rows_skipped == 4

    def test_query_content(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            rows = conn.execute(
                "SELECT query FROM search_history ORDER BY timestamp DESC"
            ).fetchall()
        queries = [r[0] for r in rows]
        assert "obsidian timeline" in queries
        assert "python streaming html parser" in queries
        assert "best pizza near me" in queries

    def test_location_stored(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            row = conn.execute(
                "SELECT location_lat, location_lon FROM search_history "
                "WHERE query = 'obsidian timeline'"
            ).fetchone()
        assert row is not None
        assert abs(row[0] - 40.82275) < 0.001
        assert abs(row[1] - (-74.11225)) < 0.001

    def test_no_location_stored_as_null(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            row = conn.execute(
                "SELECT location_lat, location_lon FROM search_history "
                "WHERE query = 'python streaming html parser'"
            ).fetchone()
        assert row is not None
        assert row[0] is None
        assert row[1] is None

    def test_visited_entry_has_clicked_url(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE, conn, settings)
            row = conn.execute(
                "SELECT clicked_url FROM search_history "
                "WHERE clicked_url IS NOT NULL"
            ).fetchone()
        assert row is not None
        assert "docs.python.org" in row[0]
