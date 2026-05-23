"""phdb.plugins — first-party source plugins.

Phase 5+ deliverable of the phdb Plugin Architecture plan. Each
subdirectory holds one source plugin discovered via the
``phdb.plugins`` entry-point group + in-tree fallback. Plugins are
self-contained — they own their ingest logic, manifest, MCP tools,
and CLI subcommands.
"""

from __future__ import annotations
