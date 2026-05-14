# Schema Reshape — Typed Tables (DigitalDocument Phase)

> **Companion:** Brain Soup draft at `Brain Soup/Schema Reshape - Typed Tables.md` (informal shape, 20-row walk-table). This file is the canonical execution plan.

## Context

The `messages` table started as Gmail-shaped and worked through Bundles 1-3 when the corpus was mostly email. By Bundles 4+ it became a kitchen sink for ~28 source kinds with polymorphism-via-NULL: Spotify ListenAction, Goodreads Book, Apple Health Observation (~6M rows), Calendar Event, Facebook SocialMediaPosting, etc. Columns `sender_address` / `sender_name` / `sender_domain` / `direction` are first-class on the table but vestigial for everything that isn't message-shaped. Migrations 0003/0004/0005 already broke the unified pattern (sidecars, bookmarks, connections) — this formalizes the direction.

Rob coined the term **deferral debt** for this pattern: design-debt that compounds via momentum-preservation rather than known suboptimality. The boundary case became the rule across migrations 003/004/005.

OneDrive ingestion (~2-4 GB of body text) is **gated** on this reshape. Pre-reshape ingest would land DigitalDocument rows in `messages` and compound the debt.

## Goal

When this plan is complete:

- Current `documents` table (embedding chunk registry) renamed to `chunks` everywhere — table, indexes, triggers, FTS5, vec0 references, Python code.
- New `documents` table exists with DigitalDocument-appropriate columns (file_path, file_size, mtime, ctime, bucket — no vestigial sender_*/direction).
- All 141 existing DigitalDocument rows migrated from `messages` to `documents` with bucket data recovered via COALESCE.
- `chunks.source_table` updated for migrated rows (`'messages'` → `'documents'`).
- 5 adapters retargeted to insert into `documents` instead of `messages`.
- `query.py` routes hydration by `source_table` (UNION pattern, not JOIN).
- `embed_pipeline.py` chunks from both `messages` and `documents` into `chunks`.
- OneDrive adapter patched with post-reorg config + Reference body-allowlist.
- Full test suite passes.

## Non-goals

- **Observation/Health carve-out** (~6M rows) — biggest blast radius; separate plan (`Schema Reshape - Observation Tables`).
- **Events/listens/reads/posts carve-out** (Calendar, Spotify, Goodreads, Facebook posts) — lower urgency; batch in a follow-up.
- **Dropping dead columns from `messages`** (Migration 0010+) — wait until 70%+ of rows have migrated.
- **AdapterRow discriminated union** — cleaner but doubles adapter test surface for 38 adapters. Keep the optional-fields dataclass.
- **Thread machinery for documents** — files don't have multi-party threading. Flat `bucket` column is sufficient.

## Phase 0 — Spec & Decisions

Rows 1-3 were locked in the 2026-05-14 Cowork session. Remaining rows are defaults — override any of them.

