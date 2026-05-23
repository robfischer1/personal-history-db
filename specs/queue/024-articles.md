# Port `articles` adapter to plugin

Port `phdb.adapters.articles` → `phdb.plugins.articles/`. Source: Vault
Resources/Articles/ markdown notes (article saves from the web). Writes
to the `articles` typed table (Article @type, file-shaped).

## Manifest declarations

- `emits = ["Article"]`
- `entity_refs = []`
- `formats_used = ["articles_md"]`
- `records_required = ["Provenance"]`
- `facets_projected = ["Time", "Topic"]`

## Initial scope

- Port the markdown frontmatter + body extraction.
- Article URLs project to the WebPage entity (similar to bookmarks —
  consider adding `entity_refs = ["web_pages"]` and FK if the
  dissolution pilot needs it).
- categories + tags project to the Topic facet.

## Out of scope

- The Articles Dissolution Pilot itself (separate concern — pilot
  closed 2026-05-19 per `project_articles_dissolution_pilot`).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_articles_adapter.py` (if exists; otherwise the relevant
  parts of integration tests) passes verbatim.

## Context

articles + clippings + apple_notes_full are the three vault-sourced
DigitalDocument-shape plugins. They share file-extracted-row semantics
(file_path, ctime, mtime, bucket).
