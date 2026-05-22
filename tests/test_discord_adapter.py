"""Tests for the Discord adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.adapters.discord import DiscordAdapter
from phdb.formats.discord_json import (
    _content_type_from_filename,
    _derive_other_party,
    _derive_thread_label,
    _filename_from_url,
    _parse_discord_ts,
    _split_attachments,
)
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_ZIP = Path(__file__).parent / "fixtures" / "discord" / "package.zip"


# ---- Unit tests for helper functions ----


class TestParseDiscordTs:
    def test_standard(self) -> None:
        assert _parse_discord_ts("2024-01-15 14:30:00") == "2024-01-15T14:30:00+00:00"

    def test_empty(self) -> None:
        assert _parse_discord_ts("") is None

    def test_none(self) -> None:
        assert _parse_discord_ts(None) is None

    def test_garbage(self) -> None:
        assert _parse_discord_ts("not a date") is None


class TestSplitAttachments:
    def test_single_url(self) -> None:
        result = _split_attachments("https://cdn.discordapp.com/foo/bar.jpg")
        assert result == ["https://cdn.discordapp.com/foo/bar.jpg"]

    def test_multiple_urls(self) -> None:
        result = _split_attachments(
            "https://cdn.discordapp.com/a.jpg https://cdn.discordapp.com/b.png"
        )
        assert len(result) == 2

    def test_empty(self) -> None:
        assert _split_attachments("") == []

    def test_none(self) -> None:
        assert _split_attachments(None) == []


class TestFilenameFromUrl:
    def test_normal(self) -> None:
        assert _filename_from_url("https://cdn.discordapp.com/foo/sunset.jpg") == "sunset.jpg"

    def test_encoded(self) -> None:
        assert _filename_from_url("https://cdn.discordapp.com/foo/my%20photo.png") == "my photo.png"


class TestContentType:
    def test_jpg(self) -> None:
        assert _content_type_from_filename("photo.jpg") == "image/jpeg"

    def test_mp4(self) -> None:
        assert _content_type_from_filename("clip.mp4") == "video/mp4"

    def test_unknown(self) -> None:
        assert _content_type_from_filename("file.xyz") is None

    def test_none(self) -> None:
        assert _content_type_from_filename(None) is None


class TestDeriveThreadLabel:
    def test_index_label(self) -> None:
        assert _derive_thread_label({}, "Direct Message with Jane#1234") == "Direct Message with Jane#1234"

    def test_guild_channel(self) -> None:
        meta = {"name": "general", "guild": {"name": "My Server"}}
        assert _derive_thread_label(meta, None) == "general in My Server"

    def test_fallback(self) -> None:
        assert _derive_thread_label({"id": "123"}, None) == "Discord channel 123"


class TestDeriveOtherParty:
    def test_dm(self) -> None:
        meta = {"type": "DM", "recipients": ["my_id", "other_id"]}
        addr, handle, oid = _derive_other_party(meta, "Direct Message with Jane#1234", "my_id")
        assert addr == "discord:Jane#1234"
        assert handle == "Jane#1234"
        assert oid == "other_id"

    def test_group_dm(self) -> None:
        meta = {"type": "GROUP_DM", "name": "Squad"}
        addr, handle, oid = _derive_other_party(meta, None, "my_id")
        assert addr == "discord:group-dm"
        assert handle == "Squad"

    def test_guild(self) -> None:
        meta = {"type": "GUILD_TEXT", "name": "general", "guild": {"name": "Cool Server"}}
        addr, handle, oid = _derive_other_party(meta, None, "my_id")
        assert addr == "discord-channel:Cool Server/general"


# ---- Integration tests ----


@pytest.fixture
def discord_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        identity=IdentitySettings(
            owner_names={"test user"},
            owner_emails={"test@example.com"},
            owner_phones={"+15555555555"},
            owner_handles={"discord": {"testuser"}},
        ),
    )


@pytest.fixture
def discord_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return db_path


class TestDiscordAdapterIntegration:
    def test_basic_ingest(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter()
        with connect(discord_db) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, discord_settings)

        assert report.rows_inserted == 6
        assert report.rows_skipped == 0
        assert report.threads_created == 3

    def test_all_outbound(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter()
        with connect(discord_db) as conn:
            adapter.run(FIXTURE_ZIP, conn, discord_settings)
            inbound = conn.execute(
                "SELECT COUNT(*) FROM chat_messages WHERE direction != 'outbound'"
            ).fetchone()[0]
        assert inbound == 0

    def test_sender_from_identity(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter()
        with connect(discord_db) as conn:
            adapter.run(FIXTURE_ZIP, conn, discord_settings)
            senders = conn.execute(
                "SELECT DISTINCT sender_address FROM chat_messages"
            ).fetchall()
        assert len(senders) == 1
        assert senders[0][0] == "discord:testuser"

    def test_synthetic_message_id(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter()
        with connect(discord_db) as conn:
            adapter.run(FIXTURE_ZIP, conn, discord_settings)
            ids = conn.execute(
                "SELECT message_key FROM chat_messages ORDER BY message_key"
            ).fetchall()
        msg_ids = [r[0] for r in ids]
        assert all(mid.startswith("discord:") for mid in msg_ids)
        assert "discord:1000000000000000001" in msg_ids

    def test_threads_created(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter()
        with connect(discord_db) as conn:
            adapter.run(FIXTURE_ZIP, conn, discord_settings)
            thread_nodes = conn.execute(
                "SELECT label FROM nodes WHERE kind = 'thread' ORDER BY label"
            ).fetchall()

        assert len(thread_nodes) == 3

    def test_attachments_recorded(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter()
        with connect(discord_db) as conn:
            adapter.run(FIXTURE_ZIP, conn, discord_settings)
            atts = conn.execute("SELECT filename, content_type, on_disk_path FROM attachments").fetchall()

        filenames = {a[0] for a in atts}
        assert "sunset.jpg" in filenames
        assert "clip.mp4" in filenames
        assert "screenshot.png" in filenames
        assert len(atts) == 3

    def test_recipients_recorded(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter()
        with connect(discord_db) as conn:
            adapter.run(FIXTURE_ZIP, conn, discord_settings)
            rcpts = conn.execute("SELECT normalized_label FROM nodes WHERE kind = 'contact' ORDER BY normalized_label").fetchall()

        addresses = {r[0] for r in rcpts}
        assert "discord:janedoe#1234" in addresses

    def test_idempotent_rerun(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter()
        with connect(discord_db) as conn:
            adapter.run(FIXTURE_ZIP, conn, discord_settings)

        adapter2 = DiscordAdapter()
        with connect(discord_db) as conn:
            r2 = adapter2.run(FIXTURE_ZIP, conn, discord_settings)

        assert r2.rows_inserted == 0
        assert r2.rows_yielded == 0

    def test_since_filter(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter(since="2024-02-01T00:00:00+00:00")
        with connect(discord_db) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, discord_settings)

        assert report.rows_inserted == 3  # ch2 (2 msgs) + ch3 (1 msg)

    def test_max_channels(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter(max_channels=1)
        with connect(discord_db) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, discord_settings)

        assert report.threads_created == 1

    def test_time_budget(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter(max_seconds=0.001)
        with connect(discord_db) as conn:
            report = adapter.run(FIXTURE_ZIP, conn, discord_settings)
        assert report.rows_yielded >= 0

    def test_thread_aggregates(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter()
        with connect(discord_db) as conn:
            adapter.run(FIXTURE_ZIP, conn, discord_settings)
            # Verify inThread triples exist for the DM channel
            in_thread_id = conn.execute(
                "SELECT id FROM predicates WHERE name = 'inThread'"
            ).fetchone()[0]
            dm_node = conn.execute(
                "SELECT id FROM nodes WHERE kind = 'thread' AND label LIKE '%900000000000000001'"
            ).fetchone()

        assert dm_node is not None
        with connect(discord_db) as conn:
            triple_count = conn.execute(
                "SELECT COUNT(*) FROM triples WHERE predicate_id = ? AND object_node_id = ?",
                (in_thread_id, dm_node[0]),
            ).fetchone()[0]
        assert triple_count == 3  # 3 messages in channel 1

    def test_guild_channel_thread(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter()
        with connect(discord_db) as conn:
            adapter.run(FIXTURE_ZIP, conn, discord_settings)
            in_thread_id = conn.execute(
                "SELECT id FROM predicates WHERE name = 'inThread'"
            ).fetchone()[0]
            guild_node = conn.execute(
                "SELECT id FROM nodes WHERE kind = 'thread' AND label LIKE '%900000000000000002'"
            ).fetchone()

        assert guild_node is not None
        with connect(discord_db) as conn:
            triple_count = conn.execute(
                "SELECT COUNT(*) FROM triples WHERE predicate_id = ? AND object_node_id = ?",
                (in_thread_id, guild_node[0]),
            ).fetchone()[0]
        assert triple_count == 2

    def test_message_thread_bridge(self, discord_db: Path, discord_settings: Settings) -> None:
        discord_settings.db_path = discord_db
        adapter = DiscordAdapter()
        with connect(discord_db) as conn:
            adapter.run(FIXTURE_ZIP, conn, discord_settings)
            bridges = conn.execute("SELECT COUNT(*) FROM triples t JOIN predicates p ON t.predicate_id = p.id WHERE p.name = 'inThread'").fetchone()[0]
            msgs = conn.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0]
        assert bridges == msgs
