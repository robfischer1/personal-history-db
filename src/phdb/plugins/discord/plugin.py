"""Discord plugin — ingests Discord data-export ZIP archives."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.chat_upserts import (
    emit_chat_recipient_triples,
    emit_chat_thread_triple,
    upsert_chat_attachment,
    upsert_chat_message,
)
from phdb.formats.discord_json import (
    _derive_other_party,
    _derive_thread_label,
    parse_channel,
)
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.records import ChatMessage
    from phdb.settings import Settings

log = get_logger("phdb.plugins.discord")


@dataclass
class IngestSummary:
    """Result of one ``run()`` call — mirrors the legacy IngestReport surface."""

    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    threads_created: int = 0
    errors: list[str] = field(default_factory=list)


def _register_source_file(
    conn: sqlite3.Connection,
    source_path: Path,
    *,
    source_kind: str = "discord",
    file_kind: str = "zip",
) -> int:
    """Insert or refresh a source_files row."""
    cur = conn.execute(
        """INSERT INTO source_files
           (source_path, source_org, file_kind, source_kind, session_uuid, ingested_at)
           VALUES (?, ?, ?, ?, NULL,
                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
           ON CONFLICT(source_path) DO UPDATE
             SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           RETURNING id""",
        (str(source_path), None, file_kind, source_kind),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


class DiscordPlugin(PhdbSourcePlugin):
    """Discord plugin — Phase 7 port."""

    SOURCE_KIND = "discord"
    FILE_KIND = "zip"
    BATCH_SIZE = 500

    def __init__(
        self,
        manifest: PluginManifest,
        *,
        max_seconds: float | None = None,
        since: str | None = None,
        max_channels: int | None = None,
    ) -> None:
        super().__init__(manifest)
        self.max_seconds = max_seconds
        self.since = since
        self.max_channels = max_channels
        self._sender_address: str = "discord:unknown"
        self._sender_name: str | None = None

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every Discord ZIP."""
        if root.is_file():
            if root.suffix.lower() == ".zip":
                # Quick check if it's a Discord export
                try:
                    with zipfile.ZipFile(root) as zf:
                        if "Messages/index.json" in zf.namelist():
                            yield root, self.SOURCE_KIND
                except zipfile.BadZipFile:
                    pass
            return
        for path in sorted(root.rglob("*.zip")):
            yield from self.discover(path)

    def parse(self, path: Path) -> Iterator[tuple[str, dict[str, Any], ChatMessage]]:
        """Yield (channel_id, channel_meta, record) from one Discord export ZIP."""
        with zipfile.ZipFile(path) as zf:
            # Read the exporting user's ID (for DM recipient logic)
            my_user_id: str | None = None
            try:
                user_data = json.loads(zf.read("Account/user.json"))
                my_user_id = user_data.get("id")
                self._sender_name = (
                    user_data.get("global_name")
                    or user_data.get("username")
                    or None
                )
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

            # We can't easily filter by max_channels here because parse() is supposed to be pure,
            # but the contract allows it. However, the run() method handles todo/done logic.
            for cid in channel_ids:
                try:
                    meta = json.loads(zf.read(f"Messages/c{cid}/channel.json"))
                except (KeyError, json.JSONDecodeError):
                    continue

                for record in parse_channel(zf, cid, meta, index.get(cid), my_user_id):
                    yield cid, meta, record

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ChatMessage,
        *,
        source_file_id: int,
        direction: str = "outbound",
    ) -> int | None:
        """Ingest a single ChatMessage record."""
        # Use identity-resolved address if placeholder is present
        sender_address = None
        sender_name = None
        if record.sender_address == "discord:self":
            sender_address = self._sender_address
            sender_name = self._sender_name

        message_id = upsert_chat_message(
            conn, source_file_id, record,
            direction=direction, body_text_source="discord-export",
            sender_address=sender_address,
            sender_name=sender_name,
        )
        if message_id is None:
            return None

        for attachment in record.attachments:
            upsert_chat_attachment(conn, message_id, attachment)

        emit_chat_recipient_triples(conn, self.SOURCE_KIND, message_id, record)

        if record.thread_key:
            emit_chat_thread_triple(conn, self.SOURCE_KIND, message_id, record.thread_key)

        return message_id

    def project_facets(self, emission_bus: Any, record: ChatMessage) -> None:
        """Emit facet events for Person, Time, Thread."""
        return None

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestSummary:
        """End-to-end ingest of one Discord ZIP."""
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        # Identity resolution
        discord_handles = settings.identity.owner_handles.get("discord", set())
        if discord_handles:
            handle = next(iter(discord_handles))
            self._sender_address = f"discord:{handle}"
        else:
            self._sender_address = "discord:unknown"

        zf = zipfile.ZipFile(source_path)
        try:
            user_data = json.loads(zf.read("Account/user.json"))
            my_user_id = user_data.get("id")
            self._sender_name = (
                user_data.get("global_name")
                or user_data.get("username")
                or None
            )
        except (KeyError, json.JSONDecodeError):
            my_user_id = None

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
            "[discord] Channels: %d total, %d done, %d remaining",
            len(channel_ids), len(done_channels), len(todo),
        )

        t_start = time.time()
        channels_done = 0
        batch_count = 0

        for cid in todo:
            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                log.info("[discord] Time budget reached after %d channels", channels_done)
                break

            try:
                meta = json.loads(zf.read(f"Messages/c{cid}/channel.json"))
            except (KeyError, json.JSONDecodeError) as e:
                log.warning("[discord] Error reading channel %s: %s", cid, e)
                report.errors.append(cid)
                continue

            # Thread creation signal
            label = f"{self.SOURCE_KIND}:{cid}"
            exists = conn.execute(
                "SELECT 1 FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
                (label.lower(),),
            ).fetchone()
            if not exists:
                report.threads_created += 1

            for record in parse_channel(zf, cid, meta, index.get(cid), my_user_id):
                # Since filter
                if self.since and record.date_sent and record.date_sent < self.since:
                    continue

                report.rows_yielded += 1
                
                # In Discord exports, all messages are outbound (sent by the user)
                message_id = self.ingest_row(
                    conn, record, source_file_id=source_file_id, direction="outbound"
                )
                
                if message_id is None:
                    report.rows_skipped += 1
                    continue

                report.rows_inserted += 1

                batch_count += 1
                if batch_count >= self.BATCH_SIZE:
                    conn.commit()
                    batch_count = 0

            self._mark_channel_done(conn, source_file_id, cid)
            conn.commit()
            channels_done += 1

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[discord] Done: %d channels, %d yielded, %d inserted, %d skipped, %d threads",
            channels_done, report.rows_yielded, report.rows_inserted,
            report.rows_skipped, report.threads_created,
        )
        return report

    # --------------------------- Private helpers ---------------------------

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
