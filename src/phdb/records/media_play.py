"""MediaPlay — music, podcast, and video play events."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class MediaPlay:
    """One media play event."""

    provenance: Provenance
    media_type: str
    title: str
    date_played: str
    platform: str
    artist: str | None = None
    album: str | None = None
    duration_ms: int | None = None
    platform_id: str | None = None
    is_skipped: bool = False
