"""phdb.plugins.writing_deltas — obsidian-delta-stream NDJSON ingester.

Phase 7 epilogue port of the legacy ``phdb.adapters.writing_deltas``
adapter. Predates the formal Phase 7 brief queue (was Phase 0 Q15's
pilot pick before being overruled by raindrop) so it ports last,
closing the loop on the adapter retirement.

Owns the ``writing_sessions`` + ``writing_deltas`` typed tables
created in migration 0015. Queries land in ``phdb.query`` for now
(the source-specific layer dissolves in a later phase).
"""

from __future__ import annotations

from phdb.plugins.writing_deltas.plugin import WritingDeltasPlugin

__all__ = ["WritingDeltasPlugin"]
