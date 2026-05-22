"""Tests for the facebook residuals adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.facebook_unified import FacebookUnifiedAdapter as FacebookResidualsAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "facebook_residuals" / "test_residuals.zip"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestFacebookResidualsIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookResidualsAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
        assert report.rows_inserted == 4

    def test_schema_types(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookResidualsAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            has_comments = conn.execute(
                "SELECT COUNT(*) FROM comments"
            ).fetchone()[0]
            has_likes = conn.execute(
                "SELECT COUNT(*) FROM like_actions"
            ).fetchone()[0]
        assert has_comments > 0
        assert has_likes > 0

    def test_direction_outbound(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookResidualsAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            dirs = set()
            for tbl in ("comments", "like_actions", "join_actions", "invite_actions"):
                rows = conn.execute(f"SELECT DISTINCT direction FROM [{tbl}]").fetchall()
                dirs.update(r[0] for r in rows)
        assert dirs == {"outbound"}

    def test_thread_per_kind(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookResidualsAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]
        assert threads == 2

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookResidualsAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
        with connect(db_path) as conn:
            r2 = FacebookResidualsAdapter().run(FIXTURE_ZIP, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookResidualsAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
        assert bridge == report.rows_inserted

    def test_h2_entries_have_timestamps(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookResidualsAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            rows = conn.execute(
                "SELECT date_posted FROM comments"
            ).fetchall()
        assert all(r[0] is not None for r in rows)

    def test_table_entries_have_body(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = FacebookResidualsAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            rows = conn.execute(
                "SELECT body_text FROM like_actions"
            ).fetchall()
        assert all(r[0] and len(r[0]) > 0 for r in rows)
