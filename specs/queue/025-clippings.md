# Port `clippings` adapter to plugin

Port `phdb.adapters.clippings` → `phdb.plugins.clippings/`. Source:
Vault Resources/Clippings/ + Resources/Reddit Posts/ markdown notes.
Writes to the `clippings` typed table (Quotation + Comment under one
table, schema_type='Quotation' default).

## Manifest declarations

- `emits = ["Quotation"]`
- `entity_refs = []`
- `formats_used = ["clippings_md"]`
- `records_required = ["Provenance"]`
- `facets_projected = ["Time", "Topic"]`

## Initial scope

- Port markdown frontmatter + body extraction.
- Source-URL projection to the WebPage entity (optional — same
  consideration as articles).

## Out of scope

- Differentiating Reddit Posts from Clippings — they share a table
  per migration 0017 design.

## Success criteria

- Plugin discovers + describes cleanly.
- Existing test coverage passes verbatim.

## Context

Sibling to `024-articles`. Shares the file-extracted column shape.
