"""Places-facet coalescence — identity rules engine over Place emissions.

Phase 8B. Extends ``phdb.facets._coalescence_lib.Coalescer`` with
place-specific predicates and helpers. People-specific predicates
(email, phone, Discord handle) live in
``phdb.facets.people.coalescence`` (Phase 8A); both subclasses share
the same ``CoalescenceRule`` / ``MergeProposal`` / ``AuditEntry``
contract from the shared lib.

Predicate vocabulary (default rules pack — overridable per-instance):

- ``geo_within_100m`` (0.85) — two emissions whose lat/lon are within
  100 meters (great-circle distance via haversine) are taken to be
  the same place. The tight default radius reflects EXIF/GPS jitter
  for stationary observations.
- ``geo_within_500m`` (0.65) — wider radius for handheld captures
  with degraded GPS lock; ``require_manual_review = true`` so the
  Phase 8C review CLI gates these even when the rule fires.
- ``named_location_exact`` (0.80) — same lowercased + whitespace-
  stripped place name = same place. Useful when GPS is missing but
  the source labeled the place (e.g., Google Timeline visits).

Auto-merge threshold: ``>= 0.90``. Below that, proposals are
buffered for interactive review (Phase 8C consumes the buffer).
The bundled default pack ships nothing above the threshold —
the operator is expected to tune radius / confidence per-instance.

Rule loading order:
1. ``personal-history-instance/identity_rules.toml`` — per-instance
   overrides (highest priority).
2. Bundled defaults baked into ``DEFAULT_PLACES_RULES`` below
   (fallback when no instance file is found).

The instance file path is overridable via the ``Settings.instance_dir``
attribute. To override or extend the bundled rule pack, copy
``src/phdb/templates/identity_rules.template.toml`` into your
instance dir and adjust.

Predicate-builder extension pattern (per Phase 8A's note: shared lib
ships a placeholder for ``geo_radius_meters``; the real haversine
math lives here). ``PlacesCoalescer`` installs a module-local
predicate-builder lookup that overrides the shared one for two
shapes (``geo_radius_meters`` + ``named_location_exact``) without
mutating the shared module's ``_PREDICATE_BUILDERS`` dict.
"""

from __future__ import annotations

import math
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.facets._coalescence_lib import (
    Coalescer,
    CoalescenceRule,
    MergeProposal,
    _emission_field,
    _normalize,
    apply_merge,
    build_predicate,
)
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.core.plugin.bus import FacetEmission

log = get_logger("phdb.facets.places.coalescence")


AUTO_MERGE_THRESHOLD = 0.90

# Mean Earth radius in meters (IUGG mean radius, per Wikipedia haversine reference).
_EARTH_RADIUS_M = 6_371_000.0

# Bundled defaults — used when the instance has no identity_rules.toml.
DEFAULT_PLACES_RULES: list[dict[str, Any]] = [
    {
        "name": "geo_within_100m",
        "shape": "geo_radius_meters",
        "radius_m": 100.0,
        "confidence": 0.85,
        "notes": "Same coords within 100m (haversine) = same place.",
    },
    {
        "name": "geo_within_500m",
        "shape": "geo_radius_meters",
        "radius_m": 500.0,
        "confidence": 0.65,
        "require_manual_review": True,
        "notes": "Wider radius for degraded GPS; manual review required.",
    },
    {
        "name": "named_location_exact",
        "shape": "named_location_exact",
        "field": "name",
        "normalize": "lowercase_strip",
        "confidence": 0.80,
        "notes": "Same normalized place name = same place.",
    },
]


# ---------------------------------------------------------------------------
# Places-specific predicate builders
# ---------------------------------------------------------------------------


