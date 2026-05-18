"""BookmarkEvent — saved URLs from browsers and services."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class BookmarkEvent:
    """One bookmark save event."""

    provenance: Provenance
    url: str
    normalized_url: str
    date_added: str
    instrument: str
    title: str | None = None
    description: str | None = None
    tags: tuple[str, ...] = ()
    folder: str | None = None
    is_dead: bool | None = None
