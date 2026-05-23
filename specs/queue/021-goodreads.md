# Port `goodreads` adapter to plugin

Port `phdb.adapters.goodreads` → `phdb.plugins.goodreads/`. Source:
Goodreads CSV export — books read + reviews + ratings.

## Manifest declarations

- `emits = ["Book", "Review"]`
- `entity_refs = []`
- `formats_used = ["goodreads_csv"]`
- `records_required = ["ConsumedItem"]`
- `facets_projected = ["Time"]`

## Initial scope

- Port the CSV parser + per-row routing (book entry → Book; review text
  → Review).
- Preserve the existing book/review separation logic.

## Out of scope

- Book-entity factoring (deferred — books stay action-shaped in Phase 7;
  entity-factor pass lands when a second source emits Books).
- Cross-referencing book metadata with mentions in articles / clippings.

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_goodreads_adapter.py` passes verbatim.

## Context

Currently the only source emitting `Book` and `Review` schemas. Phase 7
keeps these action-shaped per the canonical schemas registry; entity-
factor lands in a future plan when (a) another book source ships, or
(b) consumed-media cross-correlation becomes a need.
