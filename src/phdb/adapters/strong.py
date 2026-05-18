"""Strong workout adapter — ingests Strong4.sqlite from a decrypted iPhone backup.

Consumes ExerciseSet records grouped by WorkoutContext from phdb.formats.strong_sqlite.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.strong_sqlite import _format_weight, parse_workouts
from phdb.log import get_logger

log = get_logger("phdb.adapters.strong")


class StrongAdapter(Adapter):
    """Ingest Strong workout SQLite exports."""

    name = "strong"
    source_kind = "strong"
    file_kind = "sqlite"
    schema_type = "ExerciseAction"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        for ctx, sets in parse_workouts(source_path):
            body_parts = [f"Workout: {ctx.workout_name}"]

            if ctx.duration_seconds:
                body_parts.append(f"Duration: {ctx.duration_seconds // 60} min")
            if ctx.body_weight_kg and ctx.body_weight_kg > 0:
                bw = _format_weight(ctx.body_weight_kg)
                if bw:
                    body_parts.append(f"Body weight: {bw}")
            if ctx.notes:
                body_parts.append(f"Notes: {ctx.notes}")

            current_exercise = ""
            for s in sets:
                if s.exercise_name != current_exercise:
                    current_exercise = s.exercise_name
                    body_parts.append(f"\n{current_exercise}:")
                parts: list[str] = []
                if s.weight_kg and s.weight_kg > 0:
                    w = _format_weight(s.weight_kg)
                    if w:
                        parts.append(w)
                if s.reps and s.reps > 0:
                    parts.append(f"{s.reps} reps")
                if s.duration_seconds and s.duration_seconds > 0:
                    parts.append(f"{s.duration_seconds // 60}:{s.duration_seconds % 60:02d}")
                if s.distance_meters and s.distance_meters > 0:
                    parts.append(f"{s.distance_meters:.0f}m")
                body_parts.append(f"  Set {s.set_number}: " + (" × ".join(parts) if parts else "(body weight)"))

            body_text = "\n".join(body_parts)

            yield AdapterRow(
                schema_type="ExerciseAction",
                rfc822_message_id=f"strong:{ctx.z_pk}",
                subject=ctx.workout_name,
                sender_address="strong:self",
                sender_name="Strong",
                direction="self",
                date_sent=ctx.date_iso,
                body_text=body_text,
                body_text_source="strong-sqlite",
                raw_hash=ctx.raw_hash,
                body_text_hash=hashlib.sha256(body_text.encode()).hexdigest(),
                thread_key="strong:workouts",
            )
