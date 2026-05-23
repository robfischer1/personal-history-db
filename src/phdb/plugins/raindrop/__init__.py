"""phdb.plugins.raindrop — Raindrop.io bookmarks ingester.

Phase 5 pilot port of the phdb Plugin Architecture plan
(2026-05-22). First plugin to land under the new contract; validates
the entity-FK pattern from the WebPage Entity Factoring precedent.
"""

from __future__ import annotations

from phdb.plugins.raindrop.plugin import RaindropPlugin

__all__ = ["RaindropPlugin"]
