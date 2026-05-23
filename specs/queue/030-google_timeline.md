# Port `google_timeline` adapter to plugin

Port `phdb.adapters.google_timeline` → `phdb.plugins.google_timeline/`.
Source: Google Maps Timeline JSON exports — TravelAction
(movement segments) + Place (visited locations) + GeoShape (place
boundaries).

## Manifest declarations

- `emits = ["TravelAction", "Place", "GeoShape"]`
- `entity_refs = []`
- `formats_used = ["google_timeline_json"]`
- `records_required = ["GeoTrace"]`
- `facets_projected = ["Place", "Time"]`

## Initial scope

- Port the JSON parser + per-segment routing.
- Project visited places to the Place facet (geo coordinates +
  named location).
- Project timestamps to Time.

## Out of scope

- Place-entity factoring (deferred per Phase 7 — Place stays
  action-shaped; entity-factor lands when the Place facet
  coalescence rules engine ships in Phase 8).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_google_timeline_adapter.py` passes verbatim.

## Context

Primary input for the Place facet. Phase 8 will use the Place facet's
coalescence rules (same-coords-within-radius, named-location match)
to merge Google's Place rows with future phone-photos geo metadata.
