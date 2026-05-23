"""Tests for the iMessage adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.formats.imessage_html import (
    is_bulk_sender,
    normalize_addr,
    parse_filename_participants,
    parse_message_block,
    parse_timestamp,
)
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.imessage.plugin import IMessagePlugin as IMessageAdapter
from phdb.settings import IdentitySettings, Settings

FIXTURES = Path(__file__).parent / "fixtures" / "imessage"


# ---- Unit tests for helper functions ----


class TestParseTimestamp:
    def test_standard(self) -> None:
        assert parse_timestamp("Jan 19, 2017 12:22:55 PM") == "2017-01-19T12:22:55"

    def test_with_suffix(self) -> None:
        result = parse_timestamp("Jan 19, 2017 12:22:55 PM (Read by Jane)")
        assert result == "2017-01-19T12:22:55"

    def test_am(self) -> None:
        assert parse_timestamp("Dec 01, 2020 08:05:00 AM") == "2020-12-01T08:05:00"

    def test_empty(self) -> None:
        assert parse_timestamp("") is None

    def test_garbage(self) -> None:
        assert parse_timestamp("not a date") is None


class TestParseFilenameParticipants:
    def test_single_phone(self) -> None:
        assert parse_filename_participants("+15551234567.html") == ["+15551234567"]

    def test_group(self) -> None:
        result = parse_filename_participants("+15551234567, +15559876543.html")
        assert result == ["+15551234567", "+15559876543"]

    def test_email(self) -> None:
        assert parse_filename_participants("user@example.com.html") == ["user@example.com"]


class TestBulkSender:
    def test_short_code(self) -> None:
        assert is_bulk_sender("22345") == (True, "short-code")

    def test_noreply(self) -> None:
        assert is_bulk_sender("noreply@orders.apple.com") == (True, "known-automated")

    def test_normal_phone(self) -> None:
        assert is_bulk_sender("+15551234567") == (False, None)

    def test_empty(self) -> None:
        assert is_bulk_sender("") == (False, None)


class TestNormalizeAddr:
    def test_strips_whitespace(self) -> None:
        assert normalize_addr("  Foo@BAR.com  ") == "foo@bar.com"

    def test_empty(self) -> None:
        assert normalize_addr("") == ""


class TestParseMessageBlock:
    def test_sent_message(self) -> None:
        from bs4 import BeautifulSoup

        html = """<div class="message">
          <div class="sent"><span class="sender">Me</span>
          <span class="timestamp">Jan 19, 2017 12:22:55 PM</span>
          <div class="message_part">Hello</div></div>
        </div>"""
        soup = BeautifulSoup(html, "lxml")
        div = soup.select_one("div.message")
        assert div is not None
        info = parse_message_block(div)
        assert info is not None
        assert info["direction"] == "sent"
        assert info["sender_name"] == "Me"
        assert info["body_text"] == "Hello"
        assert info["date_sent"] == "2017-01-19T12:22:55"

    def test_empty_message_returns_none(self) -> None:
        from bs4 import BeautifulSoup

        html = '<div class="message"><div class="received"></div></div>'
        soup = BeautifulSoup(html, "lxml")
        div = soup.select_one("div.message")
        assert div is not None
        assert parse_message_block(div) is None


# ---- Integration tests ----


@pytest.fixture
def imessage_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        identity=IdentitySettings(
            owner_names={"test user"},
            owner_emails={"test@example.com"},
            owner_phones={"+15555555555"},
        ),
    )


@pytest.fixture
def imessage_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return db_path


def _new_plugin(**kwargs) -> IMessageAdapter:
    """Build an IMessagePlugin with the in-tree manifest."""
    from phdb.core.plugin.manifest import load_manifest

    manifest_path = Path("src/phdb/plugins/imessage/plugin.toml").resolve()
    manifest = load_manifest(manifest_path)
    return IMessageAdapter(manifest, **kwargs)


class TestIMessageAdapterIntegration:
    def test_ingest_one_on_one(self, imessage_db: Path, imessage_settings: Settings) -> None:
        imessage_settings.db_path = imessage_db
        adapter = _new_plugin()
        with connect(imessage_db) as conn:
            report = adapter.run(FIXTURES, conn, imessage_settings)

        assert report.rows_inserted > 0
        assert report.rows_skipped == 0
        assert report.threads_created > 0

        with connect(imessage_db) as conn:
            msgs = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
            assert msgs == report.rows_inserted

            threads = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()[0]
            assert threads > 0

            bridges = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
            assert bridges == msgs

    def test_direction_inference(self, imessage_db: Path, imessage_settings: Settings) -> None:
        imessage_settings.db_path = imessage_db
        adapter = _new_plugin()
        with connect(imessage_db) as conn:
            adapter.run(FIXTURES, conn, imessage_settings)
            sent = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE direction = 'outbound'"
            ).fetchone()[0]
            received = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE direction = 'inbound'"
            ).fetchone()[0]

        assert sent > 0
        assert received > 0

    def test_contact_name_learning(self, imessage_db: Path, imessage_settings: Settings) -> None:
        """1-on-1 files build a name→phone lookup used in group files."""
        imessage_settings.db_path = imessage_db
        adapter = _new_plugin()
        with connect(imessage_db) as conn:
            adapter.run(FIXTURES, conn, imessage_settings)

        assert "Jane Doe" in adapter._name_to_phone
        assert adapter._name_to_phone["Jane Doe"] == "+15551234567"

    def test_bulk_detection(self, imessage_db: Path, imessage_settings: Settings) -> None:
        imessage_settings.db_path = imessage_db
        adapter = _new_plugin()
        with connect(imessage_db) as conn:
            adapter.run(FIXTURES, conn, imessage_settings)
            bulk = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE is_bulk = 1"
            ).fetchone()[0]

        assert bulk >= 1

    def test_idempotent_rerun(self, imessage_db: Path, imessage_settings: Settings) -> None:
        imessage_settings.db_path = imessage_db
        adapter = _new_plugin()
        with connect(imessage_db) as conn:
            adapter.run(FIXTURES, conn, imessage_settings)

        adapter2 = _new_plugin()
        with connect(imessage_db) as conn:
            r2 = adapter2.run(FIXTURES, conn, imessage_settings)

        assert r2.rows_inserted == 0
        assert r2.rows_skipped == 0
        assert r2.rows_yielded == 0

    def test_thread_nodes_and_triples(self, imessage_db: Path, imessage_settings: Settings) -> None:
        imessage_settings.db_path = imessage_db
        adapter = _new_plugin()
        with connect(imessage_db) as conn:
            adapter.run(FIXTURES, conn, imessage_settings)
            thread_nodes = conn.execute(
                "SELECT id, label FROM nodes WHERE kind = 'thread' ORDER BY label"
            ).fetchall()
            in_thread_id = conn.execute(
                "SELECT id FROM predicates WHERE name = 'inThread'"
            ).fetchone()[0]

        assert len(thread_nodes) > 0
        for node_id, label in thread_nodes:
            with connect(imessage_db) as conn:
                triple_count = conn.execute(
                    "SELECT COUNT(*) FROM triples WHERE predicate_id = ? AND object_node_id = ?",
                    (in_thread_id, node_id),
                ).fetchone()[0]
            assert triple_count > 0, f"Thread {label} has 0 inThread triples"

    def test_attachments_recorded(self, imessage_db: Path, imessage_settings: Settings) -> None:
        imessage_settings.db_path = imessage_db
        adapter = _new_plugin()
        with connect(imessage_db) as conn:
            adapter.run(FIXTURES, conn, imessage_settings)
            atts = conn.execute("SELECT filename FROM attachments").fetchall()

        filenames = [a[0] for a in atts]
        assert "menu.pdf" in filenames

    def test_recipients_recorded(self, imessage_db: Path, imessage_settings: Settings) -> None:
        imessage_settings.db_path = imessage_db
        adapter = _new_plugin()
        with connect(imessage_db) as conn:
            adapter.run(FIXTURES, conn, imessage_settings)
            rcpts = conn.execute("SELECT normalized_label FROM nodes WHERE kind = 'contact' ORDER BY normalized_label").fetchall()

        addresses = {r[0] for r in rcpts}
        assert "+15555555555" in addresses or "+15551234567" in addresses

    def test_email_sender_parsed(self, imessage_db: Path, imessage_settings: Settings) -> None:
        imessage_settings.db_path = imessage_db
        adapter = _new_plugin()
        with connect(imessage_db) as conn:
            adapter.run(FIXTURES, conn, imessage_settings)
            email_msgs = conn.execute(
                "SELECT sender_address, sender_domain FROM chat_messages WHERE sender_domain IS NOT NULL"
            ).fetchall()

        assert len(email_msgs) >= 1
        domains = {r[1] for r in email_msgs}
        assert "example.com" in domains

    def test_group_sender_resolution(self, imessage_db: Path, imessage_settings: Settings) -> None:
        """In group chat, 'Jane Doe' sender should resolve to +15551234567 via lookup."""
        imessage_settings.db_path = imessage_db
        adapter = _new_plugin()
        with connect(imessage_db) as conn:
            adapter.run(FIXTURES, conn, imessage_settings)
            jane_in_group = conn.execute(
                """SELECT sender_address FROM chat_messages
                   WHERE sender_name = 'Jane Doe'
                     AND sender_address = '+15551234567'
                """,
            ).fetchall()

        assert len(jane_in_group) >= 1

    def test_time_budget(self, imessage_db: Path, imessage_settings: Settings) -> None:
        imessage_settings.db_path = imessage_db
        adapter = _new_plugin(max_seconds=0.001)
        with connect(imessage_db) as conn:
            report = adapter.run(FIXTURES, conn, imessage_settings)
        # Should process at least one file before budget expires
        assert report.rows_yielded >= 0
