---
created: 2026-05-07
revised: 2026-05-07
status: draft
type: project-plan
related:
  - "[[Brain Soup/AI Sessions DB Migration]]"
  - "[[Source Material/Google Activity 2026-04-28/manifest|Google Activity 2026-04-28]]"
  - "[[project_personal_history_db]]"
  - "[[project_personal_history_mcp]]"
  - "[[REWRITE_PLAN]]"
---

# AI Sessions — DB Ingest Plan

## Goals

- Graduate `Timelines/AI Sessions/` (Gemini Scribe + web Gemini markdown landmarks, plus all future Claude Code sessions) from vault markdown into `personal-history-db` rows
- Adopt **Claude Code's JSONL session format** as the canonical shape for an AI conversation
- Preserve branch identity for forked Gemini conversations (Trunk/Branch tree from `MyActivity.html` + landmark files)
- Retire vault landmarks after DB import is verified — they are implementation reference, not permanent state
- Keep the existing `personal-history-db` MCP tool contracts; this plan is additive to schema and adapters, not a query API change

## Non-goals

- No `claude-web` adapter — defer until claude.ai exports become a thing
- No re-design of existing `Conversation` @type — `SCHEMA.md §5.11` already covers AI sessions; this plan adds adapters, not types
- No periodic / scheduled ingest — manual runs only (per Rob 2026-05-07)
- No full text-ingest of Gemini-web attachments — register them as references; defer body ingest
- No vault-side query surface beyond what the MCP already exposes

## Sequencing principles

1. **Smallest falsifiable test first.** Claude Code adapter proves the schema and validates the pattern before the more complex Gemini-web work.
2. **Schema migration is a one-shot, additive ALTER.** No retroactive backfill across other source kinds; `metadata` JSON and new `messages` columns default-NULL on existing rows.
3. **Gemini-web boundary derivation is mechanical, not fuzzy.** The verb-prefix discovery (2026-05-07) makes it deterministic — no need to recover the previous session's boundary work.
4. **Behavior preservation via vault landmark counts.** Per-branch row counts must match landmark `description:` message counts within ~10% before retirement.
5. **No phase exits without green CI.** Same as REWRITE_PLAN.
6. **Default to Gemini for code generation, Claude for design and validation.** Same routing as REWRITE_PLAN.

## Resource notation

Same as `REWRITE_PLAN.md`:

- **[H]** Haiku — mechanical, pattern-clear, boilerplate, file ops
- **[S]** Sonnet — ordinary development; validation, integration, test running, schema work
- **[O]** Opus — architectural decisions, framework design, irreducible judgment
- **[G→S]** Gemini drafts, Sonnet validates — most code generation from clear specs
- **[G→O]** Gemini drafts, Opus validates — generated code with architectural or security stakes
- **[O→R]** Opus recommends, Rob decides — choices that need analysis but are Rob's call
- **[R]** Rob — irreducibly human: decisions, real-corpus testing, physical moves

---

## Execution architecture

Same master/subagent dispatch as `REWRITE_PLAN.md` — Opus master orchestrates, Sonnet/Haiku subagents execute, Gemini CLI drafts via Rob's terminal.

### Master model

**Opus master** for Phases 0, 1, 3 (boundary algorithm), and 5 (MCP refresh + retirement). **Sonnet master** acceptable for Phase 2 (Claude Code adapter port) and Phase 4 (Gemini Scribe adapter) once the schema is locked.

### Subagents

Reuse the custom subagents defined in `REWRITE_PLAN.md`:

- **Adapter validator (Sonnet)** — runs golden-diff + landmark-count check; returns structured report
- **Gemini prompt writer (Sonnet)** — composes self-contained Gemini CLI prompts

New subagent for this plan:

- **Boundary deriver (Opus)** — given a set of `Gemini - *.md` landmark files + a `MyActivity.html` path, produces the `(branch_key, parent_thread_key, start_timestamp, end_timestamp)` table. Opus because Branch 3's `"From Branch • Branch • ..."` lineage decision is judgment-shaped.

