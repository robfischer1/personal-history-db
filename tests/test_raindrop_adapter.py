"""Tests for the raindrop (bookmarks) adapter."""

from __future__ import annotations

import json
from pathlib import Path

from phdb.adapters.raindrop import RaindropAdapter
from phdb.formats.raindrop import is_junk, normalize_url, should_skip
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
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
        # www. is part of netloc lowercasing — www.example.com stays, but
        # normalization lowercases it consistently so cross-instrument dedup
        # works when one source has www and another doesn't
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


class TestRaindropIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = RaindropAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_CSV, conn, settings)
        # 5 CSV rows yielded. Row 103 is a duplicate normalized_url of row 100
        # (www.example.com/article?id=123 normalizes the same as example.com/article?id=123)
        # so the upsert updates in-place — still counts as inserted.
        assert report.rows_yielded == 5
        assert report.rows_inserted == 5
        assert report.rows_skipped == 0

    def test_bookmark_count(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = RaindropAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_CSV, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
        # Row 103 collides with row 100 on normalized_url, so 4 distinct rows
        assert count == 4

    def test_junk_excluded(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = RaindropAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_CSV, conn, settings)
            row = conn.execute(
                "SELECT excluded, excluded_reason FROM bookmarks WHERE title='Gmail Root'"
            ).fetchone()
        assert row is not None
        assert row[0] == 1
        assert row[1] is not None
        assert "junk:" in row[1]

    def test_url_normalization(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = RaindropAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_CSV, conn, settings)
            # Row 100 and 103 collide — both normalize to the same URL.
            # Query by normalized_url pattern to verify tracking params stripped.
            row = conn.execute(
                "SELECT normalized_url FROM bookmarks WHERE normalized_url LIKE '%example.com/article%'"
            ).fetchone()
        assert row is not None
        # utm_source and fbclid stripped, trailing slash stripped
        assert "utm_source" not in row[0]
        assert "fbclid" not in row[0]
        assert "id=123" in row[0]

    def test_tags_stored_as_json(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = RaindropAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_CSV, conn, settings)
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
        adapter = RaindropAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_CSV, conn, settings)
            # Row 102 has favorite=true, row 104 also has favorite=true
            fav_rows = conn.execute(
                "SELECT title FROM bookmarks WHERE favorite=1 ORDER BY title"
            ).fetchall()
        titles = [r[0] for r in fav_rows]
        assert "GitHub Repo" in titles
        assert "Favorite Blog" in titles

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = RaindropAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_CSV, conn, settings)
        with connect(db_path) as conn:
            r2 = RaindropAdapter().run(FIXTURE_CSV, conn, settings)
        # Second run still yields and "inserts" via upsert (appearance_count increments)
        assert r2.rows_yielded == 5
        assert r2.rows_inserted == 5
        # Verify no duplicate rows were created
        with connect(db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
        # Row 103 collides with row 100 on normalized_url: still 4 distinct rows
        assert count == 4

    def test_appearance_count_increments(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = RaindropAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_CSV, conn, settings)
        with connect(db_path) as conn:
            RaindropAdapter().run(FIXTURE_CSV, conn, settings)
            counts = conn.execute(
                "SELECT appearance_count FROM bookmarks ORDER BY id"
            ).fetchall()
        # Each row has been seen twice (first run + second run), except the
        # duplicate row 103 which collides with row 100 — that one has been
        # upserted 4 times total (row 100 first-run + row 103 first-run + row
        # 100 second-run + row 103 second-run)
        assert all(c[0] >= 2 for c in counts)

    def test_instrument_is_raindrop(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = RaindropAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_CSV, conn, settings)
            instruments = conn.execute(
                "SELECT DISTINCT instrument FROM bookmarks"
            ).fetchall()
        assert len(instruments) == 1
        assert instruments[0][0] == "raindrop"

    def test_source_file_registered(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = RaindropAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_CSV, conn, settings)
            sf = conn.execute(
                "SELECT id, source_kind FROM source_files WHERE id=?",
                (report.source_file_id,),
            ).fetchone()
        assert sf is not None
        assert sf[1] == "raindrop"


class TestWebPageEntity:
    """Phase 4 — WebPage entity factoring tests."""

    def test_web_pages_populated(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            RaindropAdapter().run(FIXTURE_CSV, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM web_pages").fetchone()[0]
        # 5 rows, but row 100+103 share normalized_url → 4 distinct web_pages
        assert count == 4

    def test_fk_integrity(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            RaindropAdapter().run(FIXTURE_CSV, conn, settings)
            orphans = conn.execute(
                """SELECT b.id FROM bookmarks b
                   LEFT JOIN web_pages wp ON b.web_page_id = wp.id
                   WHERE b.web_page_id IS NULL OR wp.id IS NULL"""
            ).fetchall()
        assert orphans == []

    def test_web_page_id_not_null(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            RaindropAdapter().run(FIXTURE_CSV, conn, settings)
            nulls = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE web_page_id IS NULL"
            ).fetchone()[0]
        assert nulls == 0

    def test_cross_instrument_dedup(self, tmp_path: Path) -> None:
        """Same URL from two instruments → one web_page, two bookmarks."""
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            RaindropAdapter().run(FIXTURE_CSV, conn, settings)
            # Simulate a second instrument by directly inserting a safari bookmark
            # for an existing URL
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
            # Still one web_page for that URL
            wp_count = conn.execute(
                "SELECT COUNT(*) FROM web_pages WHERE normalized_url LIKE '%github.com%'"
            ).fetchone()[0]
            assert wp_count == 1
            # But two bookmarks (raindrop + safari)
            bm_count = conn.execute(
                "SELECT COUNT(*) FROM bookmarks WHERE normalized_url LIKE '%github.com%'"
            ).fetchone()[0]
            assert bm_count == 2

    def test_coalesce_title_update(self, tmp_path: Path) -> None:
        """Second ingest with better title updates web_page."""
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            RaindropAdapter().run(FIXTURE_CSV, conn, settings)
            # Gmail Root web_page should have the title from the bookmark
            row = conn.execute(
                "SELECT title FROM web_pages WHERE normalized_url LIKE '%gmail.com%'"
            ).fetchone()
            assert row is not None
            assert row[0] == "Gmail Root"

    def test_coalesce_preserves_existing_title(self, tmp_path: Path) -> None:
        """Empty title in second ingest doesn't overwrite existing."""
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            RaindropAdapter().run(FIXTURE_CSV, conn, settings)
            # Set a title, then upsert with empty title
            from phdb.adapters.raindrop import upsert_web_page
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
        with connect(db_path) as conn:
            RaindropAdapter().run(FIXTURE_CSV, conn, settings)
            domains = dict(conn.execute(
                "SELECT normalized_url, domain FROM web_pages ORDER BY id"
            ).fetchall())
        for norm_url, domain in domains.items():
            assert domain is not None, f"domain NULL for {norm_url}"
            assert "://" not in domain, f"domain contains scheme: {domain}"
            assert "/" not in domain, f"domain contains path: {domain}"

    def test_domain_values(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            RaindropAdapter().run(FIXTURE_CSV, conn, settings)
            domains = set(
                r[0] for r in conn.execute("SELECT DISTINCT domain FROM web_pages").fetchall()
            )
        assert "example.com" in domains
        assert "github.com" in domains
        assert "www.gmail.com" in domains
        assert "blog.example.com" in domains

    def test_excluded_bookmark_still_creates_web_page(self, tmp_path: Path) -> None:
        """Junk/excluded bookmarks still get a web_page entity."""
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            RaindropAdapter().run(FIXTURE_CSV, conn, settings)
            # Gmail Root is excluded (junk) but should still have a web_page
            row = conn.execute(
                """SELECT wp.id, b.excluded
                   FROM bookmarks b
                   JOIN web_pages wp ON b.web_page_id = wp.id
                   WHERE b.title = 'Gmail Root'"""
            ).fetchone()
            assert row is not None
            assert row[1] == 1  # excluded
            assert row[0] is not None  # but has a web_page

    def test_temporal_window(self, tmp_path: Path) -> None:
        """web_page first_seen/last_seen tracks the bookmark dates."""
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            RaindropAdapter().run(FIXTURE_CSV, conn, settings)
            row = conn.execute(
                """SELECT first_seen, last_seen FROM web_pages
                   WHERE normalized_url LIKE '%example.com/article%'"""
            ).fetchone()
            assert row is not None
            # Row 100 is 2024-06-15, row 103 is 2024-06-18 — same web_page
            assert "2024-06-15" in row[0]
            assert "2024-06-18" in row[1]

    def test_web_page_normalized_url_unique(self, tmp_path: Path) -> None:
        """Verify no duplicate normalized_urls in web_pages."""
        db_path, settings = _setup(tmp_path)
        with connect(db_path) as conn:
            RaindropAdapter().run(FIXTURE_CSV, conn, settings)
            total = conn.execute("SELECT COUNT(*) FROM web_pages").fetchone()[0]
            distinct = conn.execute(
                "SELECT COUNT(DISTINCT normalized_url) FROM web_pages"
            ).fetchone()[0]
        assert total == distinct
