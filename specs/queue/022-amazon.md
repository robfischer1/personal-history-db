# Port `amazon` adapter to plugin

Port `phdb.adapters.amazon` → `phdb.plugins.amazon/`. Source: Amazon
"Request my data" ZIP — orders, product views, watches, reviews.

## Manifest declarations

- `emits = ["Product", "OrderAction", "Review", "WatchAction"]`
- `entity_refs = []`
- `formats_used = ["amazon_zip"]`
- `records_required = ["Transaction", "ConsumedItem"]`
- `facets_projected = ["Time"]`

## Initial scope

- Port the ZIP scanner + per-file routing (orders CSV → OrderAction;
  product-views CSV → Product; reviews CSV → Review; Prime Video
  history → WatchAction).
- Preserve the existing schema_type-per-section logic.

## Out of scope

- Product-entity factoring (deferred — products stay action-shaped
  in Phase 7).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_amazon_adapter.py` passes verbatim.

## Context

Four-@type fan-out — second-largest after facebook_unified. Validates
that a single adapter ZIP can route to multiple typed tables.
