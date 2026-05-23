# Port `apple_health_backup` adapter to plugin

Port `phdb.adapters.apple_health_backup` → `phdb.plugins.apple_health_backup/`.
Source: iMazing-style iPhone backup containing the live healthdb
SQLite. Incremental sibling of `016-apple_health` — the XML export
is point-in-time; the backup adapter ingests deltas from successive
backups.

## Manifest declarations

- `emits = ["Observation", "ExerciseAction", "MedicalRecord"]`
- `entity_refs = []`
- `formats_used = ["apple_health_backup"]`
- `records_required = ["HealthObservation", "ExerciseSet"]`
- `facets_projected = ["Time"]`

## Initial scope

- Port the SQLite parser + delta-detection logic from the legacy
  adapter.
- Reuse the same typed-table writes as apple_health (no new schemas).
- Dedup against existing rows via the standard
  `(source_file_id, raw_hash)` index.

## Out of scope

- Replacing apple_health (both adapters coexist — XML is bulk-import,
  backup is incremental).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_apple_health_adapter.py` apple-backup tests pass.
- Incremental rerun against the same backup produces zero new rows.

## Context

Pair with `016-apple_health`. The two plugins write to the same typed
tables; phdb.schemas keeps them aligned. Once both are ported, the
plan to consolidate (or keep separate) can be evaluated post-port.
