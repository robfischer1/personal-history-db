"""HealthObservation — biometric measurements and health metrics."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class HealthObservation:
    """One health/fitness measurement."""

    provenance: Provenance
    observation_type: str
    date_start: str
    value: float | None = None
    unit: str | None = None
    date_end: str | None = None
    source_device: str | None = None
    metadata: tuple[tuple[str, str], ...] = ()
