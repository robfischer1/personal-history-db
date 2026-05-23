"""Backward-compatible shim — moved to ``phdb.core.db`` in Phase 1.

The canonical implementation lives at ``phdb.core.db``. This module
remains during the plugin-architecture migration so that the 30+
unported adapters keep importing ``from phdb.db import connect``
without ripple. Removed when the last adapter ports to a plugin in
Phase 7.
"""

from __future__ import annotations

from phdb.core.db import (
    VECTOR_DIM,
    _apply_pragmas,
    _load_vec_ext,
    connect,
    connect_persistent,
    ensure_vec_table,
)

__all__ = [
    "VECTOR_DIM",
    "_apply_pragmas",
    "_load_vec_ext",
    "connect",
    "connect_persistent",
    "ensure_vec_table",
]
