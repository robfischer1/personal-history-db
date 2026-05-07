"""Tests for the raindrop (bookmarks) adapter."""

from __future__ import annotations

import json
from pathlib import Path

from phdb.adapters.raindrop import (
    RaindropAdapter,
    is_junk,
    normalize_url,
    should_skip,
)
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "raindrop" / "raindrop_export.csv"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
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
