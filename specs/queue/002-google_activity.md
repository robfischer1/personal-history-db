# Port `google_activity` adapter to plugin

Port `phdb.adapters.google_activity` → `phdb.plugins.google_activity/`.
Source: Google Takeout MyActivity HTML exports — search queries, ad
interactions, video watches, app launches. Typical input:
`Takeout/My Activity/<service>/MyActivity.html` files.

## Manifest declarations

- `emits = ["SearchAction", "WatchAction"]`
- `entity_refs = ["web_pages"]`
- `formats_used = ["url", "google_activity_html"]`
- `records_required = ["WebActivity"]`
- `facets_projected = ["Time", "Topic"]`

## Initial scope

- Port the HTML scraper to the new plugin home.
- **SearchAction → WebPage FK retrofit (WPEF inherited deliverable).**
  Every SearchAction row whose URL clicks through to a real page gets
  a `web_page_id` FK populated via `upsert_web_page`. Add a `web_page_id`
  INTEGER column to the `search_actions` typed schema in
  `phdb.schemas.canonical`; backfill from existing rows via a one-shot
  migration (`phdb plugin migrate google_activity --backfill-web-pages`
  or similar). Junk searches (no result click) leave `web_page_id` NULL.
- Wire `WatchAction` rows the same way for video clicks that land on
  real URLs.

## Out of scope

- Ad-interaction enrichment (deferred — Takeout HTML is sparse).
- Cross-correlation with Spotify or YouTube history (separate facet).

## Success criteria

- Plugin discovers cleanly; manifest validates.
- `tests/test_google_activity_adapter.py` assertions pass.
- After the backfill, every clicked SearchAction has a valid
  `web_page_id`; uncliked searches have NULL (not orphan).
- Reruns are idempotent — appearance counts don't double-count
  searches.

## Context

This brief absorbs the **SearchAction → WebPage FK retrofit**
inherited deliverable from the WPEF refactor (per the Lineage section
of the DECISIONS doc). Pair with `001-apple_dbs.md` (BrowseAction):
together these are the first two entity-FK action schemas after the
raindrop BookmarkAction precedent.