| # | Question | Default | Reason to revisit |
| :-: | :--- | :--- | :--- |
| 1 | **[LOCKED]** Scope — which schema_types move? | DigitalDocument only. Observation/listens/reads/posts deferred. | Never — scope is the plan's identity. |
| 2 | **[LOCKED]** Naming — what happens to the current `documents` table? | Rename to `chunks`. New typed table claims `documents`. | Never — naming locked in Cowork 2026-05-14. |
| 3 | **[LOCKED]** Column shape for new `documents` table | Drop sender_address/sender_domain/direction (derivable or zero-signal per DB probe). Rename date_sent → mtime. Add file_path/file_size/ctime/bucket. sender_name data (smuggles bucket in google_drive) lifted to first-class `bucket` via COALESCE during 0009. | If a DigitalDocument adapter genuinely needs sender_* semantics (none do today). |
| 4 | Migration ordering — one big or three? | Three: 0007 rename, 0008 create, 0009 move rows. Each small and independently verifiable. | If the three-step sequence causes FK/trigger ordering headaches at apply time (unlikely). |
| 5 | Soft vs hard delete of migrated rows from `messages`? | Hard DELETE after parity verification within the same migration transaction. Pre-migration backup on <host> is the safety net. | If parity verification within 0009 isn't trustworthy enough — could add a transient archive table. |
| 6 | Which adapters retarget to `documents`? | All current DigitalDocument emitters: `google_drive`, `onedrive`, `apple_notes_full`, `staged_md`. (mbox attachments checked — they emit attachment rows, not DigitalDocument messages, so no retarget needed.) | If a new DigitalDocument adapter is added before this plan completes. |
| 7 | Idempotency constraint on new `documents` table | `UNIQUE(source_file_id, raw_hash)` — mirrors `messages` partial index. | If DigitalDocument dedup needs a different key (e.g., file_path-based). |
| 8 | FTS5 + vec0 handling during 0007 rename | Drop and recreate `doc_fts` with `content='chunks'`. Repopulate from `chunks`. Rename triggers `documents_ai/ad/au` → `chunks_ai/ad/au`. `doc_vectors` (vec0) is rowid-based — no table-name reference, works as-is. | If FTS repopulation is too slow on 220K+ chunks (test on <host> first; expect ~30s). |
| 9 | `embed_pipeline.py` changes | Update all `documents` refs to `chunks` (the chunk registry). Add source-table parameterization: iterate `messages` then `documents` (the new typed table) for chunking eligibility. No file rename — name is already correct. | If a third source table (e.g., future `observations`) needs embedding before this plan completes. |
| 10 | MCP tool surface change | None. MCP tools filter by schema_type; routing happens inside `query.py`. Update MCP-CONTRACT.md docstrings to mention typed-table backend. | If a consumer depends on `source_table='messages'` for DigitalDocument rows (check MCP-CONTRACT.md). |
| 11 | Test strategy | Integration tests for each migration SQL (fixture-based with pre-migration snapshot). Unit tests for adapter routing. Full regression: existing 563-test suite must pass. ~15-20 new tests. | If migration fixture creation is too complex (fall back to live-DB snapshot testing). |
| 12 | Backup strategy | `personal-history.db` → `.pre-0007.gz` before applying 0007. Single backup before the migration set — restoring from this backup undoes all three. | If partial-apply recovery is needed (would require per-migration backups — ~7 GB × 3). |
| 13 | Rollback strategy | Restore from backup. Don't attempt in-place fix of a partial migration — the intermediate state is messy. | Never — backup-restore is always cleaner. |
| 14 | Verification sequence | After 0007: `SELECT COUNT(*) FROM chunks` = pre-rename count. After 0008: `documents` table exists + indexes built. After 0009: `documents` count = pre-migration `messages WHERE schema_type='DigitalDocument'` count; `chunks WHERE source_table='documents'` parity; zero orphan chunks. | If verification queries need to be more granular (e.g., per-adapter row counts). |
| 15 | OneDrive adapter — relative ordering | Adapter retarget (`target_table='documents'`) happens in Phase 2 with other adapters. OneDrive `--apply` happens after all migrations applied on <host> (Phase 5). | If OneDrive needs to run before other adapters are retargeted (no reason it would). |
| 16 | Reference/ body-allowlist — still applies? | Yes — orthogonal to typed-table reshape. Allowlist controls body-extraction policy, not table routing. Patch OneDrive adapter in Phase 2. | If the Reference/ file analysis from 2026-05-14 is revised. |
| 17 | Pre-publish version impact | Reshape is breaking change. First public tag becomes v0.2.0 (not v0.1.0). Alternatively v0.1.0 if we consider pre-public = no breaking-change semantics. | If Rob wants to tag v0.1.0 before the reshape lands (possible but means immediate v0.2.0 follow-up). |
| 18 | Sibling docs to update | CURRENT-SCHEMA.md (rewrite §documents + add §chunks), DECISIONS.md (add reshape decision), writing-an-adapter.md (mention target_table), PII_BASELINE.md (new table inherits baseline), MCP-CONTRACT.md (docstring update). | If additional docs reference the `documents` table by name. |

## Phase 1 — Author migration SQL (Sonnet)

- **0007_chunks_rename.sql**: `ALTER TABLE documents RENAME TO chunks`. Drop + recreate `doc_fts` with `content='chunks'`. Drop + recreate triggers with `chunks_` prefix. Rename indexes for clarity (drop old, create new with `idx_chunks_*` names).
- **0008_documents_typed_table.sql**: `CREATE TABLE documents` with the locked column shape (id, schema_type, rfc822_message_id, subject, file_path, file_size, mtime, ctime, body_text, body_text_source, body_text_hash, raw_hash, is_bulk, source_file_id, bucket, created_at). Indexes: `UNIQUE(source_file_id, raw_hash)`, `idx_documents_path`, `idx_documents_bucket`.
- **0009_documents_migrate.sql**: INSERT INTO documents SELECT FROM messages WHERE schema_type='DigitalDocument' with COALESCE bucket logic. UPDATE chunks SET source_table='documents' + repoint source_id. DELETE FROM messages WHERE schema_type='DigitalDocument'. Parity verification queries embedded as comments.
- Each migration registers in `schema_migrations`.

