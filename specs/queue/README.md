# Phase 7 brief queue — phdb Plugin Architecture

This directory holds the spec-kit briefs for porting the remaining
~30 legacy adapters in `src/phdb/adapters/` to the new plugin
contract under `src/phdb/plugins/<name>/`. Each `.md` here is one
self-contained brief intended for `/speckit-dispatch` → Gemini.

The plan: [[Outputs/Plans/phdb Plugin Architecture]] (status: active,
phase: 6). Phase 5 already shipped the raindrop pilot — that's the
canonical exemplar every brief in this queue mirrors.

## Pre-flight reading every brief assumes

Every dispatched Gemini session should load these once before touching
its assigned adapter:

- `docs/plugins.md` — the PhdbPlugin contract spec.
- `src/phdb/plugins/raindrop/` — worked example (manifest +
  plugin.py + ingest.py + tests/).
- `src/phdb/core/plugin/` — PluginManifest dataclass, ABCs, loader.
- `src/phdb/schemas/canonical.py` — the 33 typed schemas registry
  any plugin's `emits = [...]` must resolve against.
- `src/phdb/formats/url.py` — the `phdb.formats/` shared-primitives
  precedent; manifests declare `formats_used = [...]` to make
  dependencies explicit.
- `Outputs/Plans/phdb Plugin Architecture DECISIONS.md` (vault) —
  Phase 0 outcomes; especially Q4 (ABC enforcement), Q5 (schemas-
  decoupled-from-plugins), Q14 (hard break, no shim), Q16 (7-point
  pilot success criteria).

## Brief shape — every brief in this queue conforms

```markdown
# Port `<adapter>` adapter to plugin

One opening paragraph naming the source, file format, typical input
size, and what makes this adapter distinctive.

## Manifest declarations

- `emits = ["..."]`              — list of Schema.org @type strings
- `entity_refs = ["..."]`        — entity tables this plugin FKs to
- `formats_used = ["..."]`       — phdb.formats modules consumed
- `records_required = ["..."]`   — phdb.records types yielded
- `facets_projected = ["..."]`   — facet types this plugin emits into

## Initial scope

- Bulleted scope. Mention any sidecar tables (Q5b default).

## Out of scope

- Things deferred to follow-on briefs; rare edge cases.

## Success criteria

- Plugin discovers via in-tree loader; `phdb plugin describe <name>`
  shows the manifest with zero validation issues.
- Every assertion in the existing `tests/test_<name>_adapter.py` ports
  verbatim and passes against the new plugin (byte-clean golden-diff).
- Legacy `src/phdb/adapters/<name>.py` deleted; any other adapter or
  test importing from it updated to the new home (Q14 hard break).
- Entity-FK pattern (if applicable): zero orphaned `<entity>_id`
  values; junk/excluded action rows still create entity rows.

## Context

Any WPEF lineage notes; cross-adapter dependencies; sidecar specifics;
why a Person/Place/Time facet projection makes sense for this source.
```

## Dispatch ordering

Briefs are numbered for sort but spec-kit-dispatch can run them in any
order. Suggested ordering optimizes for dependency-readiness:

| # | Brief | Rationale |
| :-: | :--- | :--- |
| 001 | `apple_dbs` | Establishes the **BrowseAction** entity-FK pattern (Safari history → web_pages FK), already mid-port from WPEF |
| 002 | `google_activity` | Establishes the **SearchAction → WebPage FK retrofit** pattern |
| 003 | `mbox` | Largest emit volume (EmailMessage); validates the messages-decomposition shape under load |
| 004 | `imessage` | First chat schema (Message) with attachments + recipients sidecar |
| 005 | `discord` | Cross-source Message + Thread facet projection — exercises the facet bus end-to-end |
| 006 | `google_voice` | Calls (Action) + SMS (Message) hybrid |
| 007 | `phone_sms` | Mirror of imessage for non-Apple |
| 008 | `sms_xml` | Cross-validates against phone_sms |
| 009 | `phone_calls_xml` | Action schema for phone calls |
| 010 | `chat_logs` | Older Message format |
| 011 | `claude_chat` | Conversation schema (AI sessions) |
| 012 | `claude_code` | Conversation schema (Claude Code transcripts) |
| 013 | `facebook_unified` | Heaviest emit fan-out (Message, SocialMediaPosting, Comment, LikeAction, Event, JoinAction) |
| 014 | `facebook_connections` | Person identity coalescence source |
| 015 | `google_contacts` | Person entity authority (when entity-factored in this pass or deferred) |
| 016 | `apple_health` | Bulk Observation + ExerciseAction + MedicalRecord (5.9M rows in live DB) |
| 017 | `apple_health_backup` | Incremental sibling of apple_health |
| 018 | `google_fit` | Observation + ExerciseAction (Google-side) |
| 019 | `strong` | Pure ExerciseAction (workout app) |
| 020 | `spotify` | ListenAction |
| 021 | `goodreads` | Book + Review (book-entity factoring deferred) |
| 022 | `amazon` | Product + OrderAction + Review + WatchAction |
| 023 | `apple_notes_full` | DigitalDocument |
| 024 | `articles` | Article (file-based) |
| 025 | `clippings` | Quotation (file-based) |
| 026 | `staged_md` | Staged-md DigitalDocument + multi-@type emission |
| 027 | `google_drive` | DigitalDocument from Drive export |
| 028 | `onedrive` | DigitalDocument from OneDrive local |
| 029 | `calendar` | Event + InviteAction |
| 030 | `google_timeline` | TravelAction + Place + GeoShape |
| 031 | `phone_photos` | Photograph (mirrors digikam adapter precedent) |
| 100 | `wpef-followon-bookmark-triples` | WPEF inherited deliverable — emit `taggedWith` / `inFolder` / `mentions` / `relatesTo` triples for bookmarks |
| 101 | `wpef-followon-bookmark-column-cleanup` | WPEF inherited deliverable — destructive migration dropping deprecated bookmark cols |
| 102 | `future-readaction-pocket-instapaper` | WPEF inherited deliverable — `ReadAction` schema + stub plugin (lands when a Pocket / Instapaper exporter exists) |

## Per-batch dispatch

Per Phase 0 Q17: dispatch 2 briefs in parallel (Google AI Pro
concurrency cap). Claude reviews each PR before merge; max 2 revision
rounds per `claude-plan --type gemini` §2.6.

After each merge: full test suite (`uv run pytest`) must stay green.

## Closing the loop

When every brief in this queue is merged: the legacy
`src/phdb/adapters/` directory should be empty (or contain only
`base.py` until the last brief retires it). Phase 7 is complete when
`grep -l 'phdb.adapters' src/` returns no results.
