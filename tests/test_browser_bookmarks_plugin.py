"""Tests for the browser_bookmarks plugin.

Covers both format parsers (Netscape HTML, Chrome JSON), the full
ingest pipeline (plugin.run → bookmarks + web_pages), cross-instrument
dedup, folder hierarchy, URL normalisation, and junk/skip filtering.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.browser_bookmarks import BrowserBookmarksPlugin
from phdb.plugins.browser_bookmarks.ingest import (
    _unix_s_to_iso,
    _webkit_us_to_iso,
    parse_chrome_json,
    parse_netscape_html,
)
from phdb.settings import IdentitySettings, Settings

FIXTURE_HTML = (
    Path(__file__).parent / "fixtures" / "browser_bookmarks" / "firefox_bookmarks.html"
)
FIXTURE_JSON = (
    Path(__file__).parent / "fixtures" / "browser_bookmarks" / "chrome_bookmarks.json"
)


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


def _new_plugin() -> BrowserBookmarksPlugin:
    from phdb.core.plugin.manifest import load_manifest

    manifest_path = Path(
        "src/phdb/plugins/browser_bookmarks/plugin.toml"
    ).resolve()
    manifest = load_manifest(manifest_path)
    return BrowserBookmarksPlugin(manifest)


# ---------------------------------------------------------------------------
# Timestamp utilities
# ---------------------------------------------------------------------------

class TestTimestampUtils:
    def test_webkit_us_known_value(self) -> None:
        # 13347573500000000 WebKit us → approx 2023-09-xx UTC
        result = _webkit_us_to_iso(13_347_573_500_000_000)
        assert result is not None
        assert result.startswith("2023-")

    def test_webkit_us_zero_returns_none(self) -> None:
        assert _webkit_us_to_iso(0) is None

    def test_webkit_us_invalid_returns_none(self) -> None:
        assert _webkit_us_to_iso("not-a-number") is None

    def test_unix_s_known_value(self) -> None:
        # 1716000000 → 2024-05-18 UTC
        result = _unix_s_to_iso(1_716_000_000)
        assert result is not None
        assert "2024-05-18" in result

    def test_unix_s_zero_returns_none(self) -> None:
        assert _unix_s_to_iso(0) is None

    def test_unix_s_invalid_returns_none(self) -> None:
        assert _unix_s_to_iso("bad") is None


# ---------------------------------------------------------------------------
# Netscape HTML parser
# ---------------------------------------------------------------------------

class TestNetscapeHtmlParser:
    def test_parses_basic_bookmark(self) -> None:
        events = list(parse_netscape_html(FIXTURE_HTML))
        urls = [e.url for e in events]
        assert any("example.com/article" in u for u in urls)

    def test_instrument_inferred_as_firefox(self) -> None:
        events = list(parse_netscape_html(FIXTURE_HTML))
        instruments = {e.instrument for e in events}
        assert "firefox" in instruments

    def test_tracking_params_stripped(self) -> None:
        events = list(parse_netscape_html(FIXTURE_HTML))
        article = next(e for e in events if "example.com/article" in e.url)
        assert "utm_source" not in article.normalized_url
        assert "id=42" in article.normalized_url

    def test_folder_hierarchy_preserved(self) -> None:
        events = list(parse_netscape_html(FIXTURE_HTML))
        nested = next(
            (e for e in events if "deep-learning" in e.url), None
        )
        assert nested is not None
        assert nested.folder is not None
        assert "Work" in nested.folder
        assert "Research" in nested.folder

    def test_top_level_folder_preserved(self) -> None:
        events = list(parse_netscape_html(FIXTURE_HTML))
        toolbar = next(
            (e for e in events if "github.com" in e.url), None
        )
        assert toolbar is not None
        assert toolbar.folder is not None
        assert "Bookmarks Toolbar" in toolbar.folder

    def test_tags_parsed_from_html(self) -> None:
        events = list(parse_netscape_html(FIXTURE_HTML))
        github = next(e for e in events if "github.com" in e.url)
        assert "python" in github.tags
        assert "open-source" in github.tags

    def test_google_root_included_as_junk_candidate(self) -> None:
        # google.com root is junk but NOT skipped by should_skip — it enters
        # the bookmarks table as excluded=1. Only Google *search* URLs are
        # skipped at parse time.
        events = list(parse_netscape_html(FIXTURE_HTML))
        google = next(
            (e for e in events if e.url == "https://www.google.com/"), None
        )
        assert google is not None

    def test_google_search_skipped_at_parse(self) -> None:
        # Google search results are filtered by should_skip inside the parser.
        events = list(parse_netscape_html(FIXTURE_HTML))
        search_urls = [e.url for e in events if "google.com/search" in e.url]
        assert search_urls == []

    def test_javascript_url_skipped(self) -> None:
        events = list(parse_netscape_html(FIXTURE_HTML))
        js_urls = [e.url for e in events if e.url.startswith("javascript:")]
        assert js_urls == []

    def test_date_added_parsed(self) -> None:
        events = list(parse_netscape_html(FIXTURE_HTML))
        article = next(e for e in events if "example.com/article" in e.url)
        assert article.date_added != ""
        assert "2024-05-18" in article.date_added

    def test_normalized_url_http_to_https(self) -> None:
        events = list(parse_netscape_html(FIXTURE_HTML))
        for event in events:
            assert not event.normalized_url.startswith("http://"), (
                f"Expected https: {event.normalized_url}"
            )

    def test_no_duplicate_urls_per_instrument(self) -> None:
        events = list(parse_netscape_html(FIXTURE_HTML))
        seen: set[tuple[str, str]] = set()
        for e in events:
            key = (e.normalized_url, e.instrument)
            # Duplicates in the parse output are deduplicated in DB upsert,
            # but we should not produce duplicates in the fixture.
            assert key not in seen, f"Duplicate: {key}"
            seen.add(key)


# ---------------------------------------------------------------------------
# Chrome JSON parser
# ---------------------------------------------------------------------------

class TestChromeJsonParser:
    def test_parses_bookmark_bar(self) -> None:
        events = list(parse_chrome_json(FIXTURE_JSON))
        urls = [e.url for e in events]
        assert "https://docs.python.org/3/" in urls

    def test_instrument_is_chrome(self) -> None:
        events = list(parse_chrome_json(FIXTURE_JSON))
        instruments = {e.instrument for e in events}
        assert instruments == {"chrome"}

    def test_nested_folder_path(self) -> None:
        events = list(parse_chrome_json(FIXTURE_JSON))
        sqlite = next(e for e in events if "sqlite.org" in e.url)
        assert sqlite.folder is not None
        assert "Dev" in sqlite.folder
        assert "Databases" in sqlite.folder

    def test_top_level_folder_name(self) -> None:
        events = list(parse_chrome_json(FIXTURE_JSON))
        fastapi = next(e for e in events if "fastapi" in e.url)
        assert fastapi.folder is not None
        assert "Dev" in fastapi.folder

    def test_other_root_included(self) -> None:
        events = list(parse_chrome_json(FIXTURE_JSON))
        urls = [e.url for e in events]
        assert "https://news.ycombinator.com/" in urls

    def test_google_search_in_other_skipped(self) -> None:
        events = list(parse_chrome_json(FIXTURE_JSON))
        skipped = [e.url for e in events if "google.com/search" in e.url]
        assert skipped == []

    def test_webkit_timestamp_converted(self) -> None:
        events = list(parse_chrome_json(FIXTURE_JSON))
        python_docs = next(e for e in events if "docs.python.org" in e.url)
        assert python_docs.date_added != ""
        assert "T" in python_docs.date_added  # ISO 8601 format

    def test_empty_synced_root_produces_no_events(self, tmp_path: Path) -> None:
        # Write a minimal JSON with only synced root (empty children).
        data = {
            "roots": {
                "bookmark_bar": {"type": "folder", "name": "bar", "children": []},
                "other": {"type": "folder", "name": "other", "children": []},
                "synced": {"type": "folder", "name": "synced", "children": []},
            }
        }
        p = tmp_path / "Bookmarks"
        p.write_text(json.dumps(data), encoding="utf-8")
        events = list(parse_chrome_json(p))
        assert events == []

    def test_invalid_json_raises_value_error(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("not json at all", encoding="utf-8")
        with pytest.raises(ValueError, match="Not valid JSON"):
            list(parse_chrome_json(p))

    def test_title_stored(self) -> None:
        events = list(parse_chrome_json(FIXTURE_JSON))
        hn = next(e for e in events if "ycombinator" in e.url)
        assert hn.title == "Hacker News"


# ---------------------------------------------------------------------------
# Full ingest pipeline — HTML
# ---------------------------------------------------------------------------

class TestHtmlPluginIngest:
    def test_rows_inserted(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_HTML, conn, settings)
        # The fixture has 6 <A> tags; 1 skipped (google search), 1 skipped
        # (javascript:) = 4 bookmarks yielded. google.com root is NOT skipped
        # at parse time (it enters as junk-excluded).
        assert report.rows_yielded >= 4
        assert report.rows_inserted == report.rows_yielded

    def test_web_pages_created(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_HTML, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM web_pages").fetchone()[0]
        assert count >= 4

    def test_instrument_is_firefox(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_HTML, conn, settings)
            instruments = {
                r[0] for r in conn.execute(
                    "SELECT DISTINCT instrument FROM bookmarks"
                ).fetchall()
            }
        assert "firefox" in instruments

    def test_junk_url_excluded(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_HTML, conn, settings)
            row = conn.execute(
                "SELECT b.excluded FROM bookmarks b"
                " JOIN web_pages wp ON b.web_page_id = wp.id"
                " WHERE wp.normalized_url LIKE '%google.com%'"
            ).fetchone()
        assert row is not None
        assert row[0] == 1

    def test_folder_stored(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_HTML, conn, settings)
            row = conn.execute(
                "SELECT folder FROM bookmarks b"
                " JOIN web_pages wp ON b.web_page_id = wp.id"
                " WHERE wp.normalized_url LIKE '%blog.example.com%'"
            ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert "Work" in row[0]

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_HTML, conn, settings)
        with connect(db_path) as conn:
            plugin.run(FIXTURE_HTML, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
        # Same fixture => same rows; upsert on (web_page_id, instrument)
        initial_count = count
        with connect(db_path) as conn:
            count2 = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
        assert count2 == initial_count

    def test_fk_integrity(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_HTML, conn, settings)
            orphans = conn.execute(
                """SELECT b.id FROM bookmarks b
                   LEFT JOIN web_pages wp ON b.web_page_id = wp.id
                   WHERE b.web_page_id IS NULL OR wp.id IS NULL"""
            ).fetchall()
        assert orphans == []

    def test_source_file_registered(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_HTML, conn, settings)
            sf = conn.execute(
                "SELECT source_kind, file_kind FROM source_files WHERE id=?",
                (report.source_file_id,),
            ).fetchone()
        assert sf is not None
        assert sf[0] == "browser_bookmarks"
        assert sf[1] == "html"

    def test_tags_stored_as_json(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_HTML, conn, settings)
            row = conn.execute(
                "SELECT b.tags FROM bookmarks b"
                " JOIN web_pages wp ON b.web_page_id = wp.id"
                " WHERE wp.normalized_url LIKE '%github.com%'"
            ).fetchone()
        assert row is not None
        tags = json.loads(row[0])
        assert "python" in tags
        assert "open-source" in tags


# ---------------------------------------------------------------------------
# Full ingest pipeline — JSON
# ---------------------------------------------------------------------------

class TestJsonPluginIngest:
    def test_rows_inserted(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_JSON, conn, settings)
        # Fixture: 4 url nodes (python, fastapi, sqlite, hacker news);
        # google search is skipped → 4 yielded
        assert report.rows_yielded == 4
        assert report.rows_inserted == 4

    def test_instrument_is_chrome(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_JSON, conn, settings)
            instruments = {
                r[0] for r in conn.execute(
                    "SELECT DISTINCT instrument FROM bookmarks"
                ).fetchall()
            }
        assert instruments == {"chrome"}

    def test_nested_folder_in_db(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_JSON, conn, settings)
            row = conn.execute(
                "SELECT folder FROM bookmarks b"
                " JOIN web_pages wp ON b.web_page_id = wp.id"
                " WHERE wp.normalized_url LIKE '%sqlite.org%'"
            ).fetchone()
        assert row is not None
        assert row[0] is not None
        assert "Dev" in row[0]
        assert "Databases" in row[0]

    def test_web_pages_created(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_JSON, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM web_pages").fetchone()[0]
        assert count == 4

    def test_source_file_registered(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_JSON, conn, settings)
            sf = conn.execute(
                "SELECT source_kind, file_kind FROM source_files WHERE id=?",
                (report.source_file_id,),
            ).fetchone()
        assert sf is not None
        assert sf[0] == "browser_bookmarks"
        assert sf[1] == "json"

    def test_fk_integrity(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_JSON, conn, settings)
            orphans = conn.execute(
                """SELECT b.id FROM bookmarks b
                   LEFT JOIN web_pages wp ON b.web_page_id = wp.id
                   WHERE b.web_page_id IS NULL OR wp.id IS NULL"""
            ).fetchall()
        assert orphans == []


# ---------------------------------------------------------------------------
# Cross-instrument dedup
# ---------------------------------------------------------------------------

class TestCrossInstrumentDedup:
    def test_firefox_and_chrome_share_web_page(self, tmp_path: Path) -> None:
        """Same URL from two browsers → one web_pages row, two bookmarks rows."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()

        # Ingest HTML (firefox) first
        with connect(db_path) as conn:
            plugin.run(FIXTURE_HTML, conn, settings)

        # Manually insert a chrome bookmark for a URL already in web_pages
        with connect(db_path) as conn:
            wp = conn.execute(
                "SELECT id FROM web_pages WHERE normalized_url LIKE '%github.com%'"
            ).fetchone()
            assert wp is not None
            wp_id = wp[0]
            conn.execute(
                """INSERT INTO bookmarks
                   (schema_type, instrument, appearance_count, excluded, source_file_id, web_page_id)
                   VALUES ('BookmarkAction', 'chrome', 1, 0, 1, ?)""",
                (wp_id,),
            )
            conn.commit()

        with connect(db_path) as conn:
            wp_count = conn.execute(
                "SELECT COUNT(*) FROM web_pages WHERE normalized_url LIKE '%github.com%'"
            ).fetchone()[0]
            bm_count = conn.execute(
                "SELECT COUNT(*) FROM bookmarks b"
                " JOIN web_pages wp ON wp.id = b.web_page_id"
                " WHERE wp.normalized_url LIKE '%github.com%'"
            ).fetchone()[0]
        assert wp_count == 1
        assert bm_count == 2

    def test_raindrop_and_firefox_coexist(self, tmp_path: Path) -> None:
        """Raindrop + browser bookmarks for the same URL → two bookmark rows."""
        from phdb.plugins.raindrop import RaindropPlugin
        from phdb.core.plugin.manifest import load_manifest

        raindrop_manifest = load_manifest(
            Path("src/phdb/plugins/raindrop/plugin.toml").resolve()
        )
        raindrop_plugin = RaindropPlugin(raindrop_manifest)
        raindrop_fixture = (
            Path(__file__).parent / "fixtures" / "raindrop" / "raindrop_export.csv"
        )

        db_path, settings = _setup(tmp_path)
        browser_plugin = _new_plugin()

        with connect(db_path) as conn:
            raindrop_plugin.run(raindrop_fixture, conn, settings)

        with connect(db_path) as conn:
            browser_plugin.run(FIXTURE_HTML, conn, settings)

        with connect(db_path) as conn:
            instruments = set(
                r[0] for r in conn.execute(
                    "SELECT DISTINCT instrument FROM bookmarks"
                ).fetchall()
            )
        assert "raindrop" in instruments
        assert "firefox" in instruments


# ---------------------------------------------------------------------------
# Plugin discovery
# ---------------------------------------------------------------------------

class TestPluginDiscovery:
    def test_plugin_loads_via_entry_point(self) -> None:
        from phdb.core.plugin import discover_plugins, load_plugin

        descriptors = discover_plugins()
        bb_desc = next(
            (d for d in descriptors if d.name == "browser_bookmarks"), None
        )
        assert bb_desc is not None, "browser_bookmarks not in discover_plugins()"
        plugin = load_plugin(bb_desc)
        assert isinstance(plugin, BrowserBookmarksPlugin)

    def test_register_cli_returns_none(self) -> None:
        plugin = _new_plugin()
        assert plugin.register_cli(parser=object()) is None

    def test_register_tools_returns_none(self) -> None:
        plugin = _new_plugin()
        assert plugin.register_tools(server=object()) is None