**Deliverable:** Three `.sql` files in `src/phdb/migrations/project/` + rollback SQL comments in each.

## Phase 2 — Adapter refactor (Sonnet)

- **base.py**: Add `target_table: str = "messages"` class attribute. Add `_INSERT_DOCUMENT_SQL` constant. Add `_insert_document()` method. Route in `_insert_row()` based on `self.target_table`. Modify `run()` to skip thread machinery when `target_table != "messages"`.
- **Retarget adapters**: Set `target_table = "documents"` on `GoogleDriveAdapter`, `OneDriveAdapter`, `AppleNotesFullAdapter`, `StagedMdAdapter`.
- **OneDrive adapter patch** (paired): Update `INCLUDE_TOP_DIRS` from stale PARA dirs to post-reorg `{"Outputs", "Reference", "Records"}`. Add `BULK_DIRS` set. Add Reference body-allowlist logic (per-subdir active-pursuit vs metadata-only routing per memory `project_onedrive_reference_allowlist.md`).
- **AdapterRow**: No structural changes — document-specific fields (file_path, file_size, ctime, bucket) go through `row.extra` dict or new optional fields on AdapterRow.

**Deliverable:** Modified `base.py` + 4 adapter files. OneDrive adapter fully patched for post-reorg F:\OneDrive\ shape.

## Phase 3 — Query + embed pipeline (Sonnet)

- **query.py** (~784 LOC): Replace hard-coded `d.source_table = 'messages'` joins with source_table-aware routing. When hydrating a chunk hit: `source_table='messages'` → SELECT FROM messages; `source_table='documents'` → SELECT FROM documents. UNION pattern for cross-table search. Update `search()`, `get_message()`, `get_stats()`, and any other functions that assume messages-only.
- **embed_pipeline.py**: Rename all internal `documents` references to `chunks` (table name in SQL). Add second chunking pass: after processing `messages`, process `documents` table rows using same chunk strategy. The `source_table` column in `chunks` INSERT distinguishes origin.

**Deliverable:** Modified `query.py` + `embed_pipeline.py`. Semantic search returns results from both `messages` and `documents` tables.

## Phase 4 — Tests (Sonnet)

- **Migration integration tests**: Fixture DB with known pre-migration state → apply each migration → verify post-state (row counts, column presence, FK integrity, FTS rebuild correctness).
- **Adapter routing tests**: Unit test that `GoogleDriveAdapter.target_table == "documents"` and that `_insert_row` dispatches to `_insert_document`.
- **Query routing tests**: Verify `search()` returns results from both source tables. Verify `get_message()` correctly hydrates from `documents` when `source_table='documents'`.
- **Embed pipeline tests**: Verify both `messages` and `documents` rows get chunked.
- **Full regression**: Run existing 563-test suite. Zero failures.

**Deliverable:** ~15-20 new tests pass. Full suite green.

## Phase 5 — Execute on <host> (Rob-assisted)

This phase runs on Rob's machine (FUSE+WAL incompatible with Cowork sandbox for write-heavy migrations).

1. Backup: `personal-history.db` → `personal-history.db.pre-0007.gz`
2. Apply migration 0007. Verify: `SELECT COUNT(*) FROM chunks`.
3. Apply migration 0008. Verify: `SELECT sql FROM sqlite_master WHERE name='documents'`.
4. Apply migration 0009. Verify: parity counts + zero orphan chunks.
5. Run full test suite against migrated DB.
6. OneDrive adapter dry-run.
7. OneDrive `--apply`.
8. Embed pass (`embed_pipeline.py`) for new documents.

**Deliverable:** Live DB migrated. OneDrive content ingested. Embeddings complete.

## Phase 6 — Doc propagation (Sonnet)

- **CURRENT-SCHEMA.md**: Add `chunks` table section (renamed from documents). Add new `documents` typed table section. Update migration status table (add 0007/0008/0009). Update behavior-preservation notes.
- **DECISIONS.md**: Add reshape decision entry per template.
- **writing-an-adapter.md**: Mention `target_table` attribute and when to set it to `"documents"`.
- **PII_BASELINE.md**: New `documents` table inherits baseline (file paths may contain PII — same risk as `messages.subject`).
- **MCP-CONTRACT.md**: Update docstrings to note typed-table backend. No API surface change.
- **VAULT-HISTORY.md** (vault side): Entry for each migration apply.

**Deliverable:** All sibling docs updated. No stale references to pre-reshape schema.

## Execution architecture

