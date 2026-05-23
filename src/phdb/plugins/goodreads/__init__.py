"""phdb.plugins.goodreads — Goodreads CSV library ingester.

Phase 7 brief 021 port of the phdb Plugin Architecture plan. Emits to
both the ``books`` and ``reviews`` typed tables (one source, multiple
@types) following the ``facebook_unified`` multi-emit precedent.
"""

from __future__ import annotations

from phdb.plugins.goodreads.plugin import GoodreadsPlugin

__all__ = ["GoodreadsPlugin"]
