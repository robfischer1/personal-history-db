"""DiscordChatExporter plugin — ingests per-channel JSON exports with full
bidirectional message history (all participants, not just the exporting user).
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.chat_upserts import (
    emit_chat_recipient_triples,
    emit_chat_thread_triple,
    upsert_chat_attachment,
    upsert_chat_message,
)
from phdb.formats.discord_dce_json import is_dce_json, parse_dce_export
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.records import ChatMessage
    from phdb.settings import Settings

log = get_logger("phdb.plugins.discord_dce")


class DiscordDcePlugin(PhdbSourcePlugin):
    """DiscordChatExporter JSON ingester — bidirectional messages."""

    SOURCE_KIND = "discord-dce"
    FILE_KIND = "json"
    BATCH_SIZE = 500

    def __init__(
        self,
        manifest: PluginManifest,
        *,
        max_seconds: float | None = None,
        since: str | None = None,
    ) -> None:
        super().__init__(manifest)
        self.max_seconds = max_seconds
        self.since = since
        self._owner_names: set[str] = set()

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        if root.is_file():
            if root.suffix.lower() == ".json" and is_dce_json(root):
                yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*.json")):
            if is_dce_json(path):
                yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[tuple[str, ChatMessage]]:
        yield from parse_dce_export(path, owner_names=self._owner_names)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: ChatMessage,
        *,
        source_file_id: int,
        direction: str = "unknown",
    ) -> int | None:
        message_id = upsert_chat_message(
            conn, source_file_id, record,
            direction=direction, body_text_source="discord-chat-exporter",
        )
        if message_id is None:
            return None

        for attachment in record.attachments:
            upsert_chat_attachment(conn, message_id, attachment)

        emit_chat_recipient_triples(conn, "discord", message_id, record)

        if record.thread_key:
            emit_chat_thread_triple(conn, "discord", message_id, record.thread_key)

        return message_id

    def project_facets(self, emission_bus: Any, record: ChatMessage) -> None:
        return None

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> Any:
        from phdb.core.plugin.summary import IngestSummary

        report = IngestSummary(source_path=str(source_path))

        discord_handles = settings.identity.owner_handles.get("discord", set())
        self._owner_names: set[str] = {h.lower() for h in discord_handles}
        self._owner_names.update(n.lower() for n in settings.identity.owner_names)

        if discord_handles:
            log.info("[discord_dce] Owner handles: %s", discord_handles)
        if not self._owner_names:
            log.warning(
                "[discord_dce] No discord handles or owner names in identity config — "
                "direction will be 'unknown' for all messages"
            )

        if source_path.is_dir():
            json_files = sorted(source_path.glob("*.json"))
        else:
            json_files = [source_path]

        t_start = time.time()

        for json_path in json_files:
            if not is_dce_json(json_path):
                continue

            if self.max_seconds and (time.time() - t_start) > self.max_seconds:
                log.info("[discord_dce] Time budget reached")
                break

            source_file_id = _register_source_file(
                conn, json_path,
                source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
            )
            report.source_file_id = source_file_id

            batch_count = 0
            for direction, record in parse_dce_export(json_path, owner_names=self._owner_names):
                if self.since and record.date_sent and record.date_sent < self.since:
                    continue

                report.rows_yielded += 1

                message_id = self.ingest_row(
                    conn, record, source_file_id=source_file_id, direction=direction,
                )

                if message_id is None:
                    report.rows_skipped += 1
                    continue

                report.rows_inserted += 1

                batch_count += 1
                if batch_count >= self.BATCH_SIZE:
                    conn.commit()
                    batch_count = 0

            conn.commit()
            log.info(
                "[discord_dce] %s: %d yielded, %d inserted, %d skipped",
                json_path.name, report.rows_yielded, report.rows_inserted,
                report.rows_skipped,
            )

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, report.source_file_id),
        )
        conn.commit()

        log.info(
            "[discord_dce] Done: %d files, %d yielded, %d inserted, %d skipped",
            len(json_files), report.rows_yielded, report.rows_inserted,
            report.rows_skipped,
        )
        return report

