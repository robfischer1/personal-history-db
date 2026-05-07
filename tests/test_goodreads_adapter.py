"""Tests for the goodreads adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.adapters.goodreads import GoodreadsAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "goodreads" / "goodreads_library.csv"


@pytest.fixture
def gr_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        identity=IdentitySettings(owner_names={"test user"}),
    )


@pytest.fixture
def gr_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return db_path


class TestGoodreadsIntegration:
    def test_basic_ingest(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = GoodreadsAdapter()
        with connect(gr_db) as conn:
            report = adapter.run(FIXTURE_CSV, conn, gr_settings)
        assert report.rows_inserted == 4
        assert report.rows_skipped == 0

    def test_empty_title_skipped(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = GoodreadsAdapter()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            titles = conn.execute("SELECT subject FROM messages").fetchall()
        title_list = [t[0] for t in titles]
        assert "" not in title_list
        assert None not in title_list

    def test_schema_type_book(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = GoodreadsAdapter()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()
        assert all(t[0] == "Book" for t in types)

    def test_single_thread(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = GoodreadsAdapter()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        assert threads == 1

    def test_thread_key(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = GoodreadsAdapter()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            key = conn.execute("SELECT thread_key FROM threads").fetchone()[0]
        assert key == "goodreads:library"

    def test_isbn_as_sender_address(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = GoodreadsAdapter()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            addrs = conn.execute(
                "SELECT sender_address FROM messages WHERE subject = 'To Kill a Mockingbird'"
            ).fetchone()
        assert addrs[0] == "0061120081"

    def test_missing_isbn(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = GoodreadsAdapter()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            row = conn.execute(
                "SELECT sender_address, sender_name FROM messages WHERE subject = 'Untitled Book'"
            ).fetchone()
        assert row[0] is None
        assert row[1] == "Self Published"

    def test_idempotent_rerun(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = GoodreadsAdapter()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)

        adapter2 = GoodreadsAdapter()
        with connect(gr_db) as conn:
            r2 = adapter2.run(FIXTURE_CSV, conn, gr_settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_bom_handling(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = GoodreadsAdapter()
        with connect(gr_db) as conn:
            adapter.run(FIXTURE_CSV, conn, gr_settings)
            row = conn.execute(
                "SELECT subject FROM messages WHERE sender_address = '0061120081'"
            ).fetchone()
        assert row is not None
        assert row[0] == "To Kill a Mockingbird"

    def test_message_thread_bridge(self, gr_db: Path, gr_settings: Settings) -> None:
        gr_settings.db_path = gr_db
        adapter = GoodreadsAdapter()
        with connect(gr_db) as conn:
            report = adapter.run(FIXTURE_CSV, conn, gr_settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted
