# Port `apple_notes_full` adapter to plugin

Port `phdb.adapters.apple_notes_full` → `phdb.plugins.apple_notes_full/`.
Source: Apple Notes ZICCLOUDSYNCINGOBJECT SQLite — full extraction of
note body text (gunzip ZICNOTEDATA.ZDATA via the 2->3->2 proto path
per `feedback_apple_notes_proto_path`).

## Manifest declarations

- `emits = ["DigitalDocument"]`
- `entity_refs = []`
- `formats_used = ["apple_notes_sqlite"]`
- `records_required = ["DigitalDocument"]`
- `facets_projected = ["Time"]`

## Initial scope

- Port the SQLite + proto-gunzip extraction pipeline.
- Insert into the `documents` typed table (file-system-extracted
  DigitalDocument schema, not the messages-decomposition variant).

## Out of scope

- Per-folder Topic projection (deferred — needs more design).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_apple_notes_full_adapter.py` passes verbatim.
- The 2->3->2 proto path stays correct after the port (per memory).

## Context

Sensitive parser — the proto-path is a tested-and-fragile invariant.
Brief flags it explicitly so the Gemini port doesn't naively try a
"cleaner" 2->2->1 path.