```
master (Sonnet, Claude Code)
├── (in-process SQL)          → migration authoring [Phase 1]
├── (in-process Python)       → adapter refactor, query/embed updates [Phase 2, 3]
├── (in-process Python)       → test authoring + regression [Phase 4]
├── (Rob, <host>)         → migration apply + OneDrive ingest [Phase 5]
└── (in-process docs)         → sibling doc propagation [Phase 6]
```

| Phase | Lead | Rationale |
| :--- | :--- | :--- |
| 1 — Migration SQL | Sonnet | SQL authoring is mechanical given the locked schema shape |
| 2 — Adapter refactor | Sonnet | Routing logic is straightforward; OneDrive patch is the most complex item |
| 3 — Query + embed | Sonnet | UNION routing pattern is well-understood |
| 4 — Tests | Sonnet | Test authoring tracks implementation phases |
| 5 — Execute | Rob | Write-heavy migrations must run on <host> |
| 6 — Docs | Sonnet | Mechanical propagation |

No subagent dispatch — all phases are single-thread Code-session work except Phase 5 (Rob executes commands).

## Persistent state files

| File | Purpose |
| :--- | :--- |
| `SCHEMA_RESHAPE_PLAN.md` (this file) | Canonical execution plan |
| `DECISIONS.md` | Phase 0 outcomes (append reshape decision) |
| `src/phdb/migrations/project/0007_chunks_rename.sql` | Migration: rename documents→chunks |
| `src/phdb/migrations/project/0008_documents_typed_table.sql` | Migration: CREATE new documents table |
| `src/phdb/migrations/project/0009_documents_migrate.sql` | Migration: move rows + repoint chunks |
| `CURRENT-SCHEMA.md` | Updated schema snapshot |

## Open follow-ons (deferred)

- **Schema Reshape — Observation Tables**: ~6M Apple Health rows; biggest blast radius; next-priority carve-out after this plan lands.
- **Schema Reshape — Lifestream Tables**: Spotify/Goodreads/Calendar/Facebook posts — smaller, can batch.
- **Migration 0010+ — drop dead columns from messages**: `sender_address`, `sender_domain`, `direction` become fully vestigial once enough schema_types migrate. Wait until 70%+ have moved.
- **AdapterRow discriminated union**: Cleaner type safety, but 38 adapters × 2 test paths. Revisit if adapter count doubles or type errors become frequent.
- **Embed pipeline file rename**: `embed_pipeline.py` name is fine post-reshape. Only revisit if a third source table makes the name confusing.

## Inputs to Claude Code session

- This plan (`SCHEMA_RESHAPE_PLAN.md`)
- `CURRENT-SCHEMA.md` — current table shapes
- `src/phdb/migrations/project/0001_init.sql` — documents/FTS/trigger definitions
- `src/phdb/adapters/base.py` — current insert routing
- `src/phdb/adapters/onedrive.py` — stale INCLUDE_TOP_DIRS to patch
- `src/phdb/adapters/google_drive.py` — sender_name smuggles bucket
- `src/phdb/query.py` — hard-coded source_table joins
- `src/phdb/embed_pipeline.py` — hard-coded documents refs
- Memory: `project_onedrive_reference_allowlist.md` (Reference body-extract policy)
- Memory: `feedback_sandbox_limits.md` (Cowork write limits → Phase 5 on <host>)

## Status

- **Phase 0** — Walked in Cowork 2026-05-14 (rows 1-3 locked) and confirmed in Code session. **COMPLETE.**
- **Phase 1** — Three migration SQL files authored. **COMPLETE.**
- **Phase 2** — base.py routing + 4 adapter retargets + OneDrive body-allowlist. **COMPLETE.**
- **Phase 3** — query.py UNION hydration + embed_pipeline.py chunks rewrite. **COMPLETE.**
- **Phase 4** — Test suite: 742 tests pass, 0 failures. ~20 new tests for migration integration + adapter routing. Two real bugs found and fixed in migration 0009 (COALESCE order, CASCADE ordering). **COMPLETE.**
- **Phase 5** — Execute on <host>. Backup (`personal-history.db.pre-0007.gz`), migrations 0007-0009 applied (220,164 chunks preserved, 141 DigitalDocument rows migrated, 1,628 chunks repointed, 0 orphans), OneDrive `--apply` (2,183 inserted), embed pass (12,224 new chunks → 234,561 total, 0 pending). **COMPLETE.**
- **Phase 6** — Sibling doc propagation: CURRENT-SCHEMA.md, DECISIONS.md, writing-an-adapter.md, architecture.md, MCP-CONTRACT.md all updated. **COMPLETE.**
