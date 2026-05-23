# Port `spotify` adapter to plugin

Port `phdb.adapters.spotify` → `phdb.plugins.spotify/`. Source:
Spotify Extended Streaming History JSON files from Spotify privacy
export. 44K rows in the live DB.

## Manifest declarations

- `emits = ["ListenAction"]`
- `entity_refs = []`
- `formats_used = ["spotify_json"]`
- `records_required = ["MediaPlay"]`
- `facets_projected = ["Time"]`

## Initial scope

- Port the JSON streaming parser + per-stream insert.
- Project listen timestamps to the Time facet.

## Out of scope

- Track-as-entity factoring (deferred — listen rows reference track
  metadata inline; future plugin port could split into Track entities).
- Cross-source media coalescence (Spotify + Apple Music when that
  source lands).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_spotify_adapter.py` passes verbatim.

## Context

ListenAction is the second-most-active typed table (44K rows after
exercise_actions' 45K). Streaming-history parsers must be tolerant
of the per-format variation across Spotify export versions.
