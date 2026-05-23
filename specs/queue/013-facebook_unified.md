# Port `facebook_unified` adapter to plugin

Port `phdb.adapters.facebook_unified` → `phdb.plugins.facebook_unified/`.
Source: Facebook data export HTML (the unified all-data export).
Heaviest emit fan-out of any adapter — produces six different action
schemas from one source.

## Manifest declarations

- `emits = ["Message", "SocialMediaPosting", "Comment", "LikeAction", "Event", "JoinAction"]`
- `entity_refs = []`
- `formats_used = ["facebook_html"]`
- `records_required = ["ChatMessage", "SocialPost", "Reaction"]`
- `facets_projected = ["Person", "Time", "Thread"]`

## Initial scope

- Port the HTML scanner + per-section routing (messages section →
  Message; posts section → SocialMediaPosting; etc.).
- Preserve the existing schema_type-per-section logic verbatim.
- Friends + tagged users in posts project to the Person facet; one
  Thread per message conversation; date_sent projects to Time.

## Out of scope

- Splitting this adapter into per-section plugins (deferred — keep
  the unified shape that already works).
- facebook_residuals coverage (separate brief if a residuals adapter
  still exists; verify against the live tests/test_facebook_residuals_adapter.py).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_facebook_adapter.py` + `tests/test_facebook_posts_adapter.py`
  + `tests/test_facebook_residuals_adapter.py` all pass verbatim.
- Per-section row counts match pre-port baselines exactly.

## Context

facebook_unified is the most-shaped multi-@type emit case. Its manifest
demonstrates the `emits = [...]` array of mixed types resolving cleanly
against the schemas registry. This brief is the canary for "one source
plugin, many typed tables" routing logic.
