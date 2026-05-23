# Port `apple_health` adapter to plugin

Port `phdb.adapters.apple_health` → `phdb.plugins.apple_health/`.
Source: Apple Health XML export (`export.xml`). Bulk volume — 5.93M
Observation rows in the live DB. Heaviest single-source emit count.

## Manifest declarations

- `emits = ["Observation", "ExerciseAction", "MedicalRecord"]`
- `entity_refs = []`
- `formats_used = ["apple_health_xml"]`
- `records_required = ["HealthObservation", "ExerciseSet"]`
- `facets_projected = ["Time"]`

## Initial scope

- Port the streaming XML parser + per-record-type routing.
- Preserve the existing record_metadata + hr_samples sidecar tables.
- Project per-day buckets to the Time facet (don't emit per-row —
  5.93M emissions would saturate the bus; aggregate first).

## Out of scope

- Per-Observation Person facet (Observations are self-only — Rob's
  body; no other-Person attribution).
- Sleep stage analysis beyond what the current parser extracts.

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_apple_health_adapter.py` passes verbatim.
- Bulk insert performance: full 5.93M-row reingest completes in
  comparable wall time to the legacy adapter (no order-of-magnitude
  regression).

## Context

Bulk-volume canary. If the plugin contract introduces per-row
overhead that hurts apple_health's ingest time, the manifest's
`embeddable_tables` declaration + the post-ingest hook need to be
checked for unintended per-row work. This is the source with the
widest sidecar usage (hr_samples in particular).
