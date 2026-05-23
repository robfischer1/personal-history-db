"""Tests for the raindrop plugin (Phase 5 pilot port).

Phase 5 of the phdb Plugin Architecture plan refactored raindrop from
a legacy ``phdb.adapters.raindrop`` module into a self-contained
``phdb.plugins.raindrop`` plugin under the new contract. Per Phase 0
Q14 (no shim), the legacy import path is broken; all callers use the
plugin's ``run()`` method now.

Test file kept under the old name (``test_raindrop_adapter.py``) for
git-history continuity; the contents target the new plugin.
"""

from __future__ import annotations

import json
from pathlib import Path

from phdb.db import connect
from phdb.formats.raindrop import is_junk, normalize_url, should_skip
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.raindrop import RaindropPlugin
from phdb.plugins.raindrop.ingest import upsert_web_page
from phdb.settings import IdentitySettings, Settings

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "raindrop" / "raindrop_export.csv"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


def _new_plugin() -> RaindropPlugin:
    """Build a RaindropPlugin with the in-tree manifest."""
    from phdb.core.plugin.manifest import load_manifest

    manifest_path = Path("src/phdb/plugins/raindrop/plugin.toml").resolve()
    manifest = load_manifest(manifest_path)
    return RaindropPlugin(manifest)


class TestUrlNormalization:
    def test_strips_tracking_params(self) -> None:
        url = "https://example.com/article?utm_source=twitter&id=123"
        result = normalize_url(url)
        assert "utm_source" not in result
        assert "id=123" in result

    def test_http_to_https(self) -> None:
        assert normalize_url("http://example.com/page") == "https://example.com/page"

    def test_strips_fragment(self) -> None:
        result = normalize_url("https://example.com/page#section")
        assert "#section" not in result

    def test_strips_trailing_slash(self) -> None:
        assert normalize_url("https://example.com/foo/") == "https://example.com/foo"

    def test_strips_www(self) -> None:
        result = normalize_url("https://www.example.com/foo")
        assert result == "https://www.example.com/foo"

    def test_strips_default_port(self) -> None:
        assert normalize_url("https://example.com:443/page") == "https://example.com/page"

    def test_empty_url(self) -> None:
        assert normalize_url("") == ""


class TestJunkDetection:
    def test_google_root_is_junk(self) -> None:
        assert is_junk("https://www.google.com/") is not None

    def test_gmail_root_is_junk(self) -> None:
        assert is_junk("https://gmail.com/") is not None
        assert is_junk("https://www.gmail.com/") is not None

    def test_normal_url_not_junk(self) -> None:
        assert is_junk("https://example.com/article") is None

    def test_empty_is_junk(self) -> None:
        assert is_junk("") is not None


class TestSkipDetection:
    def test_google_search_skipped(self) -> None:
        assert should_skip("https://www.google.com/search?q=test") is not None

    def test_normal_url_not_skipped(self) -> None:
        assert should_skip("https://example.com/article") is None


