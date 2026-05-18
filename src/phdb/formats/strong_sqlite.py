"""Strong SQLite format parser — yields ExerciseSet records grouped by workout.

Parses Strong4.sqlite (Core Data) from decrypted iPhone backups.
Yields ExerciseSet records, each carrying workout-level context via parent_id and workout_name.
Pure parser: no DB (destination), no identity.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from phdb.records import ExerciseSet, Provenance

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


@dataclass(frozen=True)
class WorkoutContext:
    """Per-workout metadata for grouping sets."""

    z_pk: int
    workout_name: str
    date_iso: str
    duration_seconds: int | None
    body_weight_kg: float | None
    notes: str | None
    raw_hash: str


def parse_workouts(source_path: Path) -> Iterator[tuple[WorkoutContext, list[ExerciseSet]]]:
    """Parse Strong4.sqlite, yielding (WorkoutContext, sets) per workout."""
    source_str = str(source_path)
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
            msg_id = f"strong:{z_pk}"
            raw_hash = hashlib.sha256(msg_id.encode()).hexdigest()

            duration_s = None
            if completion_date and start_date:
                duration_s = int(completion_date - start_date)

            ctx = WorkoutContext(
                z_pk=z_pk,
                workout_name=workout_name,
                date_iso=date_iso,
                duration_seconds=duration_s,
                body_weight_kg=body_weight,
                notes=notes,
                raw_hash=raw_hash,
            )

            groups = src.execute(
                """SELECT sg.Z_PK, e.ZNAME AS exercise_name
                     FROM ZSSETGROUP sg
                     LEFT JOIN ZSEXERCISE e ON e.Z_PK = sg.ZEXERCISE
                    WHERE sg.ZWORKOUT = ?
                    ORDER BY sg.ZSUPERSETORDER, sg.Z_PK""",
                (z_pk,),
            ).fetchall()

            sets: list[ExerciseSet] = []
            set_num = 0
            for g_pk, exercise_name in groups:
                rows = src.execute(
                    """SELECT ZKILOGRAMS, ZREPS, ZSECONDS, ZMETERS, ZRPE
                         FROM ZSEXERCISESET WHERE ZSETGROUP = ? ORDER BY Z_PK""",
                    (g_pk,),
                ).fetchall()
                for kg, reps, secs, meters, _rpe in rows:
                    set_num += 1
                    set_hash = hashlib.sha256(f"strong:{z_pk}:set:{set_num}".encode()).hexdigest()
                    sets.append(ExerciseSet(
                        provenance=Provenance(source_path=source_str, raw_hash=set_hash),
                        exercise_name=exercise_name or "Unknown Exercise",
                        date_performed=date_iso,
                        parent_id=raw_hash,
                        set_number=set_num,
                        reps=int(reps) if reps and reps > 0 else None,
                        weight_kg=float(kg) if kg and kg > 0 else None,
                        duration_seconds=int(secs) if secs and secs > 0 else None,
                        distance_meters=float(meters) if meters and meters > 0 else None,
                        workout_name=workout_name,
                    ))

            yield ctx, sets
    finally:
        src.close()
