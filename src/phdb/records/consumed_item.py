"""ConsumedItem — books, products, and other acquired items."""

from __future__ import annotations

from dataclasses import dataclass

from phdb.records.provenance import Provenance


@dataclass(frozen=True)
class ConsumedItem:
    """One consumed/acquired item (book, product)."""

    provenance: Provenance
    item_type: str
    title: str
    platform: str
    author: str | None = None
    isbn: str | None = None
    asin: str | None = None
    date_acquired: str | None = None
    date_consumed: str | None = None
    rating: float | None = None
    review_text: str | None = None
    shelves: tuple[str, ...] = ()
