"""Provenance dataclass — shared across all typed records."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Provenance:
    """Origin metadata for a single record.

    Every typed record carries a Provenance instance tracing the exact
    byte range in the source file it was parsed from.
    """

    source_path: str
    raw_hash: str
    source_byte_offset: int | None = None
    source_byte_length: int | None = None
