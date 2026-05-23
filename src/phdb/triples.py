"""Backward-compatible shim — moved to ``phdb.core.graph`` in Phase 1.

The canonical implementation lives at ``phdb.core.graph``. This module
remains during the plugin-architecture migration; removed once Phase 7
ports the last adapter consuming this surface.
"""

from __future__ import annotations

from phdb.core.graph import (
    GraphService,
    add_qualifier,
    add_triple,
    emit_for_frontmatter,
    get_predicate,
    get_qualifiers,
    list_predicates,
    node_neighborhood,
    query_triples,
    resolve_node,
    resolve_node_for_wikilink,
    triple_stats,
)

__all__ = [
    "GraphService",
    "add_qualifier",
    "add_triple",
    "emit_for_frontmatter",
    "get_predicate",
    "get_qualifiers",
    "list_predicates",
    "node_neighborhood",
    "query_triples",
    "resolve_node",
    "resolve_node_for_wikilink",
    "triple_stats",
]
