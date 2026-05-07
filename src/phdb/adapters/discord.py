"""Discord adapter — ingests Discord data-export package.zip.

Source: a single `package.zip` produced by Discord's "Request my Data" export.

Only the `Messages/` tree is ingested:
  Messages/index.json               -- {channel_id: human_label}
  Messages/c<channel_id>/channel.json   -- channel metadata (type/guild/recipients)
  Messages/c<channel_id>/messages.json  -- list of {ID, Timestamp, Contents, Attachments}

Per-message Discord export only contains messages the user *sent*, so every
row gets direction='outbound'. The other party (for DMs) or channel context
(for guilds) is captured on the thread row and in the recipients table.

Per-channel resume: completed channel IDs are tracked in source_files.notes
as JSON ``{"channels_done": [...]}``.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import time
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import unquote, urlparse

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.discord")

_MAX_BODY_LEN = 50_000
_LOG_EVERY_CHANNELS = 10
_BATCH_COMMIT = 25


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


class DiscordAdapter(Adapter):
    """Ingest Discord data-export package.zip files."""

    name = "discord"
    source_kind = "discord"
    file_kind = "zip"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def __init__(
        self,
        *,
        max_seconds: float | None = None,
        since: str | None = None,
        max_channels: int | None = None,
    ) -> None:
        self.max_seconds = max_seconds
        self.since = since
        self.max_channels = max_channels

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        yield from ()

    def compute_raw_hash(self, row: AdapterRow) -> str:
        seed = f"discord|{row.thread_key or ''}|{row.rfc822_message_id or ''}|{(row.body_text or '')[:200]}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def _get_done_channels(self, conn: sqlite3.Connection, source_file_id: int) -> set[str]:
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        if not row or not row[0]:
            return set()
        try:
            return set(json.loads(row[0]).get("channels_done", []))
        except (json.JSONDecodeError, TypeError):
            return set()

    def _mark_channel_done(
        self, conn: sqlite3.Connection, source_file_id: int, channel_id: str
    ) -> None:
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        notes: dict[str, object] = {}
        if row and row[0]:
            try:
                notes = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                notes = {}
        raw_done = notes.get("channels_done", [])
        done: set[str] = set(raw_done) if isinstance(raw_done, list) else set()
        done.add(channel_id)
        notes["channels_done"] = sorted(done)
        conn.execute(
            "UPDATE source_files SET notes = ? WHERE id = ?",
            (json.dumps(notes), source_file_id),
        )

    def _resolve_sender(self, settings: Settings) -> tuple[str, str | None]:
        """Return (sender_address, sender_name) from identity config."""
        discord_handles = settings.identity.owner_handles.get("discord", set())
        if discord_handles:
            handle = next(iter(discord_handles))
            return f"discord:{handle}", None
        return "discord:unknown", None

    def _iter_channel_rows(
        self,
        zf: zipfile.ZipFile,
        channel_id: str,
        channel_meta: dict[str, object],
        index_label: str | None,
        my_user_id: str | None,
        sender_address: str,
        sender_name: str | None,
        channel_idx: int,
    ) -> Iterator[AdapterRow]:
        """Yield AdapterRows for one channel."""
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
        label = _derive_thread_label(channel_meta, index_label)

        for msg_idx, msg in enumerate(msgs):
            snowflake = str(msg.get("ID", ""))
            if not snowflake:
                continue

            body = (msg.get("Contents") or "").strip()
            att_urls = _split_attachments(msg.get("Attachments"))
            if not body and not att_urls:
                continue

            ts = _parse_discord_ts(msg.get("Timestamp"))
            if self.since and ts and ts < self.since:
                continue

            if len(body) > _MAX_BODY_LEN:
                body = body[:_MAX_BODY_LEN]

            synthetic_id = f"discord:{snowflake}"
            raw_hash = hashlib.sha256(
                f"discord|{channel_id}|{snowflake}|{body[:200]}".encode()
            ).hexdigest()
            body_hash = hashlib.sha256(body.encode()).hexdigest() if body else None

            recipients: list[dict[str, str]] = []
            if other_addr:
                recipients.append({
                    "address": other_addr,
                    "name": other_handle or "",
                    "rtype": "to",
                })

            attachments: list[dict[str, str | int | None]] = []
            for url in att_urls:
                fname = _filename_from_url(url)
                ctype = _content_type_from_filename(fname)
                attachments.append({
                    "filename": fname,
                    "content_type": ctype,
                    "content_disposition": None,
                    "size_bytes": None,
                    "on_disk_path": url,
                    "content_hash": None,
                })

            yield AdapterRow(
                schema_type="Message",
                rfc822_message_id=synthetic_id,
                sender_address=sender_address,
                sender_name=sender_name or label,
                sender_domain="discord",
                direction="outbound",
                date_sent=ts,
                body_text=body or None,
                body_text_source="discord-export",
                is_multipart=0,
                has_attachments=int(bool(att_urls)),
                attachment_count=len(att_urls),
                is_bulk=0,
                source_byte_offset=channel_idx,
                source_byte_length=msg_idx,
                raw_hash=raw_hash,
                body_text_hash=body_hash,
                recipients=recipients,
                attachments=attachments,
                thread_key=thread_key,
            )

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestReport:
        report = IngestReport(
            adapter_name=self.name,
            source_path=str(source_path),
            source_file_id=0,
        )

        source_file_id = self._register_source(conn, source_path)
        report.source_file_id = source_file_id
        log.info("[%s] Source registered: id=%d path=%s", self.name, source_file_id, source_path)

        sender_address, _sender_name_from_identity = self._resolve_sender(settings)

        zf = zipfile.ZipFile(source_path)

        try:
            user_data = json.loads(zf.read("Account/user.json"))
            my_user_id = user_data.get("id")
            sender_name = (
                user_data.get("global_name")
                or user_data.get("username")
                or None
            )
        except (KeyError, json.JSONDecodeError):
            my_user_id, sender_name = None, None

        try:
            index: dict[str, str] = json.loads(zf.read("Messages/index.json"))
        except (KeyError, json.JSONDecodeError):
            index = {}

        channel_ids = sorted({
            n.split("/")[1][1:]
            for n in zf.namelist()
            if n.startswith("Messages/c") and n.endswith("/messages.json")
        })

        done_channels = self._get_done_channels(conn, source_file_id)
        todo = [cid for cid in channel_ids if cid not in done_channels]
        if self.max_channels:
            todo = todo[:self.max_channels]

        log.info(
            "[%s] Channels: %d total, %d done, %d remaining",
            self.name, len(channel_ids), len(done_channels), len(todo),
        )

        t_start = time.time()
        channels_done = 0
        touched_threads: set[int] = set()

        for ci, cid in enumerate(todo):
            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                log.info("[%s] Time budget reached after %d channels", self.name, channels_done)
                break

            try:
                meta = json.loads(zf.read(f"Messages/c{cid}/channel.json"))
            except (KeyError, json.JSONDecodeError) as e:
                log.warning("[%s] Error reading channel %s: %s", self.name, cid, e)
                report.errors.append(cid)
                continue

            for row in self._iter_channel_rows(
                zf, cid, meta, index.get(cid),
                my_user_id, sender_address, sender_name, ci,
            ):
                report.rows_yielded += 1

                if row.body_text and not row.body_text_hash:
                    row.body_text_hash = hashlib.sha256(row.body_text.encode("utf-8")).hexdigest()

                has_identity = bool(
                    settings.identity.owner_names
                    or settings.identity.owner_emails
                    or settings.identity.owner_phones
                    or settings.identity.owner_handles
                )
                if row.direction == "unknown" and has_identity:
                    row.direction = self.infer_direction(row, settings.identity)

                message_id = self._insert_row(conn, row, source_file_id)
                if message_id is None:
                    report.rows_skipped += 1
                    continue

                report.rows_inserted += 1
                self._insert_sidecars(conn, message_id, row)

                if row.thread_key:
                    other_addr, _oh, _oid = _derive_other_party(meta, index.get(cid), my_user_id)
                    participants = sorted({sender_address, other_addr or f"discord:{cid}"})
                    thread_id, created = self._upsert_thread(conn, row.thread_key, participants)
                    self._link_message_thread(conn, message_id, thread_id)
                    if created:
                        report.threads_created += 1
                    touched_threads.add(thread_id)

            self._mark_channel_done(conn, source_file_id, cid)
            conn.commit()
            channels_done += 1

            if channels_done % _LOG_EVERY_CHANNELS == 0:
                log.info(
                    "[%s] Progress: %d/%d channels, %d rows inserted",
                    self.name, channels_done, len(todo), report.rows_inserted,
                )

        for tid in touched_threads:
            self._update_thread_aggregates(conn, tid)

        actual = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE source_file_id = ?",
            (source_file_id,),
        ).fetchone()[0]
        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (actual, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d channels, %d yielded, %d inserted, %d skipped, %d threads",
            self.name, channels_done, report.rows_yielded, report.rows_inserted,
            report.rows_skipped, report.threads_created,
        )
        return report
