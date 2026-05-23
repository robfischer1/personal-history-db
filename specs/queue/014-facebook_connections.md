# Port `facebook_connections` adapter to plugin

Port `phdb.adapters.facebook_connections` → `phdb.plugins.facebook_connections/`.
Source: Facebook export HTML — friends list with timestamps. Writes to
the `connections` typed table (separate from `persons`).

## Manifest declarations

- `emits = ["Connection"]`
- `entity_refs = []`
- `formats_used = ["facebook_connections_html"]`
- `records_required = ["Connection"]`
- `facets_projected = ["Person", "Time"]`

## Initial scope

- Port HTML parsing + per-row insert into `connections`.
- Each friend name + timestamp projects to the Person facet (with
  high confidence for identity coalescence in Phase 8).
- friended-at timestamp projects to Time.

## Out of scope

- Cross-referencing connections against post-tags or chat senders
  (Phase 8 cross-source coalescence).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_facebook_connections_adapter.py` passes verbatim.

## Context

connections is a domain-specific table for the friend-graph
substrate. The Person facet emissions from this plugin are the
single richest input for identity coalescence — Facebook friends
have full names + timestamps + photo-tag co-occurrence.
