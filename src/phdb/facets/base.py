"""Shared facet plugin scaffolding — audit log table + skeleton consume.

Phase 4 deliverable. The ``facet_coalescence_log`` audit table records
every merge proposal a facet plugin makes; ``phdb facet <name>
unmerge <id>`` (Phase 8) reads from it. The table is created
opportunistically by ``ensure_audit_log`` on first ingest hook so we
don't need a migration file at Phase 4.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from phdb.core.plugin import PhdbFacetPlugin
from phdb.core.plugin.bus import FacetEmission

AUDIT_LOG_DDL = """
CREATE TABLE IF NOT EXISTS facet_coalescence_log (
    id              INTEGER PRIMARY KEY,
    facet_type      TEXT NOT NULL,
    facet_node_id   INTEGER NOT NULL,
    rule_name       TEXT,
    confidence      REAL,
    source_table    TEXT,
    source_id       INTEGER,
    payload         TEXT,
    created_at      TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
)
"""

AUDIT_LOG_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS idx_facet_coalescence_log_facet
    ON facet_coalescence_log(facet_type, facet_node_id)
"""


def ensure_audit_log(conn: sqlite3.Connection) -> None:
    """Idempotent — create facet_coalescence_log if missing."""
    conn.execute(AUDIT_LOG_DDL)
    conn.execute(AUDIT_LOG_INDEX_DDL)
    conn.commit()


class SkeletonFacetPlugin(PhdbFacetPlugin):
    """Phase 4 default impl — accepts emissions into an in-memory buffer.

    Phase 8 replaces this with the full rules-engine implementation.
    The buffer makes facet plugins testable in Phase 4 without
    requiring the full coalescence logic to exist.
    """

    def __init__(self, manifest) -> None:  # type: ignore[no-untyped-def]
        super().__init__(manifest)
        self.buffer: list[FacetEmission] = []

    def consume(self, emission: FacetEmission) -> None:
        self.buffer.append(emission)

    def coalesce(self) -> dict[str, Any]:
        """Phase 4: returns a summary of how many emissions were buffered.

        Phase 8 replaces this with the real rules-engine coalescer
        (TOML rules + manual overrides + audit log writes).
        """
        return {
            "buffered_emissions": len(self.buffer),
            "facet_type": self.manifest.facet.consumes if self.manifest.facet else None,
            "node_table": self.manifest.facet.node_table if self.manifest.facet else None,
            "status": "skeleton — full coalescer lands in Phase 8",
        }

    def register_cli(self, parser: Any) -> None:
        """Skeleton — Phase 8 wires per-facet CLI commands."""
        return None

    def register_tools(self, server: Any) -> None:
        """Skeleton — Phase 5+ wires per-facet MCP tools."""
        return None


__all__ = [
    "AUDIT_LOG_DDL",
    "AUDIT_LOG_INDEX_DDL",
    "SkeletonFacetPlugin",
    "ensure_audit_log",
]