def _haversine_meters(
    lat1: float, lon1: float, lat2: float, lon2: float,
) -> float:
    """Great-circle distance in meters between two lat/lon points.

    Implements the haversine formula per Wikipedia's authoritative
    reference (great-circle distance). Inputs are degrees; output is
    meters using the IUGG mean Earth radius (6,371,000 m).

    Edge case: identical coords return 0.0 exactly (sin(0)==0, so
    haversine reduces cleanly).
    """
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return _EARTH_RADIUS_M * c


def _coerce_float(value: Any) -> float | None:
    """Best-effort float coercion. Returns None for un-parseable input."""
    if value is None:
        return None
    if isinstance(value, bool):
        # bools are ints — refuse them; lat/lon shouldn't be booleans.
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _geo_radius_predicate(
    radius_m: float,
    *,
    lat_field: str = "lat",
    lon_field: str = "lon",
) -> Callable[[Any, Any], bool]:
    """Haversine geo-radius predicate — match when within ``radius_m`` meters.

    Match condition is ``distance <= radius_m`` (inclusive boundary —
    a point exactly on the circle counts as inside).

    Returns False when either emission is missing lat or lon (can't
    compare without coords).
    """

    def pred(a: Any, b: Any) -> bool:
        lat_a = _coerce_float(_emission_field(a, lat_field))
        lon_a = _coerce_float(_emission_field(a, lon_field))
        lat_b = _coerce_float(_emission_field(b, lat_field))
        lon_b = _coerce_float(_emission_field(b, lon_field))
        if lat_a is None or lon_a is None or lat_b is None or lon_b is None:
            return False
        distance = _haversine_meters(lat_a, lon_a, lat_b, lon_b)
        return distance <= radius_m

    return pred


def _normalize_place_name(value: Any, normalize: str | None) -> Any:
    """Place-name normalization — extends shared ``_normalize`` with ``lowercase_strip``.

    The shared lib's ``_normalize`` knows ``lowercase`` (lower + strip),
    ``e164``, and ``strip``. The places rule pack also wants
    ``lowercase_strip`` (lower + strip + collapse internal whitespace)
    for canonical name lookups.
    """
    if value is None:
        return None
    if normalize == "lowercase_strip":
        return " ".join(str(value).strip().lower().split())
    return _normalize(value, normalize)


def _named_location_exact_predicate(
    field_name: str,
    normalize: str | None = "lowercase_strip",
) -> Callable[[Any, Any], bool]:
    """Named-location exact-match predicate — same normalized name = same place."""

    def pred(a: Any, b: Any) -> bool:
        va = _normalize_place_name(_emission_field(a, field_name), normalize)
        vb = _normalize_place_name(_emission_field(b, field_name), normalize)
        if va is None or vb is None:
            return False
        if isinstance(va, str) and not va:
            return False
        return va == vb

    return pred


# Module-local predicate-builder lookup. The shared lib ships
# ``geo_radius_meters`` as a placeholder (returns False); this dict
# overrides for the two place-specific shapes without mutating the
# shared ``_PREDICATE_BUILDERS`` dict — that keeps the people facet
# isolated from places-side changes.
_PLACES_PREDICATE_BUILDERS: dict[str, Callable[..., Callable[[Any, Any], bool]]] = {
    "geo_radius_meters": lambda radius_m, lat_field, lon_field, **_: _geo_radius_predicate(  # noqa: E501
        float(radius_m), lat_field=lat_field, lon_field=lon_field,
    ),
    "named_location_exact": lambda fields, normalize, **_: _named_location_exact_predicate(  # noqa: E501
        fields[0] if fields else "name",
        normalize or "lowercase_strip",
    ),
}


