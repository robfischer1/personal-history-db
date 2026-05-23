"""Raindrop ingest helpers — re-exports shared bookmark upserts.

The core logic moved to ``phdb.formats.bookmark_upserts`` in Phase 7 to
support the apple_dbs plugin port.
"""

from __future__ import annotations

from phdb.formats.bookmark_upserts import (
    hash_canonical_bookmark as hash_canonical,
    upsert_bookmark,
    upsert_web_page,
)

__all__ = ["hash_canonical", "upsert_bookmark", "upsert_web_page"]
