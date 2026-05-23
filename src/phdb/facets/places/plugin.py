"""PlacesFacetPlugin — geographic coalescence over the places entity table.

Phase 8B: the SkeletonFacetPlugin.coalesce() no-op is replaced by a
real implementation that drains the emission buffer, loads the rules
pack (instance file or bundled defaults), generates merge proposals
via haversine geo-radius + named-location predicates, auto-merges
high-confidence (>= 0.90) ones, and buffers the rest for the Phase 8C
interactive review CLI.

DB writes only happen when ``coalesce()`` is called with a
``connection`` keyword — the no-connection path returns a dry-run
summary plus the in-memory proposal list. Tests and the Phase 4
buffer-shape suite continue to work because the base-class buffer
behavior is preserved.

Mirrors ``PeopleFacetPlugin`` exactly so the Phase 8C review CLI
treats both facets through one uniform interface (same return-shape
keys, same dry-run vs live split, same ``pending_review`` buffer).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from phdb.facets._coalescence_lib import MergeProposal
from phdb.facets.base import SkeletonFacetPlugin
from phdb.facets.places.coalescence import (
    AUTO_MERGE_THRESHOLD,
    PlacesCoalescer,
    coalesce_buffer_to_db,
)


class PlacesFacetPlugin(SkeletonFacetPlugin):
    """Places facet — coalesces Place emissions into canonical places rows."""

    def __init__(self, manifest) -> None:  # type: ignore[no-untyped-def]
        super().__init__(manifest)
        # Buffer of pending-review proposals — Phase 8C CLI reads from here.
        self.pending_review: list[MergeProposal] = []

    def coalesce(  # type: ignore[override]
        self,
        *,
        connection: sqlite3.Connection | None = None,
        instance_dir: Path | None = None,
        fk_columns: list[tuple[str, str]] | None = None,
        auto_merge_threshold: float = AUTO_MERGE_THRESHOLD,
    ) -> dict[str, Any]:
        """Drain buffer; generate proposals; auto-merge high-confidence.

        Returns a structured summary dict. Always-present keys:

        - ``emissions_processed``
        - ``proposals_generated``
        - ``auto_merged``
        - ``pending_review``
        - ``audit_entries_written``
        - ``rules_loaded``
        - ``rules_source``
        - ``facet_type`` / ``node_table`` — for parity with the
          skeleton's return shape.

        With ``connection=None`` the proposals are generated but no
        DB writes happen (dry-run mode). With a connection, auto-merge
        runs and audit log gets populated.
        """
        # Carry forward Phase 4 buffer-only behavior keys for back-compat.
        base_summary = super().coalesce()

        if connection is None:
            coalescer, source = PlacesCoalescer.from_instance(instance_dir)
            proposals = coalescer.coalesce_batch(self.buffer)
            # Classify but don't write.
            rule_lookup = {r.name: r for r in coalescer.rules}
            auto_count = 0
            for proposal in proposals:
                rule = rule_lookup.get(proposal.rule)
                require_review = rule.require_manual_review if rule else False
                if (
                    proposal.confidence >= auto_merge_threshold
                    and not require_review
                    and proposal.into_node_id >= 0
                ):
                    auto_count += 1
                else:
                    self.pending_review.append(proposal)
            return {
                **base_summary,
                "emissions_processed": len(self.buffer),
                "proposals_generated": len(proposals),
                "auto_merged": 0,  # dry-run — no writes
                "would_auto_merge": auto_count,
                "pending_review": len(self.pending_review),
                "audit_entries_written": 0,
                "rules_loaded": len(coalescer.rules),
                "rules_source": source,
                "dry_run": True,
                "status": "phase-8b-coalescer (dry-run; no connection)",
            }

        # Live mode — auto-merge + audit writes.
        summary, pending = coalesce_buffer_to_db(
            connection,
            self.buffer,
            instance_dir=instance_dir,
            auto_merge_threshold=auto_merge_threshold,
            fk_columns=fk_columns,
        )
        self.pending_review.extend(pending)
        # Drain the buffer — emissions are now represented in audit entries
        # (for auto-merged) or in self.pending_review (for low-confidence).
        self.buffer.clear()

        return {
            **base_summary,
            **summary.to_dict(),
            "status": "phase-8b-coalescer",
        }


__all__ = ["PlacesFacetPlugin"]
