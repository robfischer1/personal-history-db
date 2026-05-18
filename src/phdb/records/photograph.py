"""Photograph — camera images and video metadata."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class Photograph:
    """One photograph or video file metadata entry."""

    provenance: Provenance
    file_path: str
    file_name: str
    date_taken: str
    mime_type: str | None = None
    width: int | None = None
    height: int | None = None
    file_size: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    camera_model: str | None = None
    folder: str | None = None