### Persistent state files

Add to project repo:

- `docs/ai-sessions-plan/INVENTORY.md` — vault landmark inventory + match status against `MyActivity.html`
- `docs/ai-sessions-plan/BOUNDARY_TABLE.md` — derived branch boundary table; the source of truth for Phase 3 ingest
- `docs/ai-sessions-plan/PORT_LOG.md` — per-adapter port status, golden-diff results
- `docs/gemini-prompts/ai-sessions/` — Gemini prompts for adapter scaffolding

---

## Phase 0 — Pre-flight & decisions

**Objective:** Lock the questions that, if left ambiguous, will cause rework. Verify the takeout shape one more time on <host>.

### Tasks

- [R] Confirm Gemini CLI auth still works (per `REWRITE_PLAN.md` Phase 0; should be carried over)
- [R] Verify `D:\<archives>\2026-04-28 Google Takeout\Takeout\My Activity\Gemini Apps\MyActivity.html` is intact on <host> (Cowork extracted it through FUSE; large files have a non-zero corruption risk)
- [O→R] Decide attachment-handling policy for Gemini-web (default: register as `source_kind='gemini-web-attachment'` references; full text-ingest deferred). Confirm.
- [O→R] Decide whether to ingest the older `D:\2026-05-07 Downloads\takeout-20260307T201803Z-3-001.zip` for older Gemini history (low priority — unzip -l first to see if it has a `Gemini Apps/` stream at all)
- [O→R] Decide treatment of `kind='branch_fork'` rows in default MCP retrieval (recommend: filter from default search; expose `include_fork_markers=true` flag)
- [O→R] Decide canonical ordering of un-landmarked Gemini conversations (auto-stub landmarks vs DB-only vs triage report) — see `Brain Soup/AI Sessions DB Migration §7` for the three options. Default: triage report.
- [O] Inventory the 8 vault landmark files: catalog `name`, `url`, `created`, `description` (message count), `branch_lineage` (derived from `From Branch • ...` prefix). Output to `INVENTORY.md`.
- [G→S] Inventory `MyActivity.html` outer-cells: count by verb (`Prompted`/`Branched`/system events), distribution by month, count of distinct first-prompts. Output to `INVENTORY.md`.

### Tooling setup

- [G→S] Scaffold `docs/ai-sessions-plan/` with INVENTORY.md, BOUNDARY_TABLE.md, PORT_LOG.md skeletons
- [O] Define **Boundary deriver** subagent (system prompt, allowed tools, model)

### Exit criteria

- All open Phase 0 decisions resolved and committed to a `DECISIONS.md` row block
- Landmark inventory complete (8 files cataloged with all metadata)
- `MyActivity.html` outer-cell stats match prior Cowork-side findings (440 cells: 292 Prompted + 135 Branched + 9 system + 4 unparsed)
- Boundary deriver subagent tested on a throwaway task

---

## Phase 1 — Schema migration

**Objective:** Add columns needed for AI session ingest. Idempotent ALTERs, no backfill.

### Tasks

- [O] Design migration `0XXX_ai_sessions.sql`:
  - `messages.kind TEXT` — `'message' | 'tool_use' | 'tool_result' | 'summary' | 'sidechain' | 'branch_fork'`
  - `messages.role TEXT NULLABLE` — `'user' | 'assistant' | 'system' | NULL`
  - `messages.parent_uuid TEXT NULLABLE INDEXED` — graph traversal target, not FK
  - `messages.tool_name TEXT NULLABLE` — promoted out of payload for Bash/Edit/etc. filters
  - `messages.tool_use_id TEXT NULLABLE` — links `tool_use` ↔ `tool_result` rows
  - `messages.model TEXT NULLABLE` — per-turn model name
  - `messages.payload JSON NULLABLE` — raw JSONL line (Claude) or raw HTML cell (Gemini); preserves anything not promoted
  - `threads.metadata JSON NULLABLE` — Claude: `{gitBranch, cwd, version, userType}`. Gemini-web: `{branch_key, branch_lineage, parent_thread_key}`
  - `threads.cwd TEXT NULLABLE INDEXED` — promoted for "show me sessions touching the vault" queries
