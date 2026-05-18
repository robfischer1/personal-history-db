"""GeoTrace — location visits, activities, and paths."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class GeoTrace:
    """One location trace (visit, path, or activity segment)."""

    provenance: Provenance
    trace_type: str
    date_start: str
    date_end: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    place_name: str | None = None
    place_address: str | None = None
    activity_type: str | None = None
    confidence: float | None = None
    waypoints: tuple[tuple[float, float, str], ...] = ()
