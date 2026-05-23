"""phdb.plugins.clippings — vault clippings + reddit posts markdown ingester.

Phase 7 brief 025 port of the phdb Plugin Architecture plan. Replaces
the legacy ``phdb.adapters.clippings`` module deleted in the same commit
per Phase 0 Q14 (no shim). Reuses the ``clippings`` typed table
(migration 0017); no schema changes.
"""

from __future__ import annotations

from phdb.plugins.clippings.plugin import ClippingsPlugin

__all__ = ["ClippingsPlugin"]
