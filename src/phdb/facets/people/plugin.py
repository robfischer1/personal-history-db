"""PeopleFacetPlugin — identity coalescence over the persons entity table.

Phase 8A: the SkeletonFacetPlugin.coalesce() no-op is replaced by a
real implementation that drains the emission buffer, loads the rules
pack (instance file or bundled defaults), generates merge proposals,
auto-merges high-confidence (>= 0.90) ones, and buffers the rest for
the Phase 8C interactive review CLI.

DB writes only happen when ``coalesce()`` is called with a
``connection`` keyword — the no-connection path returns a dry-run
summary plus the in-memory proposal list. Tests and the Phase 4
buffer-shape suite continue to work because the base-class buffer
behavior is preserved.
"""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any

from phdb.facets._coalescence_lib import MergeProposal
from phdb.facets._review_queue import append_pending
from phdb.facets.base import SkeletonFacetPlugin
from phdb.facets.people.coalescence import (
    AUTO_MERGE_THRESHOLD,
    PEOPLE_FK_COLUMNS,
    PeopleCoalescer,
    coalesce_buffer_to_db,
)


class PeopleFacetPlugin(SkeletonFacetPlugin):
    """People facet — coalesces Person emissions into canonical persons rows."""

    def __init__(self, manifest) -> None:  # type: ignore[no-untyped-def]
        super().__init__(manifest)
        # Buffer of pending-review proposals — Phase 8C CLI reads from here.
        self.pending_review: list[MergeProposal] = []

    def coalesce(
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
            coalescer, source = PeopleCoalescer.from_instance(instance_dir)
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
                "status": "phase-8a-coalescer (dry-run; no connection)",
            }

        # Live mode — auto-merge + audit writes.
        # Default to PEOPLE_FK_COLUMNS when caller didn't pass a list
        # (Phase 8C Q3 — explicit per-facet constants beat introspection).
        effective_fks = fk_columns if fk_columns is not None else PEOPLE_FK_COLUMNS
        summary, pending = coalesce_buffer_to_db(
            connection,
            self.buffer,
            instance_dir=instance_dir,
            auto_merge_threshold=auto_merge_threshold,
            fk_columns=effective_fks,
        )
        self.pending_review.extend(pending)
        # Phase 8C: persist pending proposals so the review CLI can
        # consume them across process boundaries. No-op if no instance_dir.
        if instance_dir is not None and pending:
            for proposal in pending:
                # Defensive: don't crash coalesce() on disk errors.
                with contextlib.suppress(Exception):  # pragma: no cover
                    append_pending("people", instance_dir, proposal)
        # Drain the buffer — emissions are now represented in audit entries
        # (for auto-merged) or in self.pending_review (for low-confidence).
        self.buffer.clear()

        return {
            **base_summary,
            **summary.to_dict(),
            "status": "phase-8a-coalescer",
        }


__all__ = ["PeopleFacetPlugin"]
