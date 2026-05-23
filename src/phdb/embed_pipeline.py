"""Backward-compatible shim — moved to ``phdb.core.embed`` in Phase 1.

The canonical implementation lives at ``phdb.core.embed``. This module
remains during the plugin-architecture migration; removed once Phase 7
ports the last adapter consuming this surface.
"""

from __future__ import annotations

from phdb.core.embed import (
    DEFAULT_BATCH_SIZE,
    MIN_CHUNK_CHARS,
    OVERLAP_CHARS,
    TARGET_CHUNK_CHARS,
    EmbedProgress,
    EmbedResult,
    EmbedStatus,
    ProgressCallback,
    chunk_text,
    get_embed_status,
    run_embed_pipeline,
)
from phdb.embed_service import EmbedClient  # noqa: F401 — re-export for backwards compat

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "EmbedClient",
    "EmbedProgress",
    "EmbedResult",
    "EmbedStatus",
    "MIN_CHUNK_CHARS",
    "OVERLAP_CHARS",
    "ProgressCallback",
    "TARGET_CHUNK_CHARS",
    "chunk_text",
    "get_embed_status",
    "run_embed_pipeline",
]
