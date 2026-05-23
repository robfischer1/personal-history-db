# WPEF follow-on — ReadAction action schema + Pocket/Instapaper plugin (stub)

Introduce a `ReadAction` action schema for reading-list entries (Pocket,
Instapaper, future read-it-later sources) that FK into `web_pages` —
mirrors the BrowseAction pattern from `001-apple_dbs`.

This brief is a **placeholder** — there's no Pocket or Instapaper
adapter yet. Ship the schema + the plugin scaffold so when an export
file becomes available, the plugin already exists and just needs its
parser wired.

## Scope

- Author a `ReadAction` schema in `phdb.schemas.canonical`:
  - schema_type = "ReadAction"
  - table_name = "read_actions"
  - emits-list extension to the schemas registry
  - fields: id, schema_type, web_page_id (FK), date_read (TEXT),
    direction, body_text, body_text_source, source_file_id, created_at
  - Standard dedup index on `(source_file_id, raw_hash)`
- Author a stub `src/phdb/plugins/readaction/` with manifest declaring
  `emits = ["ReadAction"]`, `entity_refs = ["web_pages"]`. The plugin
  `parse()` method raises `NotImplementedError("no Pocket/Instapaper
  format parser yet")` — clear signal that the schema is ready and
  the plugin is the seam waiting for a parser.
- Author a test that verifies the schema applies cleanly + the stub
  plugin discovers.

## Out of scope

- The actual Pocket / Instapaper format parser (lands when Rob exports
  data from either service).

## Success criteria

- `ReadAction` schema present + DDL applies cleanly.
- Plugin discovers + describes with zero manifest validation issues.
- Stub plugin tests cover the discovery + manifest-shape invariants.

## Context

Closes the WPEF inherited-deliverable list. The full set after these
three briefs:
1. BrowseAction action schema ✓ (`001-apple_dbs`)
2. SearchAction → WebPage FK retrofit ✓ (`002-google_activity`)
3. ReadAction action schema (this brief)
4. Triple emission for bookmarks ↔ web_pages (`100-...-triples`)
5. Column cleanup migration (`101-...-column-cleanup`)
