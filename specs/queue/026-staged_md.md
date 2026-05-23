# Port `staged_md` adapter to plugin

Port `phdb.adapters.staged_md` → `phdb.plugins.staged_md/`. Source:
staged-markdown directories — generic frontmatter+body files routed by
their `note_type` frontmatter to different typed tables.

## Manifest declarations

- `emits = ["DigitalDocument", "CreativeWork", "Thing", "JoinAction"]`
- `entity_refs = []`
- `formats_used = ["staged_md"]`
- `records_required = ["DigitalDocument"]`
- `facets_projected = ["Time", "Topic"]`

## Initial scope

- Port the generic staging-md scanner + per-note_type routing.
- Verify the per-note_type list of emitted schemas matches the live
  DB before locking the manifest (the four-schema emit declared above
  is the inferred minimum; expand if the live test fixture shows more).

## Out of scope

- New routing logic — keep the existing dispatch table verbatim.

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_staged_md_adapter.py` passes verbatim.

## Context

staged_md is the generic substrate ingester for any frontmatter+body
markdown cluster. Reusable pattern; the inverse of "many sources, one
schema" is "one source, many schemas via frontmatter routing" — this
is its canonical example.
