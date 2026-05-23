"""Tests for the staged_md plugin (Phase 7 brief 026 port).

Phase 7 of the phdb Plugin Architecture plan refactored staged_md from
the legacy ``phdb.adapters.staged_md`` module into a self-contained
``phdb.plugins.staged_md`` plugin under the new contract. Per Phase 0
Q14 (no shim), the legacy import path is broken; all callers use the
plugin's ``run()`` method now.

Test file kept under the old name (``test_staged_md_adapter.py``) for
git-history continuity; the contents target the new plugin.
"""

from __future__ import annotations

from pathlib import Path

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.staged_md import StagedMdPlugin
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "staged_md" / "test_cluster"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestStagedMdIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdPlugin()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
        assert report.rows_inserted == 2
        assert report.rows_skipped == 0

    def test_schema_type_from_frontmatter(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            types = {t[0] for t in conn.execute("SELECT DISTINCT schema_type FROM documents").fetchall()}
        assert "CreativeWork" in types
        assert "Article" in types

    def test_target_table_is_documents(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            doc_count = conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0]
        assert doc_count == 2

    def test_body_extraction(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            body = conn.execute(
                "SELECT body_text FROM documents WHERE subject = 'My First Note'"
            ).fetchone()
        assert body is not None
        assert "body text" in body[0].lower()

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
        with connect(db_path) as conn:
            r2 = StagedMdPlugin().run(FIXTURE_DIR, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_bucket_is_cluster_name(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            buckets = {b[0] for b in conn.execute("SELECT DISTINCT bucket FROM documents").fetchall()}
        assert "test_cluster" in buckets

    def test_file_path_populated(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdPlugin()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DIR, conn, settings)
            paths = conn.execute(
                "SELECT file_path FROM documents WHERE file_path IS NOT NULL"
            ).fetchall()
        assert len(paths) == 2

    def test_no_threads_created(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = StagedMdPlugin()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DIR, conn, settings)
        assert report.threads_created == 0
