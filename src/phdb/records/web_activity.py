"""WebActivity — searches, page visits, video watches."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class WebActivity:
    """One web activity event (search, watch, visit)."""

    provenance: Provenance
    activity_type: str
    date_performed: str
    platform: str
    url: str | None = None
    title: str | None = None
    query: str | None = None
    duration_seconds: int | None = None
