"""Tests for the articles plugin (Phase 7 brief 024 port).

Mirrors the apple_notes_full + staged_md integration-test shape: spin up
a fresh DB, apply all migrations, run the plugin against a fixture
directory, assert on the typed-table rows it produced.
"""

from __future__ import annotations

from pathlib import Path

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.articles import ArticlesPlugin
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "articles" / "test_articles"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestArticlesIntegration:
    def test_basic_ingest_inserts(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = ArticlesPlugin()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
        assert report.rows_inserted == 2
        assert report.rows_skipped == 0

    def test_folder_note_skipped(self, tmp_path: Path) -> None:
        """The folder note (note_type: Folder) must be filtered out by the parser."""
        db_path, settings = _setup(tmp_path)
        adapter = ArticlesPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        assert count == 2

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = ArticlesPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM articles").fetchall()
        assert all(t[0] == "Article" for t in types)

    def test_target_table_is_articles(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = ArticlesPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            count = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert count == 2
        assert doc_count == 0  # articles plugin writes only to articles table

    def test_typed_columns_populated(self, tmp_path: Path) -> None:
        """Frontmatter scalars + list-typed fields land in the typed columns."""
        db_path, settings = _setup(tmp_path)
        adapter = ArticlesPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            row = conn.execute(
                """SELECT subject, url, publisher, creator, description,
                          image_url, categories, tags, aliases,
                          note_type, author_type, ctime, mtime, bucket
                   FROM articles
                   WHERE subject = 'Some Saved Article'"""
            ).fetchone()
        assert row is not None
        (subject, url, publisher, creator, description, image_url,
         categories, tags, aliases, note_type, author_type,
         ctime, mtime, bucket) = row
        assert subject == "Some Saved Article"
        assert url == "https://example.com/some-article"
        assert publisher == "Example Publisher"
        assert creator == "Jane Author"
        assert description == "A short description of the article."
        assert image_url == "https://example.com/cover.png"
        # categories / tags / aliases are JSON arrays
        assert "tech" in categories
        assert "design" in categories
        assert "reading" in tags
        assert "Saved Article" in aliases
        assert note_type == "source-material"
        assert author_type == "external"
        assert ctime == "2024-06-01T10:00:00Z"
        assert mtime == "2024-06-02T11:00:00Z"
        assert bucket == "Articles"

    def test_inline_list_frontmatter(self, tmp_path: Path) -> None:
        """Inline `[a, b]` list syntax should also parse into the JSON columns."""
        db_path, settings = _setup(tmp_path)
        adapter = ArticlesPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            row = conn.execute(
                "SELECT categories, tags FROM articles WHERE subject = 'Another Article'"
            ).fetchone()
        assert row is not None
        categories, tags = row
        assert "news" in categories
        assert "longform" in categories
        assert "archive" in tags

    def test_body_text_preserved(self, tmp_path: Path) -> None:
        """The body is stored verbatim for round-trip materialization."""
        db_path, settings = _setup(tmp_path)
        adapter = ArticlesPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            row = conn.execute(
                "SELECT body_text, body_text_source FROM articles"
                " WHERE subject = 'Some Saved Article'"
            ).fetchone()
        assert row is not None
        body, source = row
        assert "body text of a saved web article" in body
        assert "Second paragraph for body extraction validation." in body
        assert source == "article-md-verbatim"

    def test_file_path_relative_to_root(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = ArticlesPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            paths = {
                p[0]
                for p in conn.execute(
                    "SELECT file_path FROM articles"
                ).fetchall()
            }
        # file_path is stored relative to the source root
        assert "Some Saved Article.md" in paths
        assert "Another Article.md" in paths

    def test_raw_hash_and_dedup_index(self, tmp_path: Path) -> None:
        """raw_hash populated; (source_file_id, raw_hash) dedup unique."""
        db_path, settings = _setup(tmp_path)
        adapter = ArticlesPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            hashes = conn.execute("SELECT raw_hash FROM articles").fetchall()
        assert len(hashes) == 2
        assert all(h[0] for h in hashes)
        # Hashes are distinct per file
        assert len({h[0] for h in hashes}) == 2

    def test_source_file_registered(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = ArticlesPlugin()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
            sf_row = conn.execute(
                "SELECT source_kind, file_kind FROM source_files WHERE id = ?",
                (report.source_file_id,),
            ).fetchone()
        assert sf_row is not None
        assert sf_row[0] == "vault-articles"
        assert sf_row[1] == "md"

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        """Re-running the plugin against the same source is a no-op."""
        db_path, settings = _setup(tmp_path)
        adapter = ArticlesPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
        with connect(db_path) as conn:
            r2 = ArticlesPlugin().run(FIXTURE_DIR, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded
        assert r2.rows_skipped == 2
