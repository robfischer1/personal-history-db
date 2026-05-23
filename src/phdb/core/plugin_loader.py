"""Backward-compatible shim — moved to ``phdb.core.plugin.loader`` in Phase 3.

The canonical implementation lives at ``phdb.core.plugin.loader``.
This module remains so the Phase 1 deliverable contract
``from phdb.core import plugin_loader`` keeps working.
"""

from __future__ import annotations

from phdb.core.plugin.loader import (
    ENTRY_POINT_GROUP,
    FACET_ENTRY_POINT_GROUP,
    PluginDescriptor,
    discover_facets,
    discover_plugins,
    load_plugin,
    validate_plugin,
)

__all__ = [
    "ENTRY_POINT_GROUP",
    "FACET_ENTRY_POINT_GROUP",
    "PluginDescriptor",
    "discover_facets",
    "discover_plugins",
    "load_plugin",
    "validate_plugin",
]
