"""Tests for the chat_logs adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.plugins.chat_logs.plugin import ChatLogsPlugin
from phdb.core.plugin.manifest import PluginManifest
from phdb.formats.chat_logs_text import (
    _combine_date_and_time,
    _html_unescape,
    _normalize_handle,
    _parse_session_handle,
    _parse_session_timestamp,
    _strip_html_tags,
    _strip_msn_color_codes,
    detect_format,
    infer_filename_date,
    parse_file,
)
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURES = Path(__file__).parent / "fixtures" / "chat_logs"


@pytest.fixture
def chat_plugin() -> ChatLogsPlugin:
    manifest = PluginManifest(
        name="chat_logs",
        version="0.1.0",
        description="test",
        kind="source",
        entry_point="phdb.plugins.chat_logs:ChatLogsPlugin",
    )
    return ChatLogsPlugin(manifest)


# ---- Unit tests for helper functions ----


class TestNormalizeHandle:
    def test_basic(self) -> None:
        assert _normalize_handle("GuitarFreak63") == "guitarfreak63"

    def test_mailto(self) -> None:
        assert _normalize_handle("mailto:test@example.com") == "test@example.com"

    def test_none(self) -> None:
        assert _normalize_handle(None) is None


class TestParseSessionHandle:
    def test_full_trillian(self) -> None:
        proto, my, remote = _parse_session_handle("MSN - my@msn.com:friend@msn.com")
        assert proto == "msn"
        assert my == "my@msn.com"
        assert remote == "friend@msn.com"

    def test_no_proto(self) -> None:
        proto, my, remote = _parse_session_handle("JaneDoe")
        assert proto is None
        assert remote == "janedoe"

    def test_none(self) -> None:
        assert _parse_session_handle(None) == (None, None, None)


class TestParseSessionTimestamp:
    def test_standard(self) -> None:
        assert _parse_session_timestamp("Mon Jul 14 14:13:30 2003") == "2003-07-14T14:13:30"

    def test_date_only(self) -> None:
        assert _parse_session_timestamp("Tuesday, November 18, 2003") == "2003-11-18"

    def test_none(self) -> None:
        assert _parse_session_timestamp(None) is None


class TestHtmlHelpers:
    def test_unescape(self) -> None:
        assert _html_unescape("&amp; &lt; &gt;") == "& < >"

    def test_strip_tags(self) -> None:
        assert _strip_html_tags("<b>Hello</b> <i>world</i>") == "Hello world"


class TestStripMsnColors:
    def test_basic(self) -> None:
        result = _strip_msn_color_codes("Hello\x03(0, 128, 255) world")
        assert "Hello" in result
        assert "world" in result
        assert "\x03" not in result


class TestFilenameDate:
    def test_standard(self) -> None:
        d = infer_filename_date(Path("chat/2003-07-14 [Monday].htm"))
        assert d is not None
        assert d.year == 2003
        assert d.month == 7
        assert d.day == 14

    def test_month_year(self) -> None:
        d = infer_filename_date(Path("chat/July 2003.txt"))
        assert d is not None
        assert d.year == 2003
        assert d.month == 7


class TestCombineDateAndTime:
    def test_pm(self) -> None:
        from datetime import datetime

        d = datetime(2003, 7, 14)
        result = _combine_date_and_time(d, "5:03:16 PM")
        assert result == "2003-07-14T17:03:16"

    def test_am(self) -> None:
        from datetime import datetime

        d = datetime(2003, 7, 14)
        result = _combine_date_and_time(d, "9:30:00 AM")
        assert result == "2003-07-14T09:30:00"

    def test_no_date(self) -> None:
        assert _combine_date_and_time(None, "5:03:16 PM") is None


class TestDetectFormat:
    def test_aim_html(self) -> None:
        assert detect_format(Path("test.htm"), b"<HTML><BODY>") == "aim_html"

    def test_plaintext(self) -> None:
        assert detect_format(Path("test.txt"), b"Session Start (MSN - a:b): Mon Jul 14") == "plaintext"

    def test_bracketed(self) -> None:
        assert detect_format(Path("test.log"), b"[14:30] User: hello\n[14:31] Other: hi") == "bracketed_time"


class TestParseAimHtml:
    def test_basic(self) -> None:
        file_path = FIXTURES / "AIM" / "TestUser" / "Friend1" / "2003-07-14 [Monday].htm"
        sessions = parse_file(file_path, FIXTURES)
        assert len(sessions) == 1
        result = sessions[0]
        assert result.protocol == "aim"
        assert result.my_handle == "testuser"
        assert result.remote_handle == "friend1"
        assert len(result.messages) == 4


class TestParsePlaintextLog:
    def test_multi_session(self) -> None:
        file_path = FIXTURES / "MSN" / "msn_chat.txt"
        sessions = parse_file(file_path, FIXTURES)
        assert len(sessions) == 2
        assert len(sessions[0].messages) == 3
        assert len(sessions[1].messages) == 4


class TestParseBracketedTimeLog:
    def test_basic(self) -> None:
        file_path = FIXTURES / "MSN" / "bracketed_2003-08-01.log"
        sessions = parse_file(file_path, FIXTURES)
        assert len(sessions) == 1
        assert len(sessions[0].messages) == 4
        assert "multi-line" in (sessions[0].messages[2].body_text or "")
        assert "continuation" in (sessions[0].messages[2].body_text or "")


# ---- Integration tests ----


@pytest.fixture
def chat_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        identity=IdentitySettings(
            owner_names={"testuser", "testuser@example.com"},
            owner_emails={"testuser@example.com"},
            owner_phones=set(),
            owner_handles={"aim": {"testuser"}, "msn": {"testuser@example.com"}},
        ),
    )


@pytest.fixture
def chat_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return db_path


class TestChatLogsPluginIntegration:
    def test_basic_ingest(self, chat_db: Path, chat_settings: Settings, chat_plugin: ChatLogsPlugin) -> None:
        chat_settings.db_path = chat_db
        with connect(chat_db) as conn:
            report = chat_plugin.run(FIXTURES, conn, chat_settings)

        assert report.rows_inserted > 0

    def test_aim_html_parsed(self, chat_db: Path, chat_settings: Settings, chat_plugin: ChatLogsPlugin) -> None:
        chat_settings.db_path = chat_db
        with connect(chat_db) as conn:
            report = chat_plugin.run(FIXTURES, conn, chat_settings)
            aim_msgs = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE body_text_source = 'chat-log'"
            ).fetchone()[0]
        assert aim_msgs == report.rows_inserted

    def test_direction_inference(self, chat_db: Path, chat_settings: Settings, chat_plugin: ChatLogsPlugin) -> None:
        chat_settings.db_path = chat_db
        with connect(chat_db) as conn:
            chat_plugin.run(FIXTURES, conn, chat_settings)
            outbound = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE direction = 'outbound'"
            ).fetchone()[0]
            inbound = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE direction = 'inbound'"
            ).fetchone()[0]
        assert outbound > 0
        assert inbound > 0

    def test_threads_created(self, chat_db: Path, chat_settings: Settings, chat_plugin: ChatLogsPlugin) -> None:
        chat_settings.db_path = chat_db
        with connect(chat_db) as conn:
            chat_plugin.run(FIXTURES, conn, chat_settings)
            thread_nodes = conn.execute(
                "SELECT label FROM nodes WHERE kind = 'thread' ORDER BY label"
            ).fetchall()
        assert len(thread_nodes) >= 3

    def test_recipients_recorded(self, chat_db: Path, chat_settings: Settings, chat_plugin: ChatLogsPlugin) -> None:
        chat_settings.db_path = chat_db
        with connect(chat_db) as conn:
            chat_plugin.run(FIXTURES, conn, chat_settings)
            rcpts = conn.execute("SELECT normalized_label FROM nodes WHERE kind = 'contact' ORDER BY normalized_label").fetchall()
        assert len(rcpts) >= 1

    def test_idempotent_rerun(self, chat_db: Path, chat_settings: Settings, chat_plugin: ChatLogsPlugin) -> None:
        chat_settings.db_path = chat_db
        with connect(chat_db) as conn:
            chat_plugin.run(FIXTURES, conn, chat_settings)

        with connect(chat_db) as conn:
            r2 = chat_plugin.run(FIXTURES, conn, chat_settings)

        assert r2.rows_inserted == 0
        assert r2.rows_yielded == 0

    def test_thread_nodes_exist(self, chat_db: Path, chat_settings: Settings, chat_plugin: ChatLogsPlugin) -> None:
        chat_settings.db_path = chat_db
        with connect(chat_db) as conn:
            chat_plugin.run(FIXTURES, conn, chat_settings)
            thread_count = conn.execute(
                "SELECT COUNT(*) FROM nodes WHERE kind = 'thread'"
            ).fetchone()[0]
        assert thread_count >= 1

    def test_message_thread_bridge(self, chat_db: Path, chat_settings: Settings, chat_plugin: ChatLogsPlugin) -> None:
        chat_settings.db_path = chat_db
        with connect(chat_db) as conn:
            chat_plugin.run(FIXTURES, conn, chat_settings)
            bridges = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
            msgs = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        assert bridges == msgs

    def test_time_budget(self, chat_db: Path, chat_settings: Settings) -> None:
        chat_settings.db_path = chat_db
        manifest = PluginManifest(
            name="chat_logs",
            version="0.1.0",
            description="test",
            kind="source",
            entry_point="phdb.plugins.chat_logs:ChatLogsPlugin",
        )
        plugin = ChatLogsPlugin(manifest, max_seconds=0.001)
        with connect(chat_db) as conn:
            report = plugin.run(FIXTURES, conn, chat_settings)
        assert report.rows_yielded >= 0

    def test_multi_session_file(self, chat_db: Path, chat_settings: Settings, chat_plugin: ChatLogsPlugin) -> None:
        """The MSN plaintext file has 2 sessions — both should create separate threads."""
        chat_settings.db_path = chat_db
        with connect(chat_db) as conn:
            chat_plugin.run(FIXTURES, conn, chat_settings)
            msn_threads = conn.execute(
                "SELECT label FROM nodes WHERE kind = 'thread' AND label LIKE '%msn:%'"
            ).fetchall()
        assert len(msn_threads) == 2