- [O] Decide whether to add `kind` to existing index set or only on new index (likely: add `(source_kind, kind, date)` composite index)
- [G→S] Implement migration from Opus design
- [G→S] Migration tests: apply on synthetic DB, verify columns exist, verify index works
- [S] Apply migration to dev DB; verify existing sources (gmail/imessage/discord/raindrop) unaffected
- [G→S] Update `Adapter` base class (or shared helpers) to accept the new fields when writing rows

### Exit criteria

- Migration applies cleanly on dev DB and on a copy of production DB
- All existing adapters still pass their own tests post-migration
- New columns + index present and `EXPLAIN QUERY PLAN` shows index usage on the composite key

---

## Phase 2 — `claude-code` adapter

**Objective:** Smallest falsifiable test. Validates schema + flattened model + JSONL ingest before tackling Gemini complexity.

### Tasks

- [O→R] Confirm JSONL source path: `~\.claude\projects\<encoded-cwd>\<session-uuid>.jsonl`
- [O] Confirm the 5 `kind` values (`message` / `tool_use` / `tool_result` / `summary` / `sidechain`) cover everything actually seen in a sample JSONL — spot-check one Cowork-session JSONL on <host>
- [G→S] Implement `claude-code` adapter (extends `Adapter` base class):
  - Walks `~\.claude\projects\` recursively
  - One thread row per JSONL file (`thread_key = filename UUID`)
  - Flattened message rows per JSONL line (assistant turn with N tool_use blocks → 1 `kind='message'` + N `kind='tool_use'` rows)
  - `parent_uuid` = the JSONL `parentUuid` field, text only
  - `metadata` JSON on threads = `{gitBranch, cwd, version, userType}`
  - `payload` JSON on messages = full raw JSONL line for re-derivation
- [G→S] Adapter tests: synthetic JSONL fixtures covering all 5 kinds + sidechains + tool chains
- [S] Real-corpus end-to-end: ingest one historical Cowork session JSONL on <host>; verify row counts equal grep counts of each `type:` discriminator in the source file
- [O] If discrepancy >1%, investigate (likely: a new line type Claude Code added that the 5-kind enum doesn't cover — extend or fall through to `kind='unknown'` with payload preserved)
- [G→S] CLI invocation: `phdb ingest --source claude-code --apply`

### Exit criteria

- One real Cowork session JSONL ingests cleanly with row count matching JSONL line count (excluding skipped non-message types per spec)
- Tool-chain reconstruction works: `SELECT … WHERE kind='tool_use' AND tool_use_id=X` returns the use; pairing with `tool_result` works via `tool_use_id` join
- `cwd` index returns "all sessions touching the vault" in <100ms

---

## Phase 3 — `gemini-web` adapter

**Objective:** Ingest the 440 outer-cells of `MyActivity.html` with branch identity preserved. **The complex phase.**

### 3a. Boundary derivation

- [O] Boundary deriver subagent runs against:
  - The 8 vault landmark files (frontmatter + first prompt body each)
  - `MyActivity.html` outer-cells with verb prefixes (`Prompted` / `Branched` / etc.)
- [O] Algorithm:
  1. For each landmark, extract `url`, `created`, first-prompt timestamp (`(HH:MM AM/PM)` from first ad-prompt block), first-prompt verbatim text
  2. For each `Prompted` cell, build a `(timestamp, prompt_hash)` record
  3. For each `Branched` cell, build a `(fork_timestamp, parent_prompt_hash)` record
  4. Match landmarks → `Prompted` cells (Trunk anchors) and → `Branched` cells (Branch anchors) by `(date, ±5min HH:MM, prompt_hash)`
  5. Resolve branch ranges: cells between branch N's anchor and the next branch's anchor (chronologically) belong to branch N
  6. Resolve fork lineage: Branch 3's `"From Branch • Branch • ..."` prefix → `parent_thread_key=Branch 2`; Branch 2 → `parent_thread_key=Trunk`; Branch 1 → `parent_thread_key=Trunk`
- [O] Output: `BOUNDARY_TABLE.md` with `(branch_key, parent_thread_key, gemini_url, start_timestamp, end_timestamp, expected_message_count)` rows
- [R] Rob spot-checks the boundary table — does the lineage match his memory of how he forked?

### 3b. Adapter implementation

- [O] Confirm `kind` mapping for Gemini cells:
  - `Prompted` → one `kind='message'`, `role='user'`; the response below it → one `kind='message'`, `role='assistant'`
  - `Branched` → one `kind='branch_fork'`, role NULL; `parent_uuid` = hash of the original Prompted's text+timestamp
  - System events (`Created`/`Gave`/etc.) → `is_bulk=1`, `kind='system_event'` or skip
- [G→S] Implement `gemini-web` adapter:
  - Reads `MyActivity.html` + `BOUNDARY_TABLE.md` + landmark inventory
  - One `threads` row per branch (`thread_key = gemini.google.com/<id>`)
  - `messages` rows assigned to threads via boundary table
  - `metadata` JSON carries `branch_key`, `branch_lineage`, `parent_thread_key`
- [G→S] Attachment registration sub-step: for each of the 130 hash-suffixed files in `Gemini Apps/`, register as `source_kind='gemini-web-attachment'`, `thread_key=<branch-key>` (Option 2 from the migration spec). Defer body ingest.
- [G→S] Adapter tests with synthetic mini-`MyActivity.html` + synthetic landmarks
- [S] Real-corpus run: ingest the 2026-04-28 takeout's `MyActivity.html`
- [S] **Validation gate:** per-branch row counts vs landmark `description:` message counts:
  - Branch 1 expected ~114 messages
  - Branch 2 expected ~264 messages
  - Branch 3 expected ~306 messages
  - Trunk: count derived from landmark prose (no description field)
  - Tolerance: ±10%
- [O] Investigate any branch outside tolerance — likely a boundary-table bug; iterate

### 3c. Un-landmarked conversations

- [G→S] Generate triage report: list of Gemini conversations in `MyActivity.html` whose first `Prompted` doesn't match any landmark file
- [R] Rob reviews the triage report and decides per-conversation: stub-landmark / DB-only / drop

### Exit criteria

- All 4 known branches (Trunk + 3) ingest within 10% tolerance of landmark message counts
- Fork lineage correctly encoded — `SELECT branch_lineage, parent_thread_key FROM threads WHERE source_kind='gemini-web'` shows the tree
- Triage report delivered for un-landmarked conversations
- 130 attachments registered

---

## Phase 4 — `gemini-scribe` adapter

**Objective:** Ingest the 5 vault Gemini Scribe markdown files. Smallest scope — the 5 files have explicit per-prompt timestamps and structured ad-prompt/ad-ai-response blocks.

### Tasks

- [O→R] Identify the 5 files (filter `Timelines/AI Sessions/*.md` for `tool: "Gemini Scribe"`)
- [G→S] Implement `gemini-scribe` adapter:
  - One thread row per file (`thread_key = session_id` from frontmatter)
  - Walk each ad-prompt/ad-ai-response pair; emit one `messages` row per block
  - `metadata` JSON on threads = `{enabled_tools, accessed_files, last_active}` (preserve since they're already in vault and have low risk)
- [G→S] Adapter tests
- [S] Real-corpus run; verify row counts match prompt+response block counts in source files

### Exit criteria

- All 5 files ingest cleanly
- Row counts match block counts in source files

---

## Phase 5 — MCP query refresh + landmark retirement

**Objective:** Make AI sessions queryable through existing MCP tools without contract drift. Then retire vault landmarks.

### Tasks

- [S] Verify existing MCP tools (`search_messages`, `get_thread`, etc.) handle `source_kind IN ('claude-code', 'gemini-web', 'gemini-scribe')` correctly — no new tools, just new data
- [S] Add default-filter logic to retrieval: skip `kind IN ('branch_fork', 'summary', 'sidechain', 'system_event')` unless `include_meta=true` flag
- [S] End-to-end MCP test: query for a known phrase from a real Gemini conversation; confirm correct branch's thread is returned
- [O] Verify embedding policy applied correctly — `is_bulk=0` for `kind='message'`, `is_bulk=1` for everything else; queue any unembedded message rows for the next embed pass
- [R+S] Smoke test: Rob queries the MCP for a known recent Cowork session and a known Gemini conversation; verify both return useful results
- [O] **Verification gate before landmark retirement:**
  - Per-branch row counts match landmark `description:` message counts within 10%
  - At least 3 randomly-sampled landmark conversations queryable through MCP and return correct content
  - No regression on existing source kinds (sample queries against `gmail`, `imessage`, `discord` still return correct results)
- [R] Rob blesses retirement
- [O] Per `AGENTS.md §2.7`: pillar contents change (large delta in `Timelines/AI Sessions/`) → propagation: `AGENTS.md`, `Folder Hierarchy.md §2.4`, `VAULT-HISTORY.md` row; consider whether `note_type` for archived landmarks needs adjustment
- [H] Move `Timelines/AI Sessions/Gemini - *.md` files to `Archives/Timelines-AI-Sessions-2026-05-XX/` (preserve as historical record; not deleting)
- [H] Move other `Timelines/AI Sessions/` files (Gemini Scribe markdown files, etc.) to the same archive location
- [G→S] Update memory entries:
  - `feedback_logs_as_timeseries.md` — note that AI sessions are now DB-resident
  - Any `project_personal_history_*` entries that mentioned vault AI Sessions as canonical
- [O] Update `SCHEMA.md §5.11` Conversation routing table — change AI Sessions row from `vault markdown / Timelines/AI Sessions/` to `DB only` (matches Gmail / iMessage rows)

### Exit criteria

- All Gemini + Claude Code conversations queryable through MCP with default filters working
- `Timelines/AI Sessions/` empty (or contains only an index file pointing to the archive)
- Governance docs updated per propagation table
- VAULT-HISTORY.md row appended

---

## Cross-cutting concerns

### Test discipline

Every phase exits with green CI. Phase 3's validation gate (per-branch row counts within 10%) is the load-bearing test for the whole plan — it's the thing that proves the boundary derivation worked. Don't skip it.

### Documentation cadence

Update `Brain Soup/AI Sessions DB Migration.md` (the spec) as the plan executes — particularly any of the deferred decisions that get resolved in Phase 0.

### Memory updates

Update vault memory entries as ingest completes:

- `feedback_logs_as_timeseries.md` — supersede note: AI sessions are DB-resident; sidechain preservation lives in `kind='sidechain'` rows
- `project_personal_history_db.md` — schema updated 2026-05-XX with AI sessions migration
- `project_personal_history_mcp.md` — note that `source_kind` set expanded; tool contracts unchanged

### Governance sync

Per `AGENTS.md §2.7`:

- Phase 1 (schema migration) → no governance impact (DB-only addition)
- Phase 5 (landmark retirement) → pillar contents changed: `AGENTS.md` if pillar description changes, `Folder Hierarchy.md §2.4` (Timelines subdir), `VAULT-HISTORY.md`
- `SCHEMA.md §5.11` table update is the most important — the Conversation routing now says AI sessions are DB-resident, not vault markdown

### Behavior preservation budget

| Comparison | Tolerance |
| :--- | :--- |
| Per-branch row count vs landmark `description:` message count | ±10% |
| Tool-chain integrity (`tool_use` → `tool_result` join completeness) | 0% — every `tool_use` must have either a matching `tool_result` or be terminal at session end |
| Embedding policy correctness | 0% — `kind='message'` rows must be `is_bulk=0`, others `is_bulk=1` |
| MCP existing-tool responses on existing source_kinds | 0% — golden-diff must match pre-migration |

### Python environment

Per `REWRITE_PLAN.md` cross-cutting concerns: per-project `uv` venv, `uv run` for invocations.

### Gemini hygiene

Same as REWRITE_PLAN — never paste live conversation content into Gemini prompts; use synthetic fixtures for adapter scaffolding. The conversations themselves contain Rob's name and PII, so any Gemini-drafted code that ingests them must be tested against synthetic fixtures only, then validated against real data by Sonnet/Opus on <host>.

---

## Risks & mitigations

- **Boundary derivation off** → Phase 3 validation gate catches it before retirement; iterate boundary table
- **Un-landmarked Gemini conversations get incorrectly absorbed into existing branches** → triage report in Phase 3c surfaces them; Rob reviews before retirement
- **JSONL line type Claude Code adds in future versions** → adapter falls through to `kind='unknown'` with payload preserved; never lose data even on schema drift
- **Branched cells contain different prompt text than the original (Rob edited the prompt before forking)** → boundary table uses `(timestamp, parent_prompt_hash_first_80_chars)` not full text match; if mismatch, fall back to fuzzy match flagged for Rob
- **Attachment hash suffix doesn't tie to a conversation** → Option 2's `thread_key=<branch-key>` assignment relies on conversation-level reference, not turn-level; if the hash actually identifies turn rather than conversation, attachments end up tagged to one branch when they should span branches. Acceptable lossy behavior for v1; revisit if it matters.
- **Cowork-extracted `MyActivity.html` is corrupted** → Phase 0 verifies on <host>; re-extract from source zip if needed
- **Landmark message counts in `description:` are approximate** → 10% tolerance accommodates this; if real drift exceeds 10%, it's a derivation bug not a count-counting bug

---

## Phase 0 decisions — to resolve

Will be filled in during Phase 0 on <host>:

| # | Question | Resolution |
|--:|---|---|
| 1 | Attachment policy for Gemini-web | (default: register as references; defer body ingest) |
| 2 | Older 2026-03-07 takeout — ingest? | (default: skip; `unzip -l` first to see if Gemini stream exists) |
| 3 | `branch_fork` rows in default MCP retrieval | (default: filter; `include_fork_markers=true` flag) |
| 4 | Un-landmarked Gemini conversations disposition | (default: triage report) |
| 5 | New `kind` enum value for system events (`Created`/`Gave`/etc.) | (default: `'system_event'`) |
| 6 | Embedding policy edge case: `kind='branch_fork'` | (default: `is_bulk=1`) |

---

## Effort distribution

Approximate proportions:

- **[H] Haiku** — ~5% (file moves, archive creation)
- **[S] Sonnet** — ~25% (validation, integration, MCP testing, smoke tests)
- **[O] Opus** — ~30% (schema design, boundary algorithm, triage decisions, governance propagation)
- **[G→*] Gemini** — ~30% (adapter implementation, tests, scaffolding)
- **[R] Rob** — ~10% (decisions, real-corpus testing, lineage spot-check, retirement bless)

Higher Opus share than REWRITE_PLAN because the boundary algorithm (Phase 3a) is irreducibly judgment-heavy — fork lineage interpretation, ±5 minute timestamp matching, parent-prompt-hash comparison logic.

---

## Phase ordering at a glance

```
Phase 0  Pre-flight & decisions       ─── R + Opus heavy
Phase 1  Schema migration              ─── Opus design, Gemini implement, Sonnet validate
Phase 2  claude-code adapter           ─── Gemini implement, Sonnet golden-diff
Phase 3  gemini-web adapter            ─── Opus boundary, Gemini ingest, Sonnet validate
Phase 4  gemini-scribe adapter         ─── Gemini implement (smallest phase)
Phase 5  MCP refresh + retirement      ─── Mixed; Opus governance + R bless retirement
```

No phase begins until the prior phase exits cleanly. Phase 0 cannot begin until `REWRITE_PLAN.md` Phase 8 is complete (project structure stable).
