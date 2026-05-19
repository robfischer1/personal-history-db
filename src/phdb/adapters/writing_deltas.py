"""Writing delta-stream adapter — ingests NDJSON files emitted by the
`obsidian-delta-stream` Obsidian plugin into the writing_sessions +
writing_deltas typed tables.

Source format: one JSON object per line (NDJSON), one of:
    {"type":"session-start", "ts":…, "sessionId":…, "notePath":…}
    {"type":"doc-change",    "ts":…, "sessionId":…, "notePath":…,
                              "fromA":…,"toA":…,"fromB":…,"toB":…,
                              "insertedText":…,"deletedText":…,"userEvent":…,
                              "noteType":…,"vaultFolder":…}
    {"type":"selection-change","ts":…,"sessionId":…,"notePath":…,"ranges":[…]}
    {"type":"session-end",   "ts":…, "sessionId":…, "notePath":…, "reason":…}
    {"type":"note-switch",   "ts":…, "sessionId":"", "notePath":…, "fromPath":…, "toPath":…}

session-start opens a writing_sessions row; session-end closes it. doc-change
and selection-change events land in writing_deltas linked by session_id.
note-switch events are skipped — they live in NDJSON for future inspection
but are not materialised.

Dedup: writing_deltas.raw_hash (sha256 of the original NDJSON line) carries
UNIQUE — re-ingesting the same file is a no-op. writing_sessions dedups on
session_id (UNIQUE), with INSERT OR IGNORE so duplicate session-starts are
absorbed.

Crash-safety: NDJSON is append-only by construction. The last line may be
partial after a hard quit; that line is JSON-parse-skipped with a debug log.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.writing_deltas")


# ---------------------------------------------------------------------------
# Per-session aggregation accumulator
# ---------------------------------------------------------------------------


class _SessionAccumulator:
    """Per-session state assembled during the first pass over the NDJSON."""

    __slots__ = (
        "session_id",
        "note_path",
        "vault_folder",
        "note_type",
        "started_at",
        "ended_at",
        "ended_reason",
        "deltas",
    )

    def __init__(self, session_id: str) -> None:
        self.session_id: str = session_id
        self.note_path: str | None = None
        self.vault_folder: str | None = None
        self.note_type: str | None = None
        self.started_at: int | None = None
        self.ended_at: int | None = None
        self.ended_reason: str | None = None
        # Each delta carries (raw_line, parsed_obj).
        self.deltas: list[tuple[str, dict[str, Any]]] = []


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


class WritingDeltasAdapter(Adapter):
    """Ingest writing-delta NDJSON files into writing_sessions + writing_deltas."""

    name = "writing_deltas"
    source_kind = "writing-deltas"
    file_kind = "ndjson"
    schema_type = "WritingSession"
    # iter_rows is not used — this adapter overrides run() because it writes
    # to domain tables, not the messages/documents/articles tables.
    dedup_strategy = DedupStrategy.CONTENT_HASH
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        raise NotImplementedError(
            "WritingDeltasAdapter overrides run() and writes to writing_sessions/writing_deltas directly"
        )

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestReport:
        self._settings = settings
        self.validate_source_path(source_path)

        report = IngestReport(
            adapter_name=self.name,
            source_path=str(source_path),
            source_file_id=0,
        )

        source_file_id = self._register_source(conn, source_path)
        report.source_file_id = source_file_id
        log.info(
            "[%s] Source registered: id=%d path=%s",
            self.name,
            source_file_id,
            source_path,
        )

        sessions = self._parse_ndjson(source_path, report)
        if not sessions:
            log.info("[%s] No sessions parsed from %s", self.name, source_path)
            conn.execute(
                "UPDATE source_files SET message_count = ? WHERE id = ?",
                (0, source_file_id),
            )
            conn.commit()
            return report

        touched_session_pks: list[int] = []
        batch_count = 0

        for accum in sessions.values():
            session_pk = self._upsert_session(conn, accum, source_file_id)
            touched_session_pks.append(session_pk)
            report.threads_created += 1  # repurposed counter: 1 thread = 1 writing session

            for raw_line, event in accum.deltas:
                inserted = self._insert_delta(
                    conn, session_pk, accum.session_id, event, raw_line, source_file_id
                )
                report.rows_yielded += 1
                if inserted:
                    report.rows_inserted += 1
                else:
                    report.rows_skipped += 1
                batch_count += 1
                if batch_count >= self.batch_size:
                    conn.commit()
                    batch_count = 0

        conn.commit()

        # Recompute aggregates for every session touched in this run.
        for pk in touched_session_pks:
            self._recompute_aggregates(conn, pk)
        conn.commit()

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d sessions, %d deltas yielded, %d inserted, %d skipped",
            self.name,
            len(sessions),
            report.rows_yielded,
            report.rows_inserted,
            report.rows_skipped,
        )
        return report

    # -----------------------------------------------------------------------
    # Parsing
    # -----------------------------------------------------------------------

    def _parse_ndjson(
        self,
        source_path: Path,
        report: IngestReport,
    ) -> dict[str, _SessionAccumulator]:
        """First pass — read every line, bucket by session_id."""
        sessions: dict[str, _SessionAccumulator] = defaultdict(
            lambda: _SessionAccumulator("")
        )

        with source_path.open("r", encoding="utf-8") as f:
            for lineno, raw in enumerate(f, start=1):
                line = raw.rstrip("\n")
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError as e:
                    # Partial trailing line after a hard quit is expected — log + drop.
                    log.debug(
                        "[%s] Skipping unparseable line %d in %s (%s)",
                        self.name,
                        lineno,
                        source_path,
                        e,
                    )
                    report.errors.append(f"line {lineno}: {e}")
                    continue

                event_type = obj.get("type")
                if not isinstance(event_type, str):
                    continue

                if event_type == "note-switch":
                    # Not materialised — see module docstring.
                    continue

                session_id = obj.get("sessionId")
                if not isinstance(session_id, str) or session_id == "":
                    continue

                accum = sessions.get(session_id)
                if accum is None:
                    accum = _SessionAccumulator(session_id)
                    sessions[session_id] = accum

                ts = obj.get("ts")
                note_path = obj.get("notePath")
                if isinstance(note_path, str) and accum.note_path is None:
                    accum.note_path = note_path

                if event_type == "session-start":
                    if isinstance(ts, int):
                        accum.started_at = ts
                    # session-start events carry no noteType/vaultFolder in the
                    # current schema; both are denormalised onto doc-change
                    # events. Fall through to capture from the next delta.
                elif event_type == "session-end":
                    if isinstance(ts, int):
                        accum.ended_at = ts
                    reason = obj.get("reason")
                    if isinstance(reason, str):
                        accum.ended_reason = reason
                elif event_type in ("doc-change", "selection-change"):
                    accum.deltas.append((line, obj))
                    if event_type == "doc-change":
                        nt = obj.get("noteType")
                        if isinstance(nt, str) and accum.note_type is None:
                            accum.note_type = nt
                        vf = obj.get("vaultFolder")
                        if isinstance(vf, str) and accum.vault_folder is None:
                            accum.vault_folder = vf
                    if accum.started_at is None and isinstance(ts, int):
                        # Fallback when session-start lives in a different file.
                        accum.started_at = ts

        # Drop accumulators that have no usable identity. We keep a session
        # if it has a notePath AND either a started_at or an ended_at — the
        # ended_at-only case covers sessions that span the midnight day-file
        # boundary (session-start in yesterday's file, session-end in today's).
        return {
            sid: accum
            for sid, accum in sessions.items()
            if accum.note_path is not None
            and (accum.started_at is not None or accum.ended_at is not None)
        }

    # -----------------------------------------------------------------------
    # Writes
    # -----------------------------------------------------------------------

    def _upsert_session(
        self,
        conn: sqlite3.Connection,
        accum: _SessionAccumulator,
        source_file_id: int,
    ) -> int:
        """Insert the session if new, update bounds/metadata on conflict. Returns id.

        Cross-file behaviour: if a later-ingested file carries the real
        session-start (smaller ts), MIN(...) on the ON CONFLICT clause
        corrects an earlier started_at fallback that came from session-end.
        """
        # Fallback so writing_sessions.started_at stays NOT NULL when only
        # session-end was seen in this file. The MIN-on-conflict below
        # corrects this if the real session-start is later ingested.
        started_at = (
            accum.started_at if accum.started_at is not None else accum.ended_at
        )
        cur = conn.execute(
            """INSERT INTO writing_sessions
               (session_id, note_path, vault_folder, note_type,
                started_at, ended_at, ended_reason, source_file_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                   started_at    = MIN(writing_sessions.started_at, excluded.started_at),
                   ended_at      = COALESCE(excluded.ended_at,      writing_sessions.ended_at),
                   ended_reason  = COALESCE(excluded.ended_reason,  writing_sessions.ended_reason),
                   note_type     = COALESCE(writing_sessions.note_type,     excluded.note_type),
                   vault_folder  = COALESCE(writing_sessions.vault_folder,  excluded.vault_folder),
                   source_file_id = excluded.source_file_id
               RETURNING id""",
            (
                accum.session_id,
                accum.note_path,
                accum.vault_folder,
                accum.note_type,
                started_at,
                accum.ended_at,
                accum.ended_reason,
                source_file_id,
            ),
        )
        row = cur.fetchone()
        assert row is not None
        return int(row[0])

    def _insert_delta(
        self,
        conn: sqlite3.Connection,
        session_pk: int,
        session_id: str,
        event: dict[str, Any],
        raw_line: str,
        source_file_id: int,
    ) -> bool:
        """Insert one delta row. Returns True if newly inserted, False if dedup-skipped."""
        raw_hash = hashlib.sha256(raw_line.encode("utf-8")).hexdigest()
        event_type = event["type"]
        ts = event.get("ts")
        note_path = event.get("notePath", "")

        if event_type == "doc-change":
            cur = conn.execute(
                """INSERT OR IGNORE INTO writing_deltas
                   (session_pk, session_id, ts, event_type, note_path,
                    from_a, to_a, from_b, to_b,
                    inserted_text, deleted_text, user_event,
                    selection_ranges_json, source_file_id, raw_hash)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)""",
                (
                    session_pk,
                    session_id,
                    ts,
                    "doc-change",
                    note_path,
                    event.get("fromA"),
                    event.get("toA"),
                    event.get("fromB"),
                    event.get("toB"),
                    event.get("insertedText", ""),
                    event.get("deletedText", ""),
                    event.get("userEvent"),
                    source_file_id,
                    raw_hash,
                ),
            )
        elif event_type == "selection-change":
            ranges = event.get("ranges")
            ranges_json = json.dumps(ranges) if isinstance(ranges, list) else None
            cur = conn.execute(
                """INSERT OR IGNORE INTO writing_deltas
                   (session_pk, session_id, ts, event_type, note_path,
                    from_a, to_a, from_b, to_b,
                    inserted_text, deleted_text, user_event,
                    selection_ranges_json, source_file_id, raw_hash)
                   VALUES (?, ?, ?, ?, ?, NULL, NULL, NULL, NULL, NULL, NULL, NULL, ?, ?, ?)""",
                (
                    session_pk,
                    session_id,
                    ts,
                    "selection-change",
                    note_path,
                    ranges_json,
                    source_file_id,
                    raw_hash,
                ),
            )
        else:
            return False

        return cur.rowcount > 0

    def _recompute_aggregates(self, conn: sqlite3.Connection, session_pk: int) -> None:
        """Re-derive aggregate counters on writing_sessions from writing_deltas."""
        conn.execute(
            """UPDATE writing_sessions SET
                doc_change_count = (
                    SELECT COUNT(*) FROM writing_deltas
                    WHERE session_pk = ? AND event_type = 'doc-change'
                ),
                selection_change_count = (
                    SELECT COUNT(*) FROM writing_deltas
                    WHERE session_pk = ? AND event_type = 'selection-change'
                ),
                insert_count = (
                    SELECT COUNT(*) FROM writing_deltas
                    WHERE session_pk = ? AND event_type = 'doc-change'
                      AND inserted_text IS NOT NULL AND inserted_text != ''
                ),
                delete_count = (
                    SELECT COUNT(*) FROM writing_deltas
                    WHERE session_pk = ? AND event_type = 'doc-change'
                      AND deleted_text IS NOT NULL AND deleted_text != ''
                ),
                total_inserted_chars = (
                    SELECT COALESCE(SUM(LENGTH(inserted_text)), 0) FROM writing_deltas
                    WHERE session_pk = ? AND event_type = 'doc-change'
                ),
                total_deleted_chars = (
                    SELECT COALESCE(SUM(LENGTH(deleted_text)), 0) FROM writing_deltas
                    WHERE session_pk = ? AND event_type = 'doc-change'
                ),
                undo_count = (
                    SELECT COUNT(*) FROM writing_deltas
                    WHERE session_pk = ? AND user_event = 'undo'
                ),
                paste_count = (
                    SELECT COUNT(*) FROM writing_deltas
                    WHERE session_pk = ? AND user_event = 'input.paste'
                )
               WHERE id = ?""",
            (session_pk, session_pk, session_pk, session_pk,
             session_pk, session_pk, session_pk, session_pk, session_pk),
        )
