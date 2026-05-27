"""DiscordChatExporter JSON format parser.

Source: per-channel ``.json`` files produced by DiscordChatExporter (Tyrrrz).
Each file wraps a ``messages`` array with guild/channel/dateRange metadata.
Unlike the Discord Data Package (``discord_json.py``), DCE captures **all
participants** — every message has an ``author`` object with id/name/nickname.

Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from phdb.records import Attachment, ChatMessage, Provenance, Recipient

_CONTENT_TYPES: dict[str, str] = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "heic": "image/heic",
    "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
    "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
    "pdf": "application/pdf", "txt": "text/plain", "json": "application/json",
    "zip": "application/zip",
}


def _content_type_from_filename(fn: str | None) -> str | None:
    if not fn:
        return None
    ext = fn.lower().rsplit(".", 1)[-1] if "." in fn else ""
    return _CONTENT_TYPES.get(ext)


def _filename_from_url(url: str) -> str | None:
    try:
        path = urlparse(url).path
        name = unquote(path.rsplit("/", 1)[-1])
        return name or None
    except Exception:
        return None


def is_dce_json(path: Path) -> bool:
    """Quick probe: does this JSON file look like DCE output?"""
    try:
        with open(path, encoding="utf-8") as f:
            head = f.read(2048)
        return '"guild"' in head and '"channel"' in head and '"messages"' in head
    except Exception:
        return False


def parse_channel_metadata(data: dict[str, Any]) -> dict[str, Any]:
    """Extract channel metadata from the DCE wrapper object."""
    guild = data.get("guild", {})
    channel = data.get("channel", {})
    return {
        "guild_id": guild.get("id"),
        "guild_name": guild.get("name"),
        "channel_id": channel.get("id"),
        "channel_type": channel.get("type"),
        "channel_name": channel.get("name"),
        "channel_topic": channel.get("topic"),
        "message_count": data.get("messageCount"),
    }


def parse_dce_export(
    path: Path,
    my_discord_id: str | None = None,
    owner_names: set[str] | None = None,
) -> Iterator[tuple[str, ChatMessage]]:
    """Yield ``(direction, ChatMessage)`` from one DCE JSON export file.

    Parameters
    ----------
    path
        Path to the ``.json`` file.
    my_discord_id
        The exporting user's Discord numeric ID (snowflake). Compared
        against each message's ``author.id`` for direction.
    owner_names
        Lowercase owner display names / handles. Used as fallback when
        ``my_discord_id`` is not set — compared against ``author.name``.
        If neither is set, direction is ``unknown``.
    """
    source_str = str(path)

    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    channel = data.get("channel", {})
    channel_id = channel.get("id", "")
    channel_type = channel.get("type", "")
    channel_name = channel.get("name", "")

    guild = data.get("guild", {})
    guild_name = guild.get("name")

    thread_key = str(channel_id)

    for msg in data.get("messages", []):
        snowflake = str(msg.get("id", ""))
        if not snowflake:
            continue

        msg_type = msg.get("type", "Default")
        if msg_type not in ("Default", "Reply", "SlashCommand", "ContextMenuCommand"):
            continue

        body = (msg.get("content") or "").strip()
        raw_attachments = msg.get("attachments", [])
        if not body and not raw_attachments:
            continue

        timestamp = msg.get("timestamp")

        author = msg.get("author", {})
        author_id = str(author.get("id", ""))
        author_name = author.get("nickname") or author.get("name") or ""
        author_discriminator = author.get("discriminator", "0000")
        author_handle = author.get("name", "")

        if author_discriminator and author_discriminator != "0000":
            sender_address = f"discord:{author_handle}#{author_discriminator}"
        else:
            sender_address = f"discord:{author_handle}" if author_handle else f"discord:user:{author_id}"

        direction = "unknown"
        if my_discord_id:
            direction = "outbound" if author_id == my_discord_id else "inbound"
        elif owner_names:
            name_lower = author_handle.lower()
            if name_lower in owner_names or author_name.lower() in owner_names:
                direction = "outbound"
            else:
                direction = "inbound"

        synthetic_id = f"discord:{snowflake}"
        raw_hash = hashlib.sha256(
            f"discord|{channel_id}|{snowflake}|{body[:200]}".encode()
        ).hexdigest()

        recipients: list[Recipient] = []
        if direction == "outbound" and channel_type in ("DirectTextChat", "DirectTextChannel"):
            recipients.append(Recipient(
                address=f"discord:{channel_name.lower()}" if channel_name else f"discord:channel:{channel_id}",
                name=channel_name or "",
                rtype="to",
            ))

        attachments: list[Attachment] = []
        for att in raw_attachments:
            url = att.get("url", "")
            fname = att.get("fileName") or _filename_from_url(url)
            ctype = _content_type_from_filename(fname)
            size_bytes = att.get("fileSizeBytes")
            attachments.append(Attachment(
                provenance=Provenance(
                    source_path=source_str,
                    raw_hash=raw_hash,
                ),
                parent_id=synthetic_id,
                filename=fname,
                content_type=ctype,
                content_disposition=None,
                size_bytes=int(size_bytes) if size_bytes is not None else None,
                on_disk_path=url,
                content_hash=None,
            ))

        yield direction, ChatMessage(
            provenance=Provenance(
                source_path=source_str,
                raw_hash=raw_hash,
                source_byte_offset=None,
                source_byte_length=None,
            ),
            sender_address=sender_address,
            sender_name=author_name,
            date_sent=timestamp or "",
            is_multipart=False,
            has_attachments=bool(raw_attachments),
            attachment_count=len(raw_attachments),
            platform_id=synthetic_id,
            body_text=body or None,
            thread_key=thread_key,
            recipients=tuple(recipients),
            attachments=tuple(attachments),
        )
