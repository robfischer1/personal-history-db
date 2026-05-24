"""CalendarPlugin — Phase 7 brief 029 port of the iCal calendar ingester.

Consumes iCal ``.ics`` calendar exports (single file, directory, or
zip) and emits one ``events`` row per ``VEVENT``. Each insert gets an
``inThread`` triple pointing at a per-calendar thread node keyed by
``"calendar:<calendar_name>"`` — matching the legacy adapter's
per-calendar thread bucketing so existing test assertions over
thread-node counts and inThread-triple density keep passing.

The manifest declares ``emits = ["Event", "InviteAction", "Action"]``
to leave room for richer iCal exports (VEVENTs with attendee + RSVP
state -> InviteAction; VTODOs / reminders -> Action), but the current
parser yields only ``CalendarEvent`` records which route to the
``events`` table. Multi-emit branches stay dormant until the parser
grows to populate them.

Replaces the legacy ``phdb.adapters.calendar`` module deleted in the
same commit per Phase 0 Q14 (no shim). Reuses the ``events`` typed
table (migration 0021); no schema changes.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.ical import parse as parse_ical
from phdb.log import get_logger
from phdb.records import CalendarEvent
from phdb.triples import get_predicate, resolve_node

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.calendar")

_MAX_BODY_LEN = 50_000


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
_INSERT_EVENT_SQL = """\
INSERT OR IGNORE INTO events (
    schema_type, event_key, subject, sender_address, sender_name,
    direction, date_occurred, date_received, body_text, body_text_source,
    body_text_hash, raw_hash, source_file_id
) VALUES (
    'Event', ?, ?, ?, ?,
    'self', ?, ?, ?, 'ical',
    ?, ?, ?
)"""


def _emit_thread_triple(
    conn: sqlite3.Connection,
    source_kind: str,
    table: str,
    row_id: int,
    thread_key: str,
) -> tuple[int, bool]:
    """Emit an ``inThread`` triple linking ``(table, row_id)`` to the thread node.

    ``thread_key`` is passed through verbatim from the legacy adapter
    (``"calendar:<calendar_name>"``) — the resulting node label
    becomes ``"<source_kind>:<thread_key>"`` to match the legacy
    ``Adapter._upsert_thread`` shape so existing tests over
    thread-node counts keep passing.

    Returns ``(thread_node_id, created)``; ``created`` is True when
    the thread node didn't exist before this call.
    """
    pred = get_predicate(conn, "inThread")
    if not pred:
        return 0, False
    in_thread_id = pred["id"]

    record_label = f"{table}:{row_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table=table, source_id=row_id,
    )

    thread_label = f"{source_kind}:{thread_key}"
    existing = conn.execute(
        "SELECT id FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
        (thread_label.lower(),),
    ).fetchone()
    if existing:
        thread_node_id = int(existing[0])
        created = False
    else:
        _node = resolve_node(conn, thread_label, "thread")
        assert _node is not None
        thread_node_id = _node
        created = True

    conn.execute(
        """INSERT OR IGNORE INTO triples
           (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
           VALUES (?, ?, ?, 'plugin', ?)""",
        (record_node_id, in_thread_id, thread_node_id, source_kind),
    )
    return thread_node_id, created


class CalendarPlugin(PhdbSourcePlugin):
    """iCal (.ics) calendar export plugin — Phase 7 brief 029 port."""

    SOURCE_KIND = "calendar"
    FILE_KIND = "ical"
    BATCH_SIZE = 500

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every iCal source.

        Accepts a single ``.ics`` / ``.ical`` file, a ``.zip`` of them,
        or a directory containing either. The parser handles the actual
        zip/dir/file fan-out — discovery here just locates the container.
        """
        if root.is_file():
            suffix = root.suffix.lower()
            if suffix in (".ics", ".ical", ".zip"):
                yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix.lower() in (".ics", ".ical"):
                yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[CalendarEvent]:
        """Yield CalendarEvent records from one iCal source path."""
        yield from parse_ical(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: CalendarEvent,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        """Insert one CalendarEvent into the ``events`` typed table.

        Returns the inserted row id, or ``None`` on dedup skip. When a
        row is inserted, an ``inThread`` triple is emitted with
        ``thread_key="calendar:<calendar_name>"`` — matching the
        legacy adapter's per-calendar thread bucketing.
        """
        sf_id = source_file_id if source_file_id is not None else 0

        # Mirror legacy CalendarAdapter.iter_rows() body composition:
        #   "<summary>\n@ <location>\n<description>" (each section optional)
        parts = [record.summary or "(no summary)"]
        if record.location:
            parts.append(f"@ {record.location}")
        if record.description:
            parts.append(record.description)
        body = "\n".join(parts)[:_MAX_BODY_LEN]
        body_text_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()

        event_key = f"calendar:{record.provenance.raw_hash}"

        cur = conn.execute(
            _INSERT_EVENT_SQL,
            (
                event_key,                          # event_key
                record.summary or "(no summary)",   # subject
                record.calendar_name,               # sender_address
                record.calendar_name,               # sender_name
                record.date_start or None,          # date_occurred
                record.date_end,                    # date_received
                body,                               # body_text
                body_text_hash,                     # body_text_hash
                record.provenance.raw_hash,         # raw_hash
                sf_id,                              # source_file_id
            ),
        )
        if cur.rowcount == 0:
            return None
        row_id = int(cur.lastrowid)  # type: ignore[arg-type]
        _emit_thread_triple(
            conn, self.SOURCE_KIND, "events", row_id,
            thread_key=f"calendar:{record.calendar_name}",
        )
        return row_id

    def register_cli(self, parser: Any) -> None:
        """Phase 7: registration via generic ``phdb plugin ingest calendar <path>``."""
        return None

    def register_tools(self, server: Any) -> None:
        """No calendar-specific MCP tools yet."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one iCal source.

        Mirrors the legacy ``CalendarAdapter.run`` surface — the ported
        tests consume this entry point. ``rows_inserted`` /
        ``rows_skipped`` track every ``events`` emission.
        """
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            row_id = self.ingest_row(
                conn, record, source_file_id=source_file_id,
            )
            if row_id is None:
                report.rows_skipped += 1
            else:
                report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[calendar] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
