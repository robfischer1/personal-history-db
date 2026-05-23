"""People-facet coalescence — identity rules engine over Person emissions.

Phase 8A. Extends ``phdb.facets._coalescence_lib.Coalescer`` with
people-specific predicates and helpers. Place-specific predicates
(haversine geo-radius, named-place resolution) live in
``phdb.facets.places.coalescence`` (Phase 8B); both subclasses share
the same ``CoalescenceRule`` / ``MergeProposal`` / ``AuditEntry``
contract from the shared lib.

Predicate vocabulary (default rules pack — overridable per-instance):

- ``exact_email_local_domain`` (0.95) — same lowercased email
  ``local@domain`` ⇒ same person. The strongest single signal.
- ``phone_e164`` (0.95) — same E.164-normalized phone number ⇒
  same person.
- ``discord_handle`` (0.85) — same case-insensitive Discord handle
  ⇒ same person.
- ``same_full_name_same_email_domain`` (0.75) — same lowercased
  full name + same email domain ⇒ same person. Medium confidence
  (collides on common names in shared org domains).
- ``same_first_last`` (0.40) — same first + last name ⇒ same
  person. Lowest confidence, ``require_manual_review = true``;
  surfaced to the Phase 8C review CLI rather than auto-merged.

Auto-merge threshold: ``>= 0.90``. Below that, proposals are
buffered for interactive review (Phase 8C consumes the buffer).

Rule loading order:
1. ``personal-history-instance/identity_rules.toml`` — per-instance
   overrides (highest priority).
2. Bundled defaults baked into ``DEFAULT_PEOPLE_RULES`` below
   (fallback when no instance file is found).

The instance file path is overridable via the ``Settings.instance_dir``
attribute. To override or extend the bundled rule pack, copy
``src/phdb/templates/identity_rules.template.toml`` into your
instance dir and adjust.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.facets._coalescence_lib import (
    Coalescer,
    CoalescenceRule,
    MergeProposal,
    apply_merge,
    load_rules_from_dicts,
    load_rules_from_toml,
)
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.core.plugin.bus import FacetEmission

log = get_logger("phdb.facets.people.coalescence")


AUTO_MERGE_THRESHOLD = 0.90

# Bundled defaults — used when the instance has no identity_rules.toml.
DEFAULT_PEOPLE_RULES: list[dict[str, Any]] = [
    {
        "name": "exact_email_local_domain",
        "shape": "exact_field",
        "field": "email",
        "normalize": "lowercase",
        "confidence": 0.95,
        "notes": "Same lowercased email = same person (strongest signal).",
    },
    {
        "name": "phone_e164",
        "shape": "exact_field",
        "field": "phone",
        "normalize": "e164",
        "confidence": 0.95,
        "notes": "Same E.164-normalized phone = same person.",
    },
    {
        "name": "discord_handle",
        "shape": "exact_field",
        "field": "discord_handle",
        "normalize": "lowercase",
        "confidence": 0.85,
        "notes": "Same case-insensitive Discord handle = same person.",
    },
    {
        "name": "same_full_name_same_email_domain",
        "shape": "two_field",
        "fields": ["full_name", "email_domain"],
        "normalize": "lowercase",
        "confidence": 0.75,
        "notes": "Medium confidence — collides on common names in shared domains.",
    },
    {
        "name": "same_first_last",
        "shape": "two_field",
        "fields": ["first_name", "last_name"],
        "normalize": "lowercase",
        "confidence": 0.40,
        "require_manual_review": True,
        "notes": "Lowest confidence; routed to Phase 8C review CLI.",
    },
]


@dataclass
class CoalesceSummary:
    """Structured return value of ``PeopleCoalescer.coalesce_batch_db``."""

    emissions_processed: int = 0
    proposals_generated: int = 0
    auto_merged: int = 0
    pending_review: int = 0
    audit_entries_written: int = 0
    rules_loaded: int = 0
    source: str = "bundled-defaults"

    def to_dict(self) -> dict[str, Any]:
        return {
            "emissions_processed": self.emissions_processed,
            "proposals_generated": self.proposals_generated,
            "auto_merged": self.auto_merged,
            "pending_review": self.pending_review,
            "audit_entries_written": self.audit_entries_written,
            "rules_loaded": self.rules_loaded,
            "rules_source": self.source,
        }


def _enrich_emission(emission: Any) -> Any:
    """Derive convenience fields from an emission payload.

    The bundled rules reference ``email_domain``, ``first_name``, and
    ``last_name`` — but emissions might arrive with just ``email`` and
    ``full_name``. We derive missing fields in-place (on a copy) so
    the predicates can read them uniformly.

    Returns a shallow-cloned ``FacetEmission`` (or dict) with the
    enriched payload; never mutates the input.
    """
    from phdb.core.plugin.bus import FacetEmission

    payload = dict(getattr(emission, "payload", None) or {})
    if isinstance(emission, dict):
        payload = {**emission}

    email = payload.get("email")
    if email and "email_domain" not in payload:
        if "@" in str(email):
            payload["email_domain"] = str(email).rsplit("@", 1)[-1].lower()

    full_name = payload.get("full_name")
    if full_name and ("first_name" not in payload or "last_name" not in payload):
        parts = str(full_name).strip().split()
        if parts:
            payload.setdefault("first_name", parts[0])
            payload.setdefault("last_name", parts[-1] if len(parts) > 1 else "")

    if isinstance(emission, FacetEmission):
        return FacetEmission(
            source_table=emission.source_table,
            source_id=emission.source_id,
            facet_type=emission.facet_type,
            payload=payload,
        )
    # Dict path
    return payload


@dataclass
class PeopleCoalescer(Coalescer):
    """Coalescer specialized for Person emissions.

    Same proposal-generation API as the base class; the people-specific
    work is in enrichment (deriving ``email_domain`` / ``first_name``
    / ``last_name`` from raw payloads) and in the default rules pack.
    """

    def evaluate_pair(self, a: Any, b: Any) -> CoalescenceRule | None:  # type: ignore[override]
        return super().evaluate_pair(_enrich_emission(a), _enrich_emission(b))

    def coalesce_batch(
        self,
        emissions: list[Any],
        *,
        existing_nodes: Any = (),
    ) -> list[MergeProposal]:
        """Enrich emissions then defer to the base coalescer."""
        enriched_emissions = [_enrich_emission(e) for e in emissions]
        enriched_existing = [_enrich_emission(n) for n in existing_nodes]
        return super().coalesce_batch(
            enriched_emissions, existing_nodes=enriched_existing,
        )

    @classmethod
    def from_instance(
        cls,
        instance_dir: Path | None = None,
        *,
        threshold: float = 0.0,
    ) -> tuple[PeopleCoalescer, str]:
        """Construct a coalescer from instance config (or bundled defaults).

        Returns ``(coalescer, rules_source)`` — the source string is
        ``"instance:<path>"`` when an instance file was loaded, or
        ``"bundled-defaults"`` when falling back.
        """
        rules: list[CoalescenceRule] = []
        source = "bundled-defaults"
        if instance_dir is not None:
            rules_path = instance_dir / "identity_rules.toml"
            if rules_path.exists():
                rules = load_rules_from_toml(rules_path, facet="people")
                if rules:
                    source = f"instance:{rules_path}"
        if not rules:
            rules = load_rules_from_dicts(DEFAULT_PEOPLE_RULES)
        return cls(rules=rules, threshold=threshold), source


def coalesce_buffer_to_db(
    conn: sqlite3.Connection,
    emissions: list[Any],
    *,
    instance_dir: Path | None = None,
    auto_merge_threshold: float = AUTO_MERGE_THRESHOLD,
    fk_columns: list[tuple[str, str]] | None = None,
) -> tuple[CoalesceSummary, list[MergeProposal]]:
    """End-to-end: load rules, generate proposals, auto-merge high-conf, return pending.

    Returns ``(summary, pending_review_proposals)``. The caller (the
    facet plugin's ``coalesce()`` method) decides what to do with the
    pending list — the Phase 8C review CLI consumes it; an auto-only
    pipeline can drop it.

    ``fk_columns`` lists ``(table, column)`` pairs holding ``persons.id``
    FKs that the merge must rewrite. Default: empty list — the apply
    step will run but FK rewrites are no-ops (safe; the Phase 4
    skeleton corpus doesn't yet have downstream FK consumers).
    """
    coalescer, source = PeopleCoalescer.from_instance(instance_dir)
    proposals = coalescer.coalesce_batch(emissions)

    summary = CoalesceSummary(
        emissions_processed=len(emissions),
        proposals_generated=len(proposals),
        rules_loaded=len(coalescer.rules),
        source=source,
    )

    pending: list[MergeProposal] = []
    rule_lookup = {r.name: r for r in coalescer.rules}

    for proposal in proposals:
        rule = rule_lookup.get(proposal.rule)
        require_review = rule.require_manual_review if rule else False
        if proposal.confidence >= auto_merge_threshold and not require_review:
            # Auto-merge — but only if we have an existing node id to
            # merge into. proposal.into_node_id == -1 means "would
            # create a new node"; the new-node creation path lives in
            # the source plugin (it already created the persons rows),
            # so for auto-merge we only act when into_node_id >= 0.
            if proposal.into_node_id >= 0:
                try:
                    apply_merge(
                        conn,
                        node_table="persons",
                        proposal=proposal,
                        facet_type="Person",
                        fk_columns=fk_columns,
                    )
                    summary.auto_merged += 1
                    summary.audit_entries_written += 1
                except Exception as exc:  # pragma: no cover - defensive
                    log.warning("auto-merge failed: %s", exc)
                    pending.append(proposal)
                    summary.pending_review += 1
            else:
                # Nothing to merge into — emission-only equivalence.
                # Surface to review so the operator can pick a survivor.
                pending.append(proposal)
                summary.pending_review += 1
        else:
            pending.append(proposal)
            summary.pending_review += 1

    return summary, pending


__all__ = [
    "AUTO_MERGE_THRESHOLD",
    "CoalesceSummary",
    "DEFAULT_PEOPLE_RULES",
    "PeopleCoalescer",
    "coalesce_buffer_to_db",
]
