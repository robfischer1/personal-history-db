"""Tests for the staged_md adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.staged_md import StagedMdAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "staged_md" / "test_cluster"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestStagedMdIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
        assert report.rows_inserted == 2
        assert report.rows_skipped == 0

    def test_schema_type_from_frontmatter(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            types = {t[0] for t in conn.execute("SELECT DISTINCT schema_type FROM documents").fetchall()}
        assert "CreativeWork" in types
        assert "Article" in types

    def test_target_table_is_documents(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
            msg_count = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE schema_type IN ('CreativeWork', 'Article')"
            ).fetchone()[0]
        assert doc_count == 2
        assert msg_count == 0

    def test_body_extraction(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            body = conn.execute(
                "SELECT body_text FROM documents WHERE subject = 'My First Note'"
            ).fetchone()
        assert body is not None
        assert "body text" in body[0].lower()

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
        with connect(db_path) as conn:
            r2 = StagedMdAdapter().run(FIXTURE_DIR, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_bucket_is_cluster_name(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            buckets = {b[0] for b in conn.execute("SELECT DISTINCT bucket FROM documents").fetchall()}
        assert "test_cluster" in buckets

    def test_file_path_populated(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            paths = conn.execute(
                "SELECT file_path FROM documents WHERE file_path IS NOT NULL"
            ).fetchall()
        assert len(paths) == 2

    def test_no_threads_created(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
        assert report.threads_created == 0
