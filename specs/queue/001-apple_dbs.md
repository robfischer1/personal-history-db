# Port `apple_dbs` adapter to plugin

Port `phdb.adapters.apple_dbs` → `phdb.plugins.apple_dbs/`. Source: Apple
SQLite databases extracted from iPhone backups — Safari history, iMessage
conversations, app metadata. Typical input: a Titanium-Backup-style
extract directory containing `History.db`, `chat.db`, etc.

The Safari history handlers were already rewritten during the WebPage
Entity Factoring refactor (2026-05-22) to use `upsert_web_page` +
`upsert_bookmark` from `phdb.plugins.raindrop.ingest`. **That cross-
plugin import is the load-bearing fragility this brief must remove.**

## Manifest declarations

- `emits = ["WebPage", "BookmarkAction", "BrowseAction", "Message"]`
- `entity_refs = ["web_pages"]`
- `formats_used = ["url", "apple_dbs_sqlite"]`
- `records_required = ["WebActivity", "ChatMessage"]`
- `facets_projected = ["Person", "Time", "Thread"]`

## Initial scope

- Move all Safari history handling from `phdb.adapters.apple_dbs` into
  the new plugin.
- **Introduce the `BrowseAction` action schema** in `phdb.schemas.canonical`
  (one row per visit; FK to `web_pages`; columns: `id`, `schema_type`,
  `web_page_id`, `visit_time`, `source_device`, `source_file_id`,
  `created_at`). Wire it into the schemas registry.
- Replace the cross-plugin import (`from phdb.plugins.raindrop.ingest
  import upsert_web_page, upsert_bookmark`) with either (a) a local
  re-implementation, (b) extracting both helpers to a shared
  `phdb.formats.bookmark_upserts` module that both plugins import.
  Recommend (b) — that's the right long-term home for the bespoke
  COALESCE temporal-merge SQL.
- Project iMessage rows to the Thread facet (one thread per
  conversation thread); Person facet (one person per sender_address).

## Out of scope

- Chrome history (separate future plugin per the WPEF inherited
  deliverables; lands when a Chrome history adapter exists).
- Apple Notes (covered by the `apple_notes_full` brief).

## Success criteria

- Plugin discovers via in-tree loader; `phdb plugin describe apple_dbs`
  shows the manifest with zero validation issues.
- `tests/test_apple_dbs_adapter.py` assertions port verbatim and pass.
- Legacy `src/phdb/adapters/apple_dbs.py` deleted.
- Every Safari history row produces a `web_pages` row + a `BrowseAction`
  row; zero orphans (`web_page_id IS NULL` count == 0).
- iMessage rows land in `chat_messages` (existing typed table); no
  duplicates after a rerun.

## Context

This brief absorbs the **BrowseAction action schema** inherited
deliverable from the WPEF refactor (per the Lineage section of
`Outputs/Plans/phdb Plugin Architecture DECISIONS.md`). Together with
raindrop (Phase 5), apple_dbs becomes the second consumer of the
web_pages entity table — exercising cross-plugin entity FK in
production.