def build_places_predicate(rule_dict: dict[str, Any]) -> Callable[[Any, Any], bool]:
    """Build a places-aware predicate — overrides for geo + named-location.

    Falls through to the shared ``build_predicate`` for shapes the
    places module doesn't override (e.g., ``exact_field`` on a
    secondary attribute like ``country_code``).
    """
    shape = rule_dict.get("shape", "exact_field")
    builder = _PLACES_PREDICATE_BUILDERS.get(shape)
    if builder is None:
        return build_predicate(rule_dict)
    fields: list[str] = list(rule_dict.get("fields") or [])
    if "field" in rule_dict and not fields:
        fields = [rule_dict["field"]]
    return builder(
        fields=fields,
        normalize=rule_dict.get("normalize"),
        radius_m=rule_dict.get("radius_m", rule_dict.get("radius_meters", 100.0)),
        lat_field=rule_dict.get("lat_field", "lat"),
        lon_field=rule_dict.get("lon_field", "lon"),
    )


def _places_rule_from_dict(rule_dict: dict[str, Any]) -> CoalescenceRule:
    """Rebuild a CoalescenceRule with the places-aware predicate.

    Mirrors ``_coalescence_lib._rule_from_dict`` but routes predicate
    construction through ``build_places_predicate`` so the placeholder
    geo predicate is replaced by the real haversine impl.
    """
    fields: list[str] = list(rule_dict.get("fields") or [])
    if "field" in rule_dict and not fields:
        fields = [rule_dict["field"]]
    return CoalescenceRule(
        name=rule_dict["name"],
        predicate=build_places_predicate(rule_dict),
        confidence=float(rule_dict.get("confidence", 0.5)),
        notes=str(rule_dict.get("notes", "")),
        shape=str(rule_dict.get("shape", "exact_field")),
        require_manual_review=bool(rule_dict.get("require_manual_review", False)),
        fields=tuple(fields),
    )


def load_places_rules_from_toml(path: Path) -> list[CoalescenceRule]:
    """Parse the ``[[rules.places]]`` block out of a TOML file.

    Unlike the shared ``load_rules_from_toml``, this loader reads the
    raw TOML and reconstructs rules via ``_places_rule_from_dict`` —
    that swaps the shared placeholder for the real haversine /
    named-location predicates.
    """
    import tomllib

    if not path.exists():
        return []
    with open(path, "rb") as f:
        data = tomllib.load(f)
    rules_block = data.get("rules", {})
    place_rules = rules_block.get("places", [])
    return [_places_rule_from_dict(rd) for rd in place_rules]


def load_places_rules_from_dicts(
    rule_dicts: list[dict[str, Any]],
) -> list[CoalescenceRule]:
    """Construct places-aware rules from in-memory dicts (bundled-defaults path)."""
    return [_places_rule_from_dict(rd) for rd in rule_dicts]


# ---------------------------------------------------------------------------
# CoalesceSummary — same shape as people-side for CLI uniformity
# ---------------------------------------------------------------------------


@dataclass
class CoalesceSummary:
    """Structured return value of ``PlacesCoalescer`` end-to-end coalesce."""

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


# ---------------------------------------------------------------------------
# Enrichment — derive convenience fields from raw place payloads
# ---------------------------------------------------------------------------


def _enrich_emission(emission: Any) -> Any:
    """Derive convenience fields from a place-emission payload.

    The rule pack and downstream queries reference a few derived
    fields that source plugins may or may not populate directly. We
    derive them in-place (on a copy) so predicates can read them
    uniformly.

    Derived:
    - ``lat_rounded`` — lat to 4 decimal places (~11m grid). Useful for
      cheap grid-clustering before haversine refinement.
    - ``lon_rounded`` — lon to 4 decimal places.
    - ``country_code`` — pass-through (lowercased) if a ``country`` or
      ``country_code`` field is present.
    - ``normalized_name`` — lowercase + whitespace-collapsed copy of
      ``name``, when ``name`` is present.

    Returns a shallow-cloned ``FacetEmission`` (or dict) with the
    enriched payload; never mutates the input.
    """
    from phdb.core.plugin.bus import FacetEmission

    payload: dict[str, Any]
    if isinstance(emission, dict):
        payload = {**emission}
    else:
        payload = dict(getattr(emission, "payload", None) or {})

    lat = _coerce_float(payload.get("lat"))
    if lat is not None and "lat_rounded" not in payload:
        payload["lat_rounded"] = round(lat, 4)
    lon = _coerce_float(payload.get("lon"))
    if lon is not None and "lon_rounded" not in payload:
        payload["lon_rounded"] = round(lon, 4)

    cc = payload.get("country_code") or payload.get("country")
    if cc:
        # Normalize whether it was passed in or derived from ``country``;
        # downstream rules expect a lowercased 2-letter-ish code.
        payload["country_code"] = str(cc).lower().strip()

    name = payload.get("name")
    if name and "normalized_name" not in payload:
        payload["normalized_name"] = " ".join(str(name).strip().lower().split())

    if isinstance(emission, FacetEmission):
        return FacetEmission(
            source_table=emission.source_table,
            source_id=emission.source_id,
            facet_type=emission.facet_type,
            payload=payload,
        )
    return payload