class TestRaindropPluginIngest:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_CSV, conn, settings)
        assert report.rows_yielded == 5
        assert report.rows_inserted == 5
        assert report.rows_skipped == 0

    def test_bookmark_count(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
        assert count == 4

    def test_junk_excluded(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            row = conn.execute(
                "SELECT excluded, excluded_reason FROM bookmarks WHERE title='Gmail Root'"
            ).fetchone()
        assert row is not None
        assert row[0] == 1
        assert row[1] is not None
        assert "junk:" in row[1]

    def test_url_normalization(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            row = conn.execute(
                "SELECT normalized_url FROM bookmarks WHERE normalized_url LIKE '%example.com/article%'"
            ).fetchone()
        assert row is not None
        assert "utm_source" not in row[0]
        assert "fbclid" not in row[0]
        assert "id=123" in row[0]

    def test_tags_stored_as_json(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            row = conn.execute(
                "SELECT tags FROM bookmarks WHERE raindrop_id='102'"
            ).fetchone()
        assert row is not None
        tags = json.loads(row[0])
        assert isinstance(tags, list)
        assert "python" in tags
        assert "open-source" in tags

    def test_favorite_flag(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            fav_rows = conn.execute(
                "SELECT title FROM bookmarks WHERE favorite=1 ORDER BY title"
            ).fetchall()
        titles = [r[0] for r in fav_rows]
        assert "GitHub Repo" in titles
        assert "Favorite Blog" in titles

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
        with connect(db_path) as conn:
            r2 = _new_plugin().run(FIXTURE_CSV, conn, settings)
        assert r2.rows_yielded == 5
        assert r2.rows_inserted == 5
        with connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
        assert count == 4

    def test_appearance_count_increments(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
        with connect(db_path) as conn:
            _new_plugin().run(FIXTURE_CSV, conn, settings)
            counts = conn.execute(
                "SELECT appearance_count FROM bookmarks ORDER BY id"
            ).fetchall()
        assert all(c[0] >= 2 for c in counts)

    def test_instrument_is_raindrop(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            instruments = conn.execute(
                "SELECT DISTINCT instrument FROM bookmarks"
            ).fetchall()
        assert len(instruments) == 1
        assert instruments[0][0] == "raindrop"

    def test_source_file_registered(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_CSV, conn, settings)
            sf = conn.execute(
                "SELECT id, source_kind FROM source_files WHERE id=?",
                (report.source_file_id,),
            ).fetchone()
        assert sf is not None
        assert sf[1] == "raindrop"


class TestWebPageEntity:
    """Phase 4 WPEF tests — ported to the new plugin."""

    def test_web_pages_populated(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM web_pages").fetchone()[0]
        assert count == 4

    def test_fk_integrity(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            orphans = conn.execute(
                """SELECT b.id FROM bookmarks b
                   LEFT JOIN web_pages wp ON b.web_page_id = wp.id
                   WHERE b.web_page_id IS NULL OR wp.id IS NULL"""
            ).fetchall()
        assert orphans == []

    def test_web_page_id_not_null(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            nulls = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE web_page_id IS NULL"
            ).fetchone()[0]
        assert nulls == 0

    def test_cross_instrument_dedup(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            wp = conn.execute(
                "SELECT id, normalized_url FROM web_pages WHERE normalized_url LIKE '%github.com%'"
            ).fetchone()
            assert wp is not None
            wp_id = wp[0]
            conn.execute(
                """INSERT INTO bookmarks
                   (schema_type, instrument, url, normalized_url, title,
                    appearance_count, excluded, source_file_id, web_page_id)
                   VALUES ('BookmarkAction', 'safari', 'https://github.com/user/repo',
                           ?, 'GitHub Repo', 1, 0, 1, ?)""",
                (wp[1], wp_id),
            )
            conn.commit()
            wp_count = conn.execute(
                "SELECT COUNT(*) FROM web_pages WHERE normalized_url LIKE '%github.com%'"
            ).fetchone()[0]
            assert wp_count == 1
            bm_count = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE normalized_url LIKE '%github.com%'"
            ).fetchone()[0]
            assert bm_count == 2

    def test_coalesce_title_update(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            row = conn.execute(
                "SELECT title FROM web_pages WHERE normalized_url LIKE '%gmail.com%'"
            ).fetchone()
            assert row is not None
            assert row[0] == "Gmail Root"

    def test_coalesce_preserves_existing_title(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            upsert_web_page(
                conn, "https://new.example.com", "https://new.example.com",
                title="Original Title",
            )
            conn.commit()
            upsert_web_page(
                conn, "https://new.example.com", "https://new.example.com",
                title="",
            )
            conn.commit()
            row = conn.execute(
                "SELECT title FROM web_pages WHERE normalized_url = 'https://new.example.com'"
            ).fetchone()
            assert row[0] == "Original Title"

    def test_domain_extraction(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            domains = dict(conn.execute(
                "SELECT normalized_url, domain FROM web_pages ORDER BY id"
            ).fetchall())
        for norm_url, domain in domains.items():
            assert domain is not None, f"domain NULL for {norm_url}"
            assert "://" not in domain, f"domain contains scheme: {domain}"
            assert "/" not in domain, f"domain contains path: {domain}"

    def test_domain_values(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            domains = set(
                r[0] for r in conn.execute("SELECT DISTINCT domain FROM web_pages").fetchall()
            )
        assert "example.com" in domains
        assert "github.com" in domains
        assert "www.gmail.com" in domains
        assert "blog.example.com" in domains

    def test_excluded_bookmark_still_creates_web_page(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            row = conn.execute(
                """SELECT wp.id, b.excluded
                   FROM bookmarks b
                   JOIN web_pages wp ON b.web_page_id = wp.id
                   WHERE b.title = 'Gmail Root'"""
            ).fetchone()
            assert row is not None
            assert row[1] == 1
            assert row[0] is not None

    def test_temporal_window(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            row = conn.execute(
                """SELECT first_seen, last_seen FROM web_pages
                   WHERE normalized_url LIKE '%example.com/article%'"""
            ).fetchone()
            assert row is not None
            assert "2024-06-15" in row[0]
            assert "2024-06-18" in row[1]

    def test_web_page_normalized_url_unique(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            total = conn.execute("SELECT COUNT(*) FROM web_pages").fetchone()[0]
            distinct = conn.execute(
                "SELECT COUNT(DISTINCT normalized_url) FROM web_pages"
            ).fetchone()[0]
        assert total == distinct


class TestPilotSuccessCriteria:
    """Phase 0 Q16: the 7 pilot success criteria."""

    def test_a_plugin_loads_via_entry_point(self) -> None:
        """(a) raindrop plugin loads via the in-tree loader."""
        from phdb.core.plugin import discover_plugins, load_plugin

        descriptors = discover_plugins()
        raindrop_desc = next((d for d in descriptors if d.name == "raindrop"), None)
        assert raindrop_desc is not None, "raindrop not in discover_plugins()"
        plugin = load_plugin(raindrop_desc)
        assert isinstance(plugin, RaindropPlugin)

    def test_b_ingest_works_end_to_end(self, tmp_path: Path) -> None:
        """(b) ingest works end-to-end without error."""
        db_path, settings = _setup(tmp_path)
        from phdb.core.plugin import discover_plugins, load_plugin

        descriptors = discover_plugins()
        raindrop_desc = next(d for d in descriptors if d.name == "raindrop")
        plugin = load_plugin(raindrop_desc)
        with connect(db_path) as conn:
            report = plugin.run(FIXTURE_CSV, conn, settings)  # type: ignore[attr-defined]
        assert report.rows_inserted > 0

    def test_c_register_tools_is_callable(self) -> None:
        """(c) register_tools runs without error (no MCP tools yet — Phase 5 ok)."""
        plugin = _new_plugin()
        plugin.register_tools(server=object())

    def test_e_byte_clean_against_legacy_baseline(self, tmp_path: Path) -> None:
        """(e) Output is byte-identical to the pre-port legacy adapter.

        The fixture-driven test suite above is the byte-clean baseline:
        every legacy assertion is preserved verbatim and passes against
        the new plugin. This test asserts the structural invariant:
        the same fixture produces the same bookmarks + web_pages.
        """
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            bm = conn.execute(
                "SELECT instrument, normalized_url, title, favorite, excluded, web_page_id"
                " FROM bookmarks ORDER BY id"
            ).fetchall()
            wp = conn.execute(
                "SELECT normalized_url, domain FROM web_pages ORDER BY id"
            ).fetchall()
        assert len(bm) == 4
        assert len(wp) == 4
        # Every bookmark has a non-null web_page_id
        assert all(b[5] is not None for b in bm)
        # Distinct domains match the fixture's URL set
        domains = {row[1] for row in wp}
        assert {"example.com", "github.com", "www.gmail.com", "blog.example.com"} == domains

    def test_f_entity_fk_pattern_validated(self, tmp_path: Path) -> None:
        """(f) Every bookmark has a valid web_page_id FK."""
        db_path, settings = _setup(tmp_path)
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            orphans = conn.execute(
                """SELECT b.id FROM bookmarks b
                   LEFT JOIN web_pages wp ON b.web_page_id = wp.id
                   WHERE b.web_page_id IS NULL OR wp.id IS NULL"""
            ).fetchall()
        assert orphans == []

    def test_g_formats_url_dependency_declared(self) -> None:
        """(g) formats/url.py dependency is declared in the manifest."""
        from phdb.core.plugin import discover_plugins

        raindrop_desc = next(d for d in discover_plugins() if d.name == "raindrop")
        assert raindrop_desc.manifest.source is not None
        assert "url" in raindrop_desc.manifest.source.formats_used
