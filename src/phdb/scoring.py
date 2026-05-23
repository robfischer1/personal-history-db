"""Backward-compatible shim — moved to ``phdb.core.scoring`` in Phase 1.

The canonical implementation lives at ``phdb.core.scoring``. This
module remains during the plugin-architecture migration; removed once
Phase 7 ports the last adapter consuming this surface.
"""

from __future__ import annotations

from phdb.core.scoring import (
    DecayConfig,
    TierConfig,
    batch_recompute,
    compute_score,
    decay_factor,
    populate_initial_scores,
    record_engagement,
    resolve_source_kind,
)

__all__ = [
    "DecayConfig",
    "TierConfig",
    "batch_recompute",
    "compute_score",
    "decay_factor",
    "populate_initial_scores",
    "record_engagement",
    "resolve_source_kind",
]
