# Port `google_fit` adapter to plugin

Port `phdb.adapters.google_fit` → `phdb.plugins.google_fit/`. Source:
Google Fit Takeout JSON exports — per-activity-type aggregated stats.

## Manifest declarations

- `emits = ["Observation", "ExerciseAction"]`
- `entity_refs = []`
- `formats_used = ["google_fit_json"]`
- `records_required = ["HealthObservation"]`
- `facets_projected = ["Time"]`

## Initial scope

- Port the JSON parser + per-activity routing.
- Reuse the apple_health typed tables — same schemas (Observation +
  ExerciseAction) cover both sources cleanly.

## Out of scope

- Cross-source dedup with apple_health (different time grain; manual
  reconciliation if needed).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_google_fit_adapter.py` passes verbatim.

## Context

Validates that multiple source plugins (apple_health, apple_health_backup,
google_fit) can emit the same @type into the same typed table — the
schema is owned by phdb.schemas; the plugins are bridges.
