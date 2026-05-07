"""Strong workout adapter — ingests Strong4.sqlite from a decrypted iPhone backup.

Source: Strong4.sqlite (Core Data).
Join chain: ZSWORKOUT -> ZSSETGROUP.ZWORKOUT -> ZSEXERCISESET.ZSETGROUP
            ZSEXERCISESET -> ZSEXERCISE via ZSSETGROUP.ZEXERCISE
Each workout becomes a schema_type='ExerciseAction' row. Single thread.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.strong")

APPLE_EPOCH_OFFSET = 978307200


def _apple_ts_to_iso(ts: float | None) -> str | None:
    if ts is None:
        return None
    try:
        unix = float(ts) + APPLE_EPOCH_OFFSET
        return datetime.fromtimestamp(unix, tz=UTC).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _format_weight(kg_val: float | None) -> str | None:
    if kg_val is None or kg_val == 0:
        return None
    lbs = kg_val * 2.20462
    return f"{round(lbs * 2) / 2:.1f} lbs"


def _build_workout_body(src_conn: sqlite3.Connection, workout_pk: int) -> str:
    lines: list[str] = []
    groups = src_conn.execute(
        """SELECT sg.Z_PK, e.ZNAME AS exercise_name
             FROM ZSSETGROUP sg
             LEFT JOIN ZSEXERCISE e ON e.Z_PK = sg.ZEXERCISE
            WHERE sg.ZWORKOUT = ?
            ORDER BY sg.ZSUPERSETORDER, sg.Z_PK""",
        (workout_pk,),
    ).fetchall()

    for g_pk, exercise_name in groups:
        lines.append(f"\n{exercise_name or 'Unknown Exercise'}:")
        sets = src_conn.execute(
            """SELECT ZKILOGRAMS, ZREPS, ZSECONDS, ZMETERS, ZRPE
                 FROM ZSEXERCISESET WHERE ZSETGROUP = ? ORDER BY Z_PK""",
            (g_pk,),
        ).fetchall()
        for i, (kg, reps, secs, meters, rpe) in enumerate(sets, 1):
            parts: list[str] = []
            if kg and kg > 0:
                w = _format_weight(kg)
                if w:
                    parts.append(w)
            if reps and reps > 0:
                parts.append(f"{int(reps)} reps")
            if secs and secs > 0:
                s = int(secs)
                parts.append(f"{s // 60}:{s % 60:02d}")
            if meters and meters > 0:
                parts.append(f"{meters:.0f}m")
            if rpe and rpe > 0:
                parts.append(f"RPE {rpe:.1f}")
            lines.append(f"  Set {i}: " + (" × ".join(parts) if parts else "(body weight)"))

    return "\n".join(lines)


class StrongAdapter(Adapter):
    """Ingest Strong workout SQLite exports."""

    name = "strong"
    source_kind = "strong"
    file_kind = "sqlite"
    schema_type = "ExerciseAction"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        src = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
        try:
            workouts = src.execute(
                """SELECT Z_PK, ZSTARTDATE, ZCOMPLETIONDATE, ZNAME, ZNOTES,
                          ZBODYWEIGHTVALUE
                     FROM ZSWORKOUT
                    WHERE ZSTARTDATE IS NOT NULL
                    ORDER BY ZSTARTDATE"""
            ).fetchall()

            for z_pk, start_date, completion_date, wname, notes, body_weight in workouts:
                date_iso = _apple_ts_to_iso(start_date)
                if not date_iso:
                    continue

                workout_name = wname or "Workout"
                body_parts = [f"Workout: {workout_name}"]

                if completion_date and start_date:
                    duration_s = int(completion_date - start_date)
                    body_parts.append(f"Duration: {duration_s // 60} min")

                if body_weight and body_weight > 0:
                    bw = _format_weight(body_weight)
                    if bw:
                        body_parts.append(f"Body weight: {bw}")

                if notes:
                    body_parts.append(f"Notes: {notes}")

                exercises = _build_workout_body(src, z_pk)
                body_text = "\n".join(body_parts) + exercises

                msg_id = f"strong:{z_pk}"
                raw_hash = hashlib.sha256(msg_id.encode()).hexdigest()

                yield AdapterRow(
                    schema_type="ExerciseAction",
                    rfc822_message_id=msg_id,
                    subject=workout_name,
                    sender_address="strong:self",
                    sender_name="Strong",
                    direction="self",
                    date_sent=date_iso,
                    body_text=body_text,
                    body_text_source="strong-sqlite",
                    raw_hash=raw_hash,
                    body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
                    thread_key="strong:workouts",
                )
        finally:
            src.close()
