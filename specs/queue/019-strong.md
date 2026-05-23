# Port `strong` adapter to plugin

Port `phdb.adapters.strong` → `phdb.plugins.strong/`. Source: Strong
workout-tracking app SQLite export. Per-routine + per-instance
exercise sets.

## Manifest declarations

- `emits = ["ExerciseAction"]`
- `entity_refs = []`
- `formats_used = ["strong_sqlite"]`
- `records_required = ["ExerciseSet"]`
- `facets_projected = ["Time"]`

## Initial scope

- Port the SQLite parser + per-set insert into `exercise_actions`.
- Preserve the existing per-routine vs per-instance ID resolution
  logic (ZUNIQUEID vs Z_PK per `feedback_strong_zuniqueid_vs_zpk`).

## Out of scope

- Routine-template entity-factoring (deferred — keep current shape).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_strong_adapter.py` passes verbatim.

## Context

Pure ExerciseAction emitter — small surface, fast port. Useful early
brief to validate that the bare-minimum source plugin (one schema, no
sidecars, no entity FK) ports cleanly.