# ---------------------------------------------------------------------------
# PlacesCoalescer
# ---------------------------------------------------------------------------


@dataclass
class PlacesCoalescer(Coalescer):
    """Coalescer specialized for Place emissions.

    Same proposal-generation API as the base class; the place-specific
    work is in enrichment (deriving rounded coords + normalized name)
    and in the bundled rules pack (real haversine geo predicate +
    named-location resolution).
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
    ) -> tuple[PlacesCoalescer, str]:
        """Construct a coalescer from instance config (or bundled defaults).

        Returns ``(coalescer, rules_source)`` — the source string is
        ``"instance:<path>"`` when an instance file was loaded with at
        least one ``[[rules.places]]`` entry, or ``"bundled-defaults"``
        when falling back.
        """
        rules: list[CoalescenceRule] = []
        source = "bundled-defaults"
        if instance_dir is not None:
            rules_path = instance_dir / "identity_rules.toml"
            if rules_path.exists():
                rules = load_places_rules_from_toml(rules_path)
                if rules:
                    source = f"instance:{rules_path}"
        if not rules:
            rules = load_places_rules_from_dicts(DEFAULT_PLACES_RULES)
        return cls(rules=rules, threshold=threshold), source


# ---------------------------------------------------------------------------
# End-to-end helper — same shape as the people-side counterpart
# ---------------------------------------------------------------------------


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

    ``fk_columns`` lists ``(table, column)`` pairs holding ``places.id``
    FKs that the merge must rewrite. Default: empty list — the Phase
    8 corpus has no formal FK consumers of ``places.id`` yet (the
    Place table is action-shaped per ``schemas/canonical.py``), so the
    apply step's FK loop is a no-op until Phase 7 entity-factors Place.
    """
    coalescer, source = PlacesCoalescer.from_instance(instance_dir)
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
            if proposal.into_node_id >= 0:
                try:
                    apply_merge(
                        conn,
                        node_table="places",
                        proposal=proposal,
                        facet_type="Place",
                        fk_columns=fk_columns,
                    )
                    summary.auto_merged += 1
                    summary.audit_entries_written += 1
                except Exception as exc:  # pragma: no cover - defensive
                    log.warning("auto-merge failed: %s", exc)
                    pending.append(proposal)
                    summary.pending_review += 1
            else:
                # Emission-only equivalence — no existing place to merge
                # into. Surface to review so the operator can pick a
                # survivor (typically by creating a new canonical row).
                pending.append(proposal)
                summary.pending_review += 1
        else:
            pending.append(proposal)
            summary.pending_review += 1

    return summary, pending


__all__ = [
    "AUTO_MERGE_THRESHOLD",
    "CoalesceSummary",
    "DEFAULT_PLACES_RULES",
    "PlacesCoalescer",
    "build_places_predicate",
    "coalesce_buffer_to_db",
    "load_places_rules_from_dicts",
    "load_places_rules_from_toml",
]
