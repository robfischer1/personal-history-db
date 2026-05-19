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
    # Extra fields carried through from format-specific parsers for DB storage.
    note: str | None = None
    excerpt: str | None = None
    cover_url: str | None = None
    favorite: bool = False
    highlights: str | None = None
    raindrop_id: str | None = None
