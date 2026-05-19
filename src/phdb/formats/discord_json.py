"""Discord JSON format parser — yields ChatMessage records from Discord data-export zips.

Source: a single ``package.zip`` produced by Discord's "Request my Data" export.

Only the ``Messages/`` tree is parsed:
  Messages/index.json               -- {channel_id: human_label}
  Messages/c<channel_id>/channel.json   -- channel metadata (type/guild/recipients)
  Messages/c<channel_id>/messages.json  -- list of {ID, Timestamp, Contents, Attachments}

Pure parser: no DB, no identity.
"""

from __future__ import annotations

import hashlib
import json
import re
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

from phdb.records import Attachment, ChatMessage, Provenance, Recipient

# ---------------------------------------------------------------------------
# Helpers (pure, no DB, no identity)
# ---------------------------------------------------------------------------

_CONTENT_TYPES: dict[str, str] = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "heic": "image/heic",
    "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm",
    "mp3": "audio/mpeg", "wav": "audio/wav", "m4a": "audio/mp4",
    "pdf": "application/pdf", "txt": "text/plain", "json": "application/json",
    "zip": "application/zip",
}


def _parse_discord_ts(ts_str: str | None) -> str | None:
    if not ts_str:
        return None
    try:
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
        return dt.isoformat()
    except ValueError:
        return None


def _split_attachments(att_field: str | None) -> list[str]:
    if not att_field:
        return []
    return [u.strip() for u in att_field.split() if u.strip().startswith("http")]


def _filename_from_url(url: str) -> str | None:
    try:
        path = urlparse(url).path
        name = unquote(path.rsplit("/", 1)[-1])
        return name or None
    except Exception:
        return None


def _content_type_from_filename(fn: str | None) -> str | None:
    if not fn:
        return None
    ext = fn.lower().rsplit(".", 1)[-1] if "." in fn else ""
    return _CONTENT_TYPES.get(ext)


def _derive_thread_label(
    channel_meta: dict[str, object], index_label: str | None
) -> str:
    if index_label:
        return index_label
    guild = channel_meta.get("guild")
    name = channel_meta.get("name")
    if isinstance(guild, dict) and name:
        return f'{name} in {guild.get("name", "?")}'
    if name:
        return str(name)
    return f'Discord channel {channel_meta.get("id", "?")}'


def _derive_other_party(
    channel_meta: dict[str, object],
    index_label: str | None,
    my_user_id: str | None,
) -> tuple[str, str | None, str | None]:
    """Return (address, handle, other_id) for the other side of the conversation."""
    ctype = channel_meta.get("type", "")

    if ctype == "DM":
        recips = channel_meta.get("recipients", [])
        other_id = None
        if isinstance(recips, list):
            other_id = next((r for r in recips if r != my_user_id), None)
        m = re.match(r"Direct Message with (.+?)\s*$", index_label or "")
        handle = m.group(1).strip() if m else None
        addr = (
            f"discord:{handle}" if handle
            else (f"discord:user:{other_id}" if other_id else "discord:unknown")
        )
        return addr, handle, str(other_id) if other_id else None

    if ctype == "GROUP_DM":
        name = channel_meta.get("name")
        return "discord:group-dm", str(name) if name else "Group DM", None

    guild = channel_meta.get("guild")
    if isinstance(guild, dict):
        gname = guild.get("name", "?")
        cname = channel_meta.get("name") or channel_meta.get("id", "?")
        return f"discord-channel:{gname}/{cname}", f"#{cname} ({gname})", None

    cid = channel_meta.get("id", "unknown")
    return f"discord:{cid}", None, None


# ---------------------------------------------------------------------------
# Per-channel parser
# ---------------------------------------------------------------------------

