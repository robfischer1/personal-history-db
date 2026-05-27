"""Tests for the DiscordChatExporter JSON format parser."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from phdb.formats.discord_dce_json import is_dce_json, parse_dce_export


def _write_dce_json(tmp_path: Path, messages: list[dict]) -> Path:
    """Write a minimal DCE JSON file and return its path."""
    data = {
        "guild": {"id": "1234", "name": "Test Guild"},
        "channel": {"id": "5678", "type": "DirectTextChannel", "name": "DM"},
        "dateRange": {"after": None, "before": None},
        "exportedAt": "2026-05-27T00:00:00Z",
        "messages": messages,
        "messageCount": len(messages),
    }
    path = tmp_path / "test_export.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


@pytest.fixture()
def two_message_file(tmp_path: Path) -> Path:
    return _write_dce_json(tmp_path, [
        {
            "id": "1001",
            "type": "Default",
            "timestamp": "2026-01-15T10:00:00+00:00",
            "content": "hey what's up",
            "author": {
                "id": "111",
                "name": "testowner42",
                "discriminator": "0000",
                "nickname": "Rob",
                "isBot": False,
            },
            "attachments": [],
        },
        {
            "id": "1002",
            "type": "Default",
            "timestamp": "2026-01-15T10:01:00+00:00",
            "content": "not much, you?",
            "author": {
                "id": "222",
                "name": "frienduser",
                "discriminator": "0000",
                "nickname": "Friend",
                "isBot": False,
            },
            "attachments": [],
        },
    ])


class TestIsDceJson:
    def test_valid_dce(self, two_message_file: Path) -> None:
        assert is_dce_json(two_message_file) is True

    def test_not_dce(self, tmp_path: Path) -> None:
        path = tmp_path / "other.json"
        path.write_text('{"something": "else"}', encoding="utf-8")
        assert is_dce_json(path) is False

    def test_nonexistent(self, tmp_path: Path) -> None:
        assert is_dce_json(tmp_path / "nope.json") is False


class TestDirectionBySnowflake:
    def test_direction_by_discord_id(self, two_message_file: Path) -> None:
        results = list(parse_dce_export(two_message_file, my_discord_id="111"))
        assert len(results) == 2
        assert results[0][0] == "outbound"
        assert results[1][0] == "inbound"

    def test_direction_by_discord_id_reversed(self, two_message_file: Path) -> None:
        results = list(parse_dce_export(two_message_file, my_discord_id="222"))
        assert results[0][0] == "inbound"
        assert results[1][0] == "outbound"


class TestDirectionByOwnerNames:
    def test_direction_by_handle(self, two_message_file: Path) -> None:
        results = list(parse_dce_export(
            two_message_file,
            owner_names={"testowner42"},
        ))
        assert len(results) == 2
        assert results[0][0] == "outbound"
        assert results[1][0] == "inbound"

    def test_direction_by_nickname(self, two_message_file: Path) -> None:
        results = list(parse_dce_export(
            two_message_file,
            owner_names={"rob"},
        ))
        assert results[0][0] == "outbound"
        assert results[1][0] == "inbound"

    def test_direction_unknown_when_no_identity(self, two_message_file: Path) -> None:
        results = list(parse_dce_export(two_message_file))
        assert all(d == "unknown" for d, _ in results)

    def test_snowflake_id_takes_precedence(self, two_message_file: Path) -> None:
        results = list(parse_dce_export(
            two_message_file,
            my_discord_id="222",
            owner_names={"testowner42"},
        ))
        assert results[0][0] == "inbound"
        assert results[1][0] == "outbound"


class TestMessageParsing:
    def test_message_fields(self, two_message_file: Path) -> None:
        results = list(parse_dce_export(two_message_file))
        _, msg = results[0]
        assert msg.platform_id == "discord:1001"
        assert msg.sender_address == "discord:testowner42"
        assert msg.sender_name == "Rob"
        assert msg.body_text == "hey what's up"
        assert msg.thread_key == "5678"
        assert msg.date_sent == "2026-01-15T10:00:00+00:00"

    def test_skips_empty_messages(self, tmp_path: Path) -> None:
        path = _write_dce_json(tmp_path, [
            {
                "id": "1003",
                "type": "Default",
                "timestamp": "2026-01-15T10:02:00+00:00",
                "content": "",
                "author": {"id": "111", "name": "a", "discriminator": "0000"},
                "attachments": [],
            },
        ])
        results = list(parse_dce_export(path))
        assert len(results) == 0

    def test_skips_system_messages(self, tmp_path: Path) -> None:
        path = _write_dce_json(tmp_path, [
            {
                "id": "1004",
                "type": "ChannelPinnedMessage",
                "timestamp": "2026-01-15T10:03:00+00:00",
                "content": "pinned a message",
                "author": {"id": "111", "name": "a", "discriminator": "0000"},
                "attachments": [],
            },
        ])
        results = list(parse_dce_export(path))
        assert len(results) == 0

    def test_attachment_only_message(self, tmp_path: Path) -> None:
        path = _write_dce_json(tmp_path, [
            {
                "id": "1005",
                "type": "Default",
                "timestamp": "2026-01-15T10:04:00+00:00",
                "content": "",
                "author": {"id": "111", "name": "a", "discriminator": "0000"},
                "attachments": [
                    {"url": "https://cdn.discord.com/img.png", "fileName": "img.png", "fileSizeBytes": 1024},
                ],
            },
        ])
        results = list(parse_dce_export(path))
        assert len(results) == 1
        _, msg = results[0]
        assert msg.has_attachments is True
        assert msg.attachment_count == 1
        assert msg.attachments[0].filename == "img.png"
        assert msg.attachments[0].content_type == "image/png"
        assert msg.attachments[0].size_bytes == 1024

    def test_discriminator_in_address(self, tmp_path: Path) -> None:
        path = _write_dce_json(tmp_path, [
            {
                "id": "1006",
                "type": "Default",
                "timestamp": "2026-01-15T10:05:00+00:00",
                "content": "old-style",
                "author": {"id": "111", "name": "olduser", "discriminator": "4321"},
                "attachments": [],
            },
        ])
        results = list(parse_dce_export(path))
        _, msg = results[0]
        assert msg.sender_address == "discord:olduser#4321"
