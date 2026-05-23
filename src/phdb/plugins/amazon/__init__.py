"""phdb.plugins.amazon — Amazon Request-My-Data ZIP ingester.

Phase 7 brief 022 port of the phdb Plugin Architecture plan. Emits to
four typed tables (``products`` / ``order_actions`` / ``reviews`` /
``watch_actions``) from a single Amazon export ZIP, following the
``facebook_unified`` multi-emit precedent.

Replaces the legacy ``phdb.adapters.amazon`` module deleted in the same
commit per Phase 0 Q14 (no shim). Reuses the ``products`` +
``order_actions`` + ``reviews`` + ``watch_actions`` typed tables
introduced in migration 0021; no schema changes.
"""

from __future__ import annotations

from phdb.plugins.amazon.plugin import AmazonPlugin

__all__ = ["AmazonPlugin"]
