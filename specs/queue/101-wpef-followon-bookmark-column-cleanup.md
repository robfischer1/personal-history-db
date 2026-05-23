# WPEF follow-on — drop deprecated bookmark columns

Destructive migration: drop the columns from `bookmarks` that are now
fully redundant against the `web_pages` entity table. Replace the
unique constraint key. SQLite table-rebuild on 15,757 rows.

This brief is gated by `100-wpef-followon-bookmark-triples.md` — the
triple emission for bookmarks must be live + populated before the
deprecated columns drop, since the triples replace the duplicated
WebPage data on bookmark rows.

## Scope

Author a numbered migration (next free ID — currently `0024_`):

- Drop columns from `bookmarks`: `url`, `normalized_url`, `title`,
  `excerpt`, `cover_url`. These all live on the parent `web_pages`
  entity now (joinable via `web_page_id`).
- Drop the unique index `idx_bookmarks_url_instrument` keyed on
  `(normalized_url, instrument)`.
- Create a replacement unique index keyed on
  `(web_page_id, instrument)`.
- SQLite doesn't support DROP COLUMN cleanly for indexed columns —
  use the rebuild pattern: create `bookmarks_new`, INSERT...SELECT
  from `bookmarks`, drop `bookmarks`, RENAME.

## Out of scope

- Touching the raindrop_id / favorite / tags / note columns — those
  are bookmark-action-specific, not WebPage-entity.
- Migrating the apple_dbs Safari bookmarks (already use
  `upsert_bookmark` post-WPEF; cleanup applies uniformly).

## Success criteria

- Migration applies cleanly against the live DB (15,757 rows).
- `tests/test_migrations.py` exercises the new migration end-to-end.
- Plugin tests (raindrop, apple_dbs) continue to pass — readers + writers
  go through `web_page_id` JOIN to retrieve URL/title/etc., never the
  bookmarks row directly.
- Plugin `ingest_row` no longer passes `url`/`normalized_url`/`title`/
  `excerpt`/`cover_url` to bookmarks (only to web_pages via upsert).

## Context

Final WPEF cleanup. After this migration ships, the bookmarks table
holds ONLY action-specific columns; URL identity lives ONLY in
web_pages. This is the load-bearing demonstration that entity-FK
factoring eliminates duplication.

Run BEFORE `102-future-readaction-pocket-instapaper.md` to keep the
bookmarks-schema cleanup atomic.
