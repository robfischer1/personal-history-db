"""Tests for the sms_xml adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.adapters.sms_xml import (
    SmsXmlAdapter,
    _epoch_ms_to_iso,
    _normalize_phone,
)
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_XML = Path(__file__).parent / "fixtures" / "sms_xml" / "sms_backup.xml"


class TestNormalizePhone:
    def test_digits_only(self) -> None:
        assert _normalize_phone("+1 (555) 123-4567") == "+15551234567"

    def test_no_plus(self) -> None:
        assert _normalize_phone("5551234567") == "5551234567"

    def test_empty(self) -> None:
        assert _normalize_phone("") == ""

    def test_international(self) -> None:
        assert _normalize_phone("+442071234567") == "+442071234567"


class TestEpochMsToIso:
    def test_known(self) -> None:
        result = _epoch_ms_to_iso("1700000000000")
        assert result is not None
        assert "2023-11-14" in result

    def test_none(self) -> None:
        assert _epoch_ms_to_iso(None) is None

    def test_empty(self) -> None:
        assert _epoch_ms_to_iso("") is None

    def test_garbage(self) -> None:
        assert _epoch_ms_to_iso("not a number") is None


@pytest.fixture
def sms_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        identity=IdentitySettings(
            owner_names={"test user"},
            owner_phones={"+15555555555"},
        ),
    )


@pytest.fixture
def sms_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return db_path


class TestSmsXmlIntegration:
    def test_basic_ingest(self, sms_db: Path, sms_settings: Settings) -> None:
        sms_settings.db_path = sms_db
        adapter = SmsXmlAdapter()
        with connect(sms_db) as conn:
            report = adapter.run(FIXTURE_XML, conn, sms_settings)

        assert report.rows_inserted == 5
        assert report.rows_skipped == 0

    def test_sms_directions(self, sms_db: Path, sms_settings: Settings) -> None:
        sms_settings.db_path = sms_db
        adapter = SmsXmlAdapter()
        with connect(sms_db) as conn:
            adapter.run(FIXTURE_XML, conn, sms_settings)
            rows = conn.execute(
                "SELECT direction, body_text FROM chat_messages ORDER BY date_sent"
            ).fetchall()

        inbound = [r for r in rows if r[0] == "inbound"]
        outbound = [r for r in rows if r[0] == "outbound"]
        assert len(inbound) >= 2
        assert len(outbound) >= 1

    def test_null_body_skipped(self, sms_db: Path, sms_settings: Settings) -> None:
        sms_settings.db_path = sms_db
        adapter = SmsXmlAdapter()
        with connect(sms_db) as conn:
            adapter.run(FIXTURE_XML, conn, sms_settings)
            bodies = conn.execute("SELECT body_text FROM chat_messages").fetchall()

        for (body,) in bodies:
            assert body != "null"
            assert body is None or len(body) > 0

    def test_mms_with_attachment(self, sms_db: Path, sms_settings: Settings) -> None:
        sms_settings.db_path = sms_db
        adapter = SmsXmlAdapter()
        with connect(sms_db) as conn:
            adapter.run(FIXTURE_XML, conn, sms_settings)
            mms_rows = conn.execute(
                "SELECT body_text, has_attachments, attachment_count FROM chat_messages WHERE is_multipart = 1"
            ).fetchall()

        assert len(mms_rows) == 1
        assert mms_rows[0][0] == "MMS text content here"
        assert mms_rows[0][1] == 1
        assert mms_rows[0][2] == 1

    def test_group_sms_recipients(self, sms_db: Path, sms_settings: Settings) -> None:
        sms_settings.db_path = sms_db
        adapter = SmsXmlAdapter()
        with connect(sms_db) as conn:
            adapter.run(FIXTURE_XML, conn, sms_settings)
            # Find the message row for the group message
            msg = conn.execute(
                "SELECT id FROM chat_messages WHERE body_text = 'Group message test'"
            ).fetchone()
            assert msg is not None
            # Check sentTo triples for that message's record node
            sent_to_id = conn.execute(
                "SELECT id FROM predicates WHERE name = 'sentTo'"
            ).fetchone()[0]
            record_label = f"chat_messages:{msg[0]}"
            triples = conn.execute(
                "SELECT n2.normalized_label FROM triples t"
                " JOIN nodes n1 ON t.subject_node_id = n1.id"
                " JOIN nodes n2 ON t.object_node_id = n2.id"
                " WHERE t.predicate_id = ? AND n1.normalized_label = ?",
                (sent_to_id, record_label.lower()),
            ).fetchall()

        assert len(triples) == 1
        assert triples[0][0] == "+15551111111"

    def test_threads_created(self, sms_db: Path, sms_settings: Settings) -> None:
        sms_settings.db_path = sms_db
        adapter = SmsXmlAdapter()
        with connect(sms_db) as conn:
            report = adapter.run(FIXTURE_XML, conn, sms_settings)
            threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]

        assert threads >= 2
        assert report.threads_created >= 2

    def test_thread_keys(self, sms_db: Path, sms_settings: Settings) -> None:
        sms_settings.db_path = sms_db
        adapter = SmsXmlAdapter()
        with connect(sms_db) as conn:
            adapter.run(FIXTURE_XML, conn, sms_settings)
            labels = conn.execute(
                "SELECT label FROM nodes WHERE kind = 'thread' ORDER BY label"
            ).fetchall()

        label_set = {k[0] for k in labels}
        assert any("sms:+15551234567" in lbl for lbl in label_set)
        assert any("sms:+15559876543" in lbl for lbl in label_set)

    def test_idempotent_rerun(self, sms_db: Path, sms_settings: Settings) -> None:
        sms_settings.db_path = sms_db
        adapter = SmsXmlAdapter()
        with connect(sms_db) as conn:
            adapter.run(FIXTURE_XML, conn, sms_settings)

        adapter2 = SmsXmlAdapter()
        with connect(sms_db) as conn:
            r2 = adapter2.run(FIXTURE_XML, conn, sms_settings)
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded

    def test_schema_type(self, sms_db: Path, sms_settings: Settings) -> None:
        sms_settings.db_path = sms_db
        adapter = SmsXmlAdapter()
        with connect(sms_db) as conn:
            adapter.run(FIXTURE_XML, conn, sms_settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM chat_messages").fetchall()

        assert all(t[0] == "Message" for t in types)

    def test_message_thread_bridge(self, sms_db: Path, sms_settings: Settings) -> None:
        sms_settings.db_path = sms_db
        adapter = SmsXmlAdapter()
        with connect(sms_db) as conn:
            report = adapter.run(FIXTURE_XML, conn, sms_settings)
            bridge = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
        assert bridge == report.rows_inserted
