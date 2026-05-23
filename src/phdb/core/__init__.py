"""phdb.core — source-agnostic infrastructure.

Holds DB connection factory, embedding pipeline, scoring engine, graph
service, hybrid retrieval, plugin discovery + registry. Nothing in this
package may import from ``phdb.adapters`` or ``phdb.plugins`` — core
ships independently of any source.

Phase 1 deliverable of the phdb Plugin Architecture plan (2026-05-22):
the package exists and the canonical modules live here. Legacy import
paths (``phdb.db``, ``phdb.embed_pipeline``, ``phdb.scoring``,
``phdb.triples``) survive as re-exports during the multi-phase port and
go away in Phase 7 when adapters land as plugins.
"""

from __future__ import annotations
