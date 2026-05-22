"""Tests for the amazon adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.amazon import AmazonAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "amazon" / "amazon_export.zip"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


class TestAmazonIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AmazonAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
        assert report.rows_inserted == 3
        assert report.rows_skipped == 0

    def test_all_bulk(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AmazonAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            bulk = conn.execute(
                "SELECT DISTINCT is_bulk FROM order_actions"
                " UNION SELECT DISTINCT is_bulk FROM products"
                " UNION SELECT DISTINCT is_bulk FROM reviews"
            ).fetchall()
        assert all(b[0] == 1 for b in bulk)

    def test_direction_self(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AmazonAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            dirs = conn.execute(
                "SELECT DISTINCT direction FROM order_actions"
                " UNION SELECT DISTINCT direction FROM products"
                " UNION SELECT DISTINCT direction FROM reviews"
            ).fetchall()
        assert all(d[0] == "self" for d in dirs)

    def test_thread_nodes_created(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AmazonAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
            thread_nodes = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE kind = 'thread'"
            ).fetchone()[0]
        assert thread_nodes >= 1

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AmazonAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_ZIP, conn, settings)
        with connect(db_path) as conn:
            r2 = AmazonAdapter().run(FIXTURE_ZIP, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_inthread_triples_emitted(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = AmazonAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, settings)
            in_thread_id = conn.execute(
                "SELECT id FROM predicates WHERE name = 'inThread'"
            ).fetchone()[0]
            triple_count = conn.execute(
                "SELECT COUNT(*) FROM triples WHERE predicate_id = ?",
                (in_thread_id,),
            ).fetchone()[0]
        assert triple_count == report.rows_inserted
