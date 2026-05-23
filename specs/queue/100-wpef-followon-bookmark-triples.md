# WPEF follow-on — emit bookmark↔webpage triples at ingest

Wire write-time triple emission for the bookmarks↔web_pages relationship
per the WPEF inherited deliverable. Adds four predicates'-worth of
edges from the existing bookmark + web_page rows so the triple store
becomes the cross-source graph view for URL identity.

This brief assumes the raindrop pilot has shipped (Phase 5 ✓) and
apple_dbs has been ported (`001-apple_dbs`). Both plugins consume
this brief's helpers.

## Scope

Author a helper module — recommend `phdb.plugins.raindrop.triples` or
`phdb.formats.bookmark_triples` — that emits the following triples per
bookmark row at ingest time:

| Predicate | Subject | Object | Source |
| :--- | :--- | :--- | :--- |
| `taggedWith` | `bookmark` node | tag-name (one triple per tag) | bookmark.tags JSON |
| `inFolder` | `bookmark` node | folder-name | bookmark.folder |
| `mentions` | `bookmark.web_page` node | concept extracted from title/note | bookmark.title + bookmark.note |
| `relatesTo` | `bookmark` node | `bookmark.web_page` node | bookmark.web_page_id |

Each plugin (`raindrop` + `apple_dbs`) calls the helper from its
`ingest_row` after the bookmark + web_page rows land. Emission uses
the `phdb.core.graph.GraphService` API; provenance = `ai-emitted` or
a plugin-specific tag (`raindrop-emitted`).

Predicates are seeded as part of this brief — if `taggedWith`,
`inFolder`, `mentions`, `relatesTo` aren't already in the `predicates`
table from earlier migrations, ship a small migration adding them.

## Out of scope

- Mention-concept extraction beyond a basic noun-phrase splitter
  (this is graph-substrate, not NLP — keep it simple).
- Back-fill of existing 15,757 bookmark rows (covered by a separate
  one-shot script the plugin authors can run after the helper exists).

## Success criteria

- After running both raindrop + apple_dbs on the fixtures, the
  `triples` table contains the expected count of `taggedWith` /
  `inFolder` / `mentions` / `relatesTo` rows for the fixture bookmarks.
- A new test (`tests/test_bookmark_triple_emission.py`) covers all
  four predicates against the raindrop fixture.

## Context

This is one of five WPEF inherited deliverables from the Lineage
section of `Outputs/Plans/phdb Plugin Architecture DECISIONS.md`.
Pair with `101-wpef-followon-bookmark-column-cleanup.md` (destructive
migration) which lands after this brief ships and the triples are
populated.
