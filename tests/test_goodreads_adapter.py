"""Tests for the goodreads plugin (Phase 7 brief 021 port).

Phase 7 of the phdb Plugin Architecture plan refactored goodreads from
the legacy ``phdb.adapters.goodreads`` module into a self-contained
``phdb.plugins.goodreads`` plugin under the new contract. Per Phase 0
Q14 (no shim), the legacy import path is broken; all callers use the
plugin's ``run()`` method now.

Test file kept under the old name (``test_goodreads_adapter.py``) for
git-history continuity; the contents target the new plugin.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.goodreads import GoodreadsPlugin
from phdb.settings import IdentitySettings, Settings

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "goodreads" / "goodreads_library.csv"


def _new_plugin() -> GoodreadsPlugin:
    """Build a GoodreadsPlugin with the in-tree manifest."""
    from phdb.core.plugin.manifest import load_manifest

    manifest_path = Path("src/phdb/plugins/goodreads/plugin.toml").resolve()
    manifest = load_manifest(manifest_path)
    return GoodreadsPlugin(manifest)


@pytest.fixture
def gr_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        identity=IdentitySettings(owner_names={"test user"}),
    )


@pytest.fixture
def gr_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return db_path


class TestGoodreadsIntegration:
    def test_basic_ingest(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = _new_plugin()
        with connect(gr_db) as conn:
            report = adapter.run(FIXTURE_CSV, conn, gr_settings)
        assert report.rows_inserted == 4
        assert report.rows_skipped == 0

    def test_empty_title_skipped(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = _new_plugin()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            titles = conn.execute("SELECT name FROM books").fetchall()
        title_list = [t[0] for t in titles]
        assert "" not in title_list
        assert None not in title_list

    def test_schema_type_book(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = _new_plugin()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM books").fetchall()
        assert all(t[0] == "Book" for t in types)

    def test_single_thread(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = _new_plugin()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]
        assert threads == 1

    def test_thread_key(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = _new_plugin()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            label = conn.execute(
                "SELECT label FROM nodes WHERE kind = 'thread'"
            ).fetchone()[0]
        assert "goodreads:library" in label

    def test_isbn_column(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = _new_plugin()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            row = conn.execute(
                "SELECT isbn FROM books WHERE name = 'To Kill a Mockingbird'"
            ).fetchone()
        assert row[0] == "0061120081"

    def test_missing_isbn(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = _new_plugin()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            row = conn.execute(
                "SELECT isbn, publisher FROM books WHERE name = 'Untitled Book'"
            ).fetchone()
        assert row[0] is None
        assert row[1] == "Self Published"

    def test_idempotent_rerun(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = _new_plugin()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)

        adapter2 = _new_plugin()
        with connect(gr_db) as conn:
            r2 = adapter2.run(FIXTURE_CSV, conn, gr_settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_bom_handling(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = _new_plugin()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            row = conn.execute(
                "SELECT name FROM books WHERE isbn = '0061120081'"
            ).fetchone()
        assert row is not None
        assert row[0] == "To Kill a Mockingbird"

    def test_message_thread_bridge(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = _new_plugin()
        with connect(gr_db) as conn:
            report = adapter.run(FIXTURE_CSV, conn, gr_settings)
            bridge = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
        assert bridge == report.rows_inserted
