"""AppleDbsPlugin — port of the apple_dbs adapter to the new contract.

Phase 7 of the phdb Plugin Architecture plan. Implements PhdbSourcePlugin
to handle Safari history, iMessages, and call history from iPhone backups.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.apple_dbs_sqlite import HANDLER_NAMES
from phdb.formats.apple_dbs_sqlite import parse as parse_apple_dbs
from phdb.log import get_logger
from phdb.plugins.apple_dbs.ingest import (
    ingest_call_record,
    ingest_chat_message,
    ingest_digital_document,
    ingest_web_activity,
)
from phdb.records import CallRecord, ChatMessage, DigitalDocument, WebActivity

if TYPE_CHECKING:
    from phdb.core.plugin.bus import EmissionBus
    from phdb.settings import Settings

log = get_logger("phdb.plugins.apple_dbs")


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
    source_kind: str = "apple_dbs",
    file_kind: str = "sqlite",
) -> int:
    """Insert (or refresh) a source_files row for the given path."""
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


def _upsert_thread(conn: sqlite3.Connection, source_kind: str, thread_key: str) -> tuple[int, bool]:
    """Find or create a thread node by (source_kind, thread_key)."""
    from phdb.core.graph import resolve_node
    label = f"{source_kind}:{thread_key}"
    existing = conn.execute(
        "SELECT id FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
        (label.lower(),),
    ).fetchone()
    if existing:
        return int(existing[0]), False

    node_id = resolve_node(conn, label, "thread")
    return int(node_id), True  # type: ignore[arg-type]


def _link_message_thread(
    conn: sqlite3.Connection,
    source_kind: str,
    source_table: str,
    source_id: int,
    thread_node_id: int,
) -> None:
    """Emit an inThread triple from the message's record node to the thread node."""
    from phdb.core.graph import get_predicate, resolve_node
    pred = get_predicate(conn, "inThread")
    if not pred:
        return
    pred_id = pred["id"]

    record_label = f"{source_table}:{source_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table=source_table, source_id=source_id,
    )

    conn.execute(
        "INSERT OR IGNORE INTO triples"
        " (subject_node_id, predicate_id, object_node_id,"
        "  provenance, source_ref)"
        " VALUES (?, ?, ?, 'adapter', ?)",
        (record_node_id, pred_id, thread_node_id, source_kind),
    )


class AppleDbsPlugin(PhdbSourcePlugin):
    """Apple iPhone backup SQLite databases ingester."""

    SOURCE_KIND = "iphone-backup"
    FILE_KIND = "sqlite"

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Discover Apple backup subdirectories by handler name."""
        found = False
        for handler in HANDLER_NAMES:
            handler_dir = root / handler
            if handler_dir.exists():
                yield handler_dir, handler
                found = True

        if not found and root.exists():
            # Treat root as a flat directory containing some DBs
            yield root, "apple-backup-flat"

    def parse(self, path: Path) -> Iterator[Any]:
        """Yield typed records from one handler directory."""
        # If path is a handler subdir, use that specific handler.
        if path.name in HANDLER_NAMES:
            yield from parse_apple_dbs(path, path.name)
            return

        # Otherwise, try all handlers on this path.
        for handler in HANDLER_NAMES:
            yield from parse_apple_dbs(path, handler)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: Any,
        *,
        source_file_id: int | None = None,
    ) -> int:
        """Persist a single record to its typed table; return the row id."""
        sf_id = source_file_id if source_file_id is not None else 0
        if isinstance(record, WebActivity):
            return ingest_web_activity(conn, record, sf_id)
        if isinstance(record, ChatMessage):
            return ingest_chat_message(conn, record, sf_id)
        if isinstance(record, CallRecord):
            return ingest_call_record(conn, record, sf_id)
        if isinstance(record, DigitalDocument):
            return ingest_digital_document(conn, record, sf_id)
        return 0

    def _get_done_handlers(self, conn: sqlite3.Connection, source_file_id: int) -> set[str]:
        """Read set of completed handlers from source_files.notes JSON."""
        import json
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        if not row or not row[0]:
            return set()
        try:
            return set(json.loads(row[0]).get("handlers_done", []))
        except (json.JSONDecodeError, TypeError):
            return set()

    def _mark_handler_done(
        self, conn: sqlite3.Connection, source_file_id: int, handler_name: str
    ) -> None:
        """Add handler_name to source_files.notes JSON."""
        import json
        row = conn.execute(
            "SELECT notes FROM source_files WHERE id = ?", (source_file_id,)
        ).fetchone()
        notes: dict[str, Any] = {}
        if row and row[0]:
            try:
                notes = json.loads(row[0])
            except (json.JSONDecodeError, TypeError):
                notes = {}
        raw_done = notes.get("handlers_done", [])
        done: set[str] = set(raw_done) if isinstance(raw_done, list) else set()
        done.add(handler_name)
        notes["handlers_done"] = sorted(done)
        conn.execute(
            "UPDATE source_files SET notes = ? WHERE id = ?",
            (json.dumps(notes), source_file_id),
        )

    def project_facets(self, emission_bus: EmissionBus, record: Any) -> None:
        """Optional — emit FacetEmission events. (Phase 8+)"""
        # iMessage -> Thread, Person
        # Legacy handled this via direct SQL in run(); plugin-port maintains
        # compatibility by doing it in run() for now, or here if bus is ready.
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
        settings: Settings | None = None,
        *,
        max_seconds: float | None = None,
        only: list[str] | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of an Apple backup directory.

        Mirrors the legacy ``AppleDbsAdapter.run`` surface for tests.
        """
        summary = IngestSummary(source_path=str(source_path))

        # 1. Register source
        sf_id = _register_source_file(conn, source_path, source_kind=self.name, file_kind=self.FILE_KIND)
        summary.source_file_id = sf_id

        # 2. Get todo handlers
        handlers_to_run = only or list(HANDLER_NAMES)
        done_handlers = self._get_done_handlers(conn, sf_id)
        todo = [h for h in handlers_to_run if h not in done_handlers]

        # 3. Iterate
        t_start = time.time()
        for handler_name in todo:
            if max_seconds and (time.time() - t_start) > max_seconds:
                log.info("[%s] Time budget reached", self.name)
                break

            handler_dir = source_path / handler_name
            if not handler_dir.exists():
                handler_dir = source_path

            try:
                for record in parse_apple_dbs(handler_dir, handler_name):
                    summary.rows_yielded += 1
                    row_id = self.ingest_row(conn, record, source_file_id=sf_id)
                    if row_id:
                        summary.rows_inserted += 1

                        # Handle Thread facets (legacy-style for now)
                        thread_key = getattr(record, "thread_key", None)
                        if thread_key:
                            tid, created = _upsert_thread(conn, self.name, thread_key)

                            # Determine source table
                            source_table = "chat_messages"
                            if isinstance(record, CallRecord):
                                source_table = "actions"

                            _link_message_thread(conn, self.name, source_table, row_id, tid)
                            if created:
                                summary.threads_created += 1

                self._mark_handler_done(conn, sf_id, handler_name)
                conn.commit()
            except Exception:
                log.exception("[%s] Error in handler %s", self.name, handler_name)
                summary.errors.append(handler_name)

        # 4. Finalize source_file record
        conn.execute(
            "UPDATE source_files SET message_count = (SELECT COUNT(*) FROM chat_messages WHERE source_file_id = ?) + (SELECT COUNT(*) FROM actions WHERE source_file_id = ?) WHERE id = ?",
            (sf_id, sf_id, sf_id),
        )
        conn.commit()

        return summary
