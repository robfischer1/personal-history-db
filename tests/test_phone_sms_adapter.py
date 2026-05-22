"""Tests for the phone_sms adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.phone_sms import PhoneSmsAdapter
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_DB = Path(__file__).parent / "fixtures" / "phone_sms" / "mmssms.db"


def _setup(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(
            owner_names={"test user"},
            owner_phones={"+15559876543"},
        ),
    )
    return db_path, settings


class TestPhoneSmsIntegration:
    def test_basic_ingest(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhoneSmsAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DB, conn, settings)
        assert report.rows_inserted >= 3

    def test_schema_type(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhoneSmsAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DB, conn, settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM chat_messages").fetchall()
        assert all(t[0] == "Message" for t in types)

    def test_direction_inference(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhoneSmsAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DB, conn, settings)
            inbound = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE direction = 'inbound'"
            ).fetchone()[0]
            outbound = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE direction = 'outbound'"
            ).fetchone()[0]
        assert inbound >= 1
        assert outbound >= 1

    def test_thread_per_phone(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhoneSmsAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DB, conn, settings)
            threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]
        assert threads >= 1

    def test_mms_with_attachment(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhoneSmsAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DB, conn, settings)
            mms = conn.execute(
                "SELECT body_text FROM chat_messages WHERE body_text_source = 'phone-mms'"
            ).fetchall()
        assert len(mms) >= 1
        assert "photo" in mms[0][0].lower() or "attachment" in mms[0][0].lower()

    def test_idempotent_rerun(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhoneSmsAdapter()
        with connect(db_path) as conn:
            adapter.run(FIXTURE_DB, conn, settings)
        with connect(db_path) as conn:
            r2 = PhoneSmsAdapter().run(FIXTURE_DB, conn, settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_message_thread_bridge(self, tmp_path: Path) -> None:
        db_path, settings = _setup(tmp_path)
        adapter = PhoneSmsAdapter()
        with connect(db_path) as conn:
            report = adapter.run(FIXTURE_DB, conn, settings)
            bridge = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
        assert bridge == report.rows_inserted