def parse_channel(
    zf: zipfile.ZipFile,
    channel_id: str,
    channel_meta: dict,
    index_label: str | None,
    my_user_id: str | None,
) -> Iterator[ChatMessage]:
    """Yield ChatMessage records for one Discord channel.

    Parameters
    ----------
    zf : zipfile.ZipFile
        The open Discord export zip.
    channel_id : str
        Numeric channel snowflake ID.
    channel_meta : dict
        Parsed ``channel.json`` for this channel.
    index_label : str | None
        Human label from ``Messages/index.json`` (may be None).
    my_user_id : str | None
        The exporting user's Discord numeric ID from ``Account/user.json``.
        Used only to determine recipient vs. sender in DMs; not used for
        identity resolution.
    """
    source_str = str(zf.filename) if zf.filename else ""

    try:
        msgs = json.loads(zf.read(f"Messages/c{channel_id}/messages.json"))
    except (KeyError, json.JSONDecodeError):
        return

    if not msgs:
        return

    other_addr, other_handle, _other_id = _derive_other_party(
        channel_meta, index_label, my_user_id,
    )
    thread_key = channel_id

    for msg_idx, msg in enumerate(msgs):
        snowflake = str(msg.get("ID", ""))
        if not snowflake:
            continue

        body = (msg.get("Contents") or "").strip()
        att_urls = _split_attachments(msg.get("Attachments"))
        if not body and not att_urls:
            continue

        ts = _parse_discord_ts(msg.get("Timestamp"))

        synthetic_id = f"discord:{snowflake}"
        raw_hash = hashlib.sha256(
            f"discord|{channel_id}|{snowflake}|{body[:200]}".encode()
        ).hexdigest()

        recipients: list[Recipient] = []
        if other_addr:
            recipients.append(Recipient(
                address=other_addr,
                name=other_handle or "",
                rtype="to",
            ))

        attachments: list[Attachment] = []
        for url in att_urls:
            fname = _filename_from_url(url)
            ctype = _content_type_from_filename(fname)
            attachments.append(Attachment(
                provenance=Provenance(
                    source_path=source_str,
                    raw_hash=raw_hash,
                ),
                parent_id=synthetic_id,
                filename=fname,
                content_type=ctype,
                content_disposition=None,
                size_bytes=None,
                on_disk_path=url,
                content_hash=None,
            ))

        # sender_address is "discord:self" as a neutral placeholder —
        # the adapter maps this to the identity-resolved address.
        yield ChatMessage(
            provenance=Provenance(
                source_path=source_str,
                raw_hash=raw_hash,
                source_byte_offset=None,
                source_byte_length=None,
            ),
            sender_address="discord:self",
            date_sent=ts or "",
            is_multipart=False,
            has_attachments=bool(att_urls),
            attachment_count=len(att_urls),
            platform_id=synthetic_id,
            sender_name=None,
            body_text=body or None,
            thread_key=thread_key,
            recipients=tuple(recipients),
            attachments=tuple(attachments),
        )


# ---------------------------------------------------------------------------
# Whole-export parser
# ---------------------------------------------------------------------------

def parse_export(
    source_path: Path,
) -> Iterator[tuple[str, dict, ChatMessage]]:
    """Yield ``(channel_id, channel_meta, record)`` for every message in the export.

    Opens the zip, reads the index + user metadata, and delegates to
    :func:`parse_channel` per channel.
    """
    with zipfile.ZipFile(source_path) as zf:
        # Read the exporting user's ID (for DM recipient logic)
        my_user_id: str | None = None
        try:
            user_data = json.loads(zf.read("Account/user.json"))
            my_user_id = user_data.get("id")
        except (KeyError, json.JSONDecodeError):
            pass

        # Read the channel index
        try:
            index: dict[str, str] = json.loads(zf.read("Messages/index.json"))
        except (KeyError, json.JSONDecodeError):
            index = {}

        # Discover channels with messages
        channel_ids = sorted({
            n.split("/")[1][1:]
            for n in zf.namelist()
            if n.startswith("Messages/c") and n.endswith("/messages.json")
        })

        for cid in channel_ids:
            try:
                meta = json.loads(zf.read(f"Messages/c{cid}/channel.json"))
            except (KeyError, json.JSONDecodeError):
                continue

            for record in parse_channel(zf, cid, meta, index.get(cid), my_user_id):
                yield cid, meta, record
