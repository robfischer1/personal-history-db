"""ExerciseSet — individual workout sets (sub-record of HealthObservation)."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class ExerciseSet:
    """One exercise set within a workout."""

    provenance: Provenance
    exercise_name: str
    date_performed: str
    parent_id: str | None = None
    set_number: int | None = None
    reps: int | None = None
    weight_kg: float | None = None
    duration_seconds: int | None = None
    distance_meters: float | None = None
    workout_name: str | None = None
