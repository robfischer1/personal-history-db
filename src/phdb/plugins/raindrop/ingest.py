"""Raindrop ingest helpers — WebPage upsert + BookmarkAction upsert.

Moved here from ``phdb.adapters.raindrop`` as part of Phase 5 of the
phdb Plugin Architecture plan. The shared helpers (upsert_web_page +
upsert_bookmark + hash_canonical) now live in ``phdb.formats.bookmark_upserts``
as of Phase 7; this module re-exports them for backward compatibility
within the raindrop plugin.
"""

from __future__ import annotations

from phdb.formats.bookmark_upserts import (
    hash_canonical_bookmark as hash_canonical,
)
from phdb.formats.bookmark_upserts import (
    upsert_bookmark,
    upsert_web_page,
)

__all__ = ["hash_canonical", "upsert_bookmark", "upsert_web_page"]
