---
created: 2026-05-06
revised: 2026-05-06b
status: draft
type: project-plan
related:
  - "[[project_personal_history_db]]"
  - "[[project_personal_history_mcp]]"
  - "[[project_database_pivot]]"
---

# Personal-History-DB — Rewrite & Spin-Out Plan

## Goals

- Replace 14K LOC organic codebase with framework-driven architecture
- Three-tier separation: **code** (publishable) / **instance** (PII + Rob-specific config) / **data** (DB, snapshots, staging)
- Preserve working system throughout — no big-bang cutover
- Project repo is open-source-ready from day one (PII surface lives in instance only)
- **Minimize Claude token usage** by routing high-volume code generation through Gemini Pro and using Claude for design + validation

## Non-goals

- No new sources during the rewrite (freeze source list at port time)
- No changes to query semantics or MCP tool contracts (preserve interface)
- No re-embedding from scratch (port carries existing vectors forward)
- This plan starts **after** current in-flight ingest work stabilizes (003.zip Drive allowlist, KDCS sweep, phone SMS modes)

## Sequencing principles

1. **Refactor in place → extract config → physically split.** Don't do all three simultaneously.
2. **Run old and new in parallel** during transitional phases. Diff outputs before retiring originals.
3. **Behavior preservation is verified by data, not code review.** Golden-output diffing on real corpus subsets.
4. **PII extraction is one-pass.** Be aggressive in the first pass — retroactive scrubbing is brutal.
5. **No phase exits without green CI.**
6. **Default to Gemini for code generation, Claude for design and validation.** Only keep work in Claude when the validation cost would exceed the writing cost.

## Resource notation

Each task is tagged with the minimum resource that should produce it:

- **[H]** Haiku — mechanical, pattern-clear, boilerplate, file ops
- **[S]** Sonnet — ordinary development; validation, integration, test running, schema work
- **[O]** Opus — architectural decisions, framework design, PII audits, irreducible judgment
- **[G→S]** Gemini drafts, Sonnet validates — most code generation from clear specs
- **[G→O]** Gemini drafts, Opus validates — generated code with architectural or security stakes
- **[O→R]** Opus recommends, Rob decides — choices that need analysis but are Rob's call
- **[R]** Rob — irreducibly human: decisions, real-corpus testing, repo creation, physical moves, license picks

Combined tags (e.g., **[O→R]**) read left-to-right as a workflow.

---

## Execution architecture

This work is executed in **Claude Code via the VS Code integration**, with a **master session orchestrating typed subagents**. Master holds plan state, makes architectural decisions, validates outputs, and dispatches work. Subagents have isolated context windows and return summarized reports.

### Master model: Opus by default

Opus master is correct for this project because:

- Architectural mistakes compound. A missed PII reference, a wrong dedup strategy, or a subtle MCP contract drift costs more than the per-session model differential.
- Master context stays small in this architecture — subagents do the bulk processing. You pay Opus rates only for the orchestration layer.
- Triage moments (which adapter is project vs. instance, what atom @types are canonical, how to interpret a golden-diff that's slightly off) are Opus-strength judgment calls.

### Drop master to Sonnet for steady-state phases

Phases or phase-segments where the per-task pattern is fully established and validation is largely automated:

- **Phase 4 mid-stream** — after ~5 successful adapter ports the pattern is set; Sonnet master can dispatch the long tail with diff-driven validation catching drift
- **Phase 8 docs batch** — templated, low-risk

Resume Opus master for any phase requiring fresh architectural judgment, every phase opener, and Phase 5 (contract preservation) and Phase 7 (governance propagation) regardless.

### Subagent assignments

Built-in agents:

- **Plan** — phase planning; recovery when a subagent returns surprising results
- **Explore** — large-file inventory; pattern-finding across the codebase
- **general-purpose (Sonnet)** — adapter ports, test writing, scaffold implementation, doc drafts when not delegated to Gemini
- **general-purpose (Haiku)** — file moves, lint runs, mechanical updates

Custom subagents worth defining once (amortize across phases):

- **PII auditor (Opus)** — scans project code against PII regex set; returns findings with file/line citations
- **Adapter validator (Sonnet)** — runs golden-diff against legacy on a real corpus subset; returns structured report (row counts, ID-set differences, content drift samples)
- **Gemini prompt writer (Sonnet)** — composes self-contained Gemini CLI prompts from a task spec; produces both the prompt file and the `gemini` invocation command

### How Gemini fits in the dispatch pattern

Gemini CLI is treated as a tool the master invokes **via Rob**:

1. Master (or scoped Sonnet subagent) writes prompt to disk
2. Master surfaces the prompt path and the command (`gemini < prompts/port-imessage.md > new/imessage.py`)
3. Rob runs the command in his terminal; output lands on disk
4. Master dispatches Adapter validator subagent against the new output
5. Validator returns report; master decides accept / iterate / fix

Master never directly invokes Gemini. The human-in-the-loop step keeps Rob aware of token-cost decisions and prevents runaway delegation chains across tools.

### Subagent context discipline

Subagents have isolated context. Master must:

- Pass enough context for the subagent to succeed standalone (relevant file paths, base class spec, golden-diff baseline location)
- Ask for **summarized reports**, not raw outputs — prevents context bloat in master
- Persist cross-phase state to disk (e.g., adapter triage inventory, port progress tracker) so subsequent sessions can resume cleanly without master needing to re-read everything

### Persistent state files for the project

Create at Phase 0, update across all phases:

- `REWRITE_PLAN.md` (this file) — the spec
- `INVENTORY.md` — per-file triage status (keep / port / retire)
- `PORT_LOG.md` — per-adapter port status, diff results, decisions made
- `DECISIONS.md` — Phase 0 decisions and any in-flight changes
- `docs/gemini-prompts/` — every Gemini prompt + output, captured for reproducibility

---

## Gemini delegation pattern

### When Gemini wins

- **Output is large relative to spec.** Adapter implementations, doc drafts, scaffolding files, tests from a clear pattern — these are 100s of lines of output from a paragraph of spec. Token-efficient to delegate.
- **Pattern is repetitive across many similar tasks.** Porting 30+ adapters is the canonical example. Same prompt skeleton, swap the source.
- **Gemini has the full input in one shot.** Gemini Pro's large context is a real advantage — you can paste an entire 700-line legacy ingester plus the new base class and ask for a port in one call.
- **Validation has a fast oracle.** If a test suite exists, Claude validates in seconds.

### When Claude wins (don't delegate)

- **Output is small.** Two-line config snippets, single-function utilities — the prompt + validation cost more than just writing it.
- **Task requires deep vault context.** Memory, governance docs, atom @type triage, PII judgment — Gemini doesn't share Claude's accumulated context, and shipping it via prompt is wasteful.
- **Iterative refinement is expected.** Multiple back-and-forth turns lose the cross-tool overhead advantage.
- **Architectural design is the deliverable.** The thinking, not the typing, is the value.

### The workflow

See **Execution architecture → How Gemini fits** for the full master/subagent dispatch pattern. In summary:

1. **Master scopes** — composes a self-contained Gemini prompt with goal, inputs, constraints, output format, gotchas list (UTF-8 stdout, busy_timeout, Z_PK dedup pattern, etc.). Writes prompt to disk under `docs/gemini-prompts/`.
2. **Rob runs** — `gemini < prompts/port-X.md > new/X.py` from VS Code terminal. Output lands on disk.
3. **Master dispatches validator** — Adapter validator subagent runs tests, golden-diff against legacy, returns structured report.
4. **Iterate or accept** — based on the report, master either accepts (subagent retires legacy), drafts a fix prompt for the next Gemini turn, or applies a small fix directly.

### Tooling preference: CLI over web

Gemini CLI is the default for this project. Rationale:

- File-as-input / file-as-output — no copy-paste of 700-line ingesters
- Scriptable batching — one shell loop ports a directory of adapters
- Outputs land directly where they need to live for git
- Prompt + result archival is automatic via shell redirection
- Rob's Google AI Pro subscription auths into CLI with raised limits + $10/mo Cloud credits

**Use the web app** for: Phase 0 exploration, single-deliverable design tasks, conversational iteration on a result before saving.

**Known caveat to verify in Phase 0:** some Google AI Pro subscribers hit 403 PERMISSION_DENIED errors on first CLI auth despite correct subscription detection (open issue google-gemini/gemini-cli#24517). Verify CLI auth works before depending on it for Phase 4.

### Batch when possible

Gemini's large context can absorb entire codebases. Batch related work into single calls when the dependencies are clean:

- Three small related adapters → one Gemini call
- A whole module's tests → one call
- A doc set (README + CONTRIBUTING + adapter-authoring guide) → one call

Don't batch when batches would mask validation issues per-unit.

### Prompt skeleton (Claude writes these)

```
GOAL: [one paragraph]

INPUTS:
- [file 1 contents]
- [file 2 contents]
- [base class / spec]

CONSTRAINTS:
- Python 3.11+
- ruff + mypy clean
- [other style/dependency pins]

OUTPUT FORMAT: [one file / multi-file with separators / unified diff]

KNOWN GOTCHAS — DO NOT REPRODUCE THESE BUGS:
- All ingesters need `sys.stdout.reconfigure(encoding="utf-8")` for Windows
- Connection factory must set `busy_timeout=30000`
- Dedup keys must be Z_PK or equivalent primary key, never domain identifiers
- [task-specific pitfalls]

VALIDATION CRITERIA: [what "good" looks like — Gemini self-check]
```

---

## Phase 0 — Pre-flight & decisions

**Objective:** Lock the questions that, if left ambiguous, will cause rework later.

### Tasks

- [R] Decide project name and instance name
- [R] Decide on-disk paths for project / instance / data dirs
- [R] Decide repo strategy (project: public GitHub; instance: private git or local-only; data: backups, no git)
- [O→R] Decide which atom @types are canonical-and-publishable vs. instance-specific
- [O] Decide migration namespacing scheme (recommend project 0001–0999, instance 1000+)
- [O] Decide config format (recommend TOML for instance config, Pydantic for schemas)
- [O] Decide adapter discovery mechanism (entry points vs. configured Python path)
- [R] Decide license (MIT, Apache 2.0, BSL)
- [O→R] Decide embedding model — keep current or upgrade during rewrite
- [G→S] Inventory the 41 existing `.py` files: keep / port / retire (Gemini reads files, produces tagged inventory; Claude spot-checks)
- [G→S] Inventory current schema: tables, columns, indexes
- [R] Inventory data dependencies: live DB path, backup paths, staging dirs (Rob knows the actual layout)
- [G→S] Document current MCP tool contracts (the behavior preservation target)
- [O] PII surface audit: enumerate every place Rob-specific values appear in current code

### Tooling setup

- [R] Verify Gemini CLI auth works with Google AI Pro subscription (watch for the 403 PERMISSION_DENIED known issue; resolve before Phase 4)
- [R] Confirm Claude Code VS Code integration is configured against the vault and the future project dir
- [O] Define custom subagents: PII auditor, Adapter validator, Gemini prompt writer (system prompts, allowed tools, model assignments)
- [G→S] Scaffold persistent state files: `INVENTORY.md`, `PORT_LOG.md`, `DECISIONS.md`, `docs/gemini-prompts/`

### Exit criteria

- Decisions doc committed
- Inventory complete and tagged
- "Sources of truth" list for behavior preservation
- Gemini CLI auth verified working
- Custom subagents defined and tested on a throwaway task

---

## Phase 1 — Project scaffold (in place)

**Objective:** Build framework skeleton inside current location. No moves yet.

### Tasks

- [H] Create new `personal-history-db/` parallel subdir for new code
- [G→S] `uv init` to scaffold `pyproject.toml` + `uv.lock`; pin deps; configure ruff, mypy, pytest (per `System/Governance/TOOL-KIT.md` Python environment conventions)
- [G→S] Write `db.py` — connection factory with WAL, busy_timeout, foreign keys, context manager
- [O] Design `Adapter` base class — `name`, `unique_key_strategy`, `update_policy`, `iter_rows()`, `parse_date()`
- [O] Design migration runner with namespaced migrations
- [G→S] Implement migration runner from Opus design
- [O] Design Pydantic settings with three-tier merge (defaults → package config → instance config)
- [G→S] Implement settings module
- [O] Design atom registry — how @types are declared, validated, queried
- [G→O] Implement atom registry framework (architectural code → Opus validation)
- [G→S] Logging module with sanitization layer; default mode is "structural-only"
- [G→S] CLI entrypoint scaffold — `phdb` command with `ingest`, `embed`, `query`, `migrate`, `stats` subcommands
- [O] Design test fixture generator (the design must accommodate every adapter type)
- [G→S] Implement fixture generator
- [G→S] CI workflow: lint, type check, test, PII scanner
- [O] PII scanner config — regex patterns for known-personal terms (own name, contacts, codenames <codename>/<codename>/<codename>/<codename>)
- [H] Pre-commit hook setup
- [G→S] Project README skeleton, CONTRIBUTING skeleton

### Exit criteria

- `pytest` green on synthetic fixtures end-to-end (ingest → embed → query)
- `phdb --help` shows subcommands
- CI passes on a fresh clone

---

## Phase 2 — Reference adapter & loader

**Objective:** Prove the framework on a real source. Establish the canonical "this is what an adapter looks like" example.

### Tasks

- [O→R] Choose reference adapter — recommend `mbox` (generic format, 559 LOC current)
- [O] Architect mbox adapter against base class
- [G→S] Implement mbox adapter
- [G→S] Unit tests for mbox adapter (date parsing, dedup key, edge cases)
- [G→S] End-to-end test on synthetic mbox: ingest → embed → query
- [S] Golden-diff against legacy `ingest_mbox.py` on a small real corpus subset (Sonnet runs and reviews)
- [G→O] Build adapter loader — discovers adapters from project + instance via entry points or path (architectural → Opus validation)
- [O] Verify external-adapter authoring works without touching project code (write a throwaway external adapter and prove the loader picks it up)
- [G→S] Documentation: "Writing a new adapter" guide

### Exit criteria

- mbox adapter passes diff against legacy on real subset
- Adapter loader works for both project-internal and external adapters
- Adapter authoring is documented end-to-end

---

## Phase 3 — Instance scaffold & PII extraction

**Objective:** Stand up the instance directory and extract the first wave of Rob-specific config.

### Tasks

- [H] Create instance directory structure
- [R] Initialize private git repo (or local-only `git init`)
- [G→S] Create `phdb-instance-template/` in project repo (example configs + TODOs)
- [O] Design people/identity config schema (your aliases, correspondent normalization, contact lookups)
- [R+S] Extract people/identity table — Rob supplies the actual data; Sonnet wires it into config format
- [G→S] Extract Tags Glossary into instance config (mechanical given the Glossary file)
- [O] Design atom @type instance config DSL
- [O] Extract custom atom @types from current code into instance config (judgment-heavy: which atoms stay generic, which become Rob-specific)
- [G→S] Extract source registration (which sources, where files live) into instance config
- [G→S] Extract path/embedding/threshold tunables into instance config
- [O] PII audit pass #1 — re-grep project code for Rob-specific terms
- [G→S] Wire instance config loading into framework
- [G→S] Add instance-config schema validation at framework startup (fail fast on drift)
- [S] Verify project code passes PII scanner clean

### Exit criteria

- Instance config drives behavior (changing config changes outputs)
- Project code grep-clean of personal terms
- mbox adapter still works through full instance config path

---

## Phase 4 — Bulk adapter port

**Objective:** Migrate the remaining ingesters to the new framework, retiring originals. **This is the phase where Gemini delegation pays off most.**

### Per-adapter task template

- [O→R] Triage decision — project adapter / instance-private adapter / delete (Claude recommends, Rob confirms)
- [O] If subtle dedup behavior — design strategy declaration (lesson: Strong Z_PK incident)
- [G→S] Port adapter from legacy file using new framework + base class
- [G→S] Tests for adapter (dedup, date parsing, edge cases)
- [S] Parallel-run + golden-diff against legacy on real corpus subset
- [O] Investigate divergence if any (judgment call: tolerable vs. bug)
- [H] Retire original

### Recommended port order

1. `ingest_imessage.py` — well-behaved, frequently re-run, validates the framework
2. `ingest_mbox.py` — already done in Phase 2 as reference
3. `ingest_discord.py`
4. `ingest_chat_logs.py`
5. `ingest_apple_dbs.py` — Strong Z_PK lesson; careful here
6. `ingest_apple_health.py`
7. `ingest_facebook.py` + `ingest_facebook_residuals.py`
8. `ingest_google_*` (activity, drive)
9. `ingest_onedrive.py`
10. `ingest_phone_sms.py`, `ingest_phone_photos.py`
11. `ingest_raindrop.py` — largest at 986 LOC; save for last when framework is mature
12. `ingest_staged_md.py` — likely retires entirely (its job is done by the new loader)
13. Specialty / one-off adapters — most likely deletion candidates

### Batching strategy

Group adjacent adapters with similar shape into single Gemini calls when feasible:

- Apple SQLite-shaped adapters can share one prompt context (`ingest_apple_dbs`, `ingest_apple_health`, `ingest_imessage`)
- Google export adapters can share one (`ingest_google_*`)
- One-off / specialty adapters validate together at the end

### Exit criteria

- All in-scope sources ported to new framework
- All originals retired
- Aggregate row counts match within tolerance per source

---

## Phase 5 — Query layer cutover

**Objective:** Single query path; retire `retrieve.py`.

### Tasks

- [O] Design `query` module API — typed search/get/list/find_threads/etc.
- [G→S] Implement query module
- [G→S] Tests for query module against synthetic fixture
- [S] Migrate MCP `server.py` to call query module (preserve tool contracts — Sonnet does this carefully, not Gemini, because contract preservation is high-stakes)
- [G→S] Migrate any `retrieve.py` callers (CLI, scripts) to query module
- [S] End-to-end test all 11 MCP tools through new query module (golden-diff against legacy responses)
- [H] Retire `retrieve.py`

### Exit criteria

- MCP tool responses identical to legacy
- One query path; `retrieve.py` deleted

---

## Phase 6 — Embed pipeline hardening

**Objective:** Enforce the two-phase write discipline structurally instead of in human memory.

### Tasks

- [O] Decide enforcement mechanism (separate processes + queue, lockfile coordinator, or scheduler-based sequencing)
- [G→S] Implement chosen mechanism
- [G→S] Tests: simulate concurrent ingest + embed, verify no write-lock conflict
- [G→S] Update docs: "ingest and embed are sequenced; here's how"
- [H] Mark `feedback_db_write_lock_conflict.md` as superseded — discipline is now in code

### Exit criteria

- Concurrent run attempts are sequenced safely
- The discipline lives in code, not in a memory note

---

## Phase 7 — Physical split

**Objective:** Move code, instance, and data to their final locations. Mostly Rob's hands here.

### Tasks

- [R] Move project to target location (Rob does the actual move with care — verify nothing's mid-write)
- [R] Move data files (DB, snapshots, staging) to data location
- [R] Move instance config to instance location
- [G→S] Update all path references in scripts/configs to use new locations
- [S] Update MCP plugin config (DB path)
- [R+S] Smoke test — Rob runs through ingest → embed → query end-to-end; Claude reviews output
- [O] Update governance docs — `AGENTS.md §2.7` propagation table, `COWORK.md` sync rules, `CLAUDE.md` path references (Opus because cross-doc consistency is judgment-heavy)
- [S] Update memory entries that reference old paths
- [R] Delete old `System/Tools/personal-history-db/` from vault (Rob blesses irreversible deletion)
- [R] Initialize project repo on GitHub (public) — first commit is the clean codebase, not a legacy import
- [R] Initialize instance repo (private or local-only)

### Exit criteria

- Three directories at target locations
- All workflows function from new locations
- Vault has no stale references to old paths

---

## Phase 8 — Polish & publish

**Objective:** Make the project genuinely usable by a fresh adopter. Bitrot prevention.

### Tasks

- [G→S] Project README with quickstart
- [G→S] `CONTRIBUTING.md`
- [G→S] `docs/` dir: architecture, adapter authoring, atom @types, configuration reference (good Gemini batch — one prompt, multiple docs)
- [G→S] Fresh-adopter walk-through: start from scratch, set up an instance, ingest synthetic data, run a query
- [G→S] CI workflow on project repo: tests + PII scanner + lint
- [O] Final architectural review of project repo (lingering smells, abstractions that didn't earn their keep)
- [R] License file (Rob picks; mechanical to add)
- [R] First public release tag
- [G→S] Instance README in private repo: "fresh machine recovery" guide
- [O→R] Decide on periodic CI test that sets up a fresh instance from synthetic fixtures (recommend yes)

### Exit criteria

- Fresh-adopter test passes
- Project repo published

---

## Cross-cutting concerns

### Test discipline

Every phase exits with green CI. No phase merges without parallel-run diff against legacy where applicable.

### Documentation cadence

Update docs in the same change that touches code. The fresh-adopter test is the discipline that catches drift.

### Memory updates

Update vault memory entries as architecture changes:

- `project_personal_history_db.md` — schema/tooling
- `project_personal_history_mcp.md` — MCP plugin
- `project_database_pivot.md` — corpus state
- `feedback_db_write_lock_conflict.md` — supersede after Phase 6
- `feedback_file_tool_truncation.md` — preserve (still relevant)

### Governance sync

Per `AGENTS.md §2.7` — schema or path changes touch governance docs. Run propagation before closing each phase.

### Behavior preservation budget

Acceptable golden-diff variance per adapter:
- **Zero** for row counts on a sealed corpus subset
- **Zero** for unique IDs / dedup outcomes
- **Tolerated** for trivial date-format normalization (must be documented per adapter)
- **Tolerated** for whitespace / line-ending differences in chunked text

### Python environment

Per `System/Governance/TOOL-KIT.md`: per-project `uv` virtual environment in local `.venv/`, scaffolded via `uv init` for new projects. Run scripts with `uv run python <script>.py`. The current `System/Tools/personal-history-db/.venv` (set up Phase −1 from `requirements.txt`) is the working environment until Phase 1's `uv init` produces a proper `pyproject.toml` + `uv.lock` for the new project tree.

### Gemini hygiene

- **Never paste live PII into Gemini.** Use synthetic fixtures or redacted samples in prompts. The whole point of the project/instance split is that the project repo holds nothing personal — Gemini work targets the project repo.
- **Capture the prompt with the result.** Save Gemini prompts alongside outputs (e.g., `docs/gemini-prompts/`) for reproducibility. Helps when an adapter needs a re-port a year later.
- **Pin a Gemini model version** in the prompt-capture file. Output style and quality drift across model revisions.

---

## Risks & mitigations

- **Behavior drift during port** → golden-diff testing on real corpus subsets per adapter
- **Instance config drift from project schema** → schema validation at framework startup
- **PII leak through logs** → structural-only logging by default
- **PII leak through commit history** → project repo starts clean; do **not** import legacy git history
- **PII leak through Gemini prompts** → only ever prompt with synthetic data; project-side targets
- **Gemini output quality drift** → validate with tests, not by reading; pin model versions
- **Cross-tool context loss** → keep prompts self-contained; don't expect Gemini to remember prior conversation
- **Bitrot of the open-source path** → fresh-adopter CI test
- **Mid-rewrite ingest failures blocking corpus updates** → keep legacy ingesters runnable until replacements are diff-clean
- **Atom @type design churn** → keep canonical @type list small and stable; instance can extend
- **Strong Z_PK class of bugs recurring** → dedup-strategy declaration is mandatory in adapter base class; named in every Gemini prompt's "gotchas" list

---

## Phase 0 decisions — resolved 2026-05-06

All open questions answered; see `DECISIONS.md` for full rationale per decision.

| # | Question | Resolution |
|--:|---|---|
| 1 | Project name | `personal-history-db` (CLI: `phdb`) |
| 2 | Instance path | `C:\Users\<owner>\Obsidian\personal-history-instance\` |
| 3 | Data path | `C:\Users\<owner>\Obsidian\personal-history-data\` |
| 4 | Config format | TOML for instance; Pydantic for project schemas |
| 5 | Adapter discovery | Configured Python path in instance config |
| 6 | License | MIT |
| 7 | Embedding model | Keep `nomic-embed-text` (768-dim); make pluggable via instance config |
| 8 | Atom @types | Schema.org standard → project; custom slugs (`dec`, etc.) → instance |
| 9 | Migration namespacing | Numbered ranges: project `0001-0999`, instance `1000+` |
| 10 | Chunk format | Defer; preserve current strategy through the rewrite |

All three target paths share the `~\Obsidian\` parent — single backup target, no cross-drive sync coordination, but **all three must be added to Obsidian Sync's excluded folders list** before any of them get used at scale (Sync would otherwise try to push the live DB and embeddings, which is bad).

---

## Effort distribution (revised)

Approximate proportions of total work, with Gemini routing:

- **[H] Haiku** — ~10% (mechanical moves, file ops, simple updates)
- **[S] Sonnet** — ~25% (validation passes, integration, golden-diff review, contract-sensitive code)
- **[O] Opus** — ~20% (architectural decisions, framework design, PII audits, triage)
- **[G→*] Gemini** — ~35% (most code generation: adapter ports, scaffolding, tests, docs)
- **[R] Rob** — ~10% (decisions, repo creation, real-corpus testing, physical moves, license)

Compared to the prior all-Claude plan, this redirects roughly two-thirds of what was Sonnet-implementation work to Gemini, with Sonnet retained for validation, integration, and contract-sensitive surfaces.

---

## Phase ordering at a glance

```
Phase 0  Pre-flight                  ─── R + Opus heavy
Phase 1  Project scaffold (in place) ─── Opus design, Gemini implement, Sonnet validate
Phase 2  Reference adapter + loader  ─── Opus design, Gemini implement, Sonnet golden-diff
Phase 3  Instance scaffold + PII     ─── Opus + R for extraction, Gemini for wiring
Phase 4  Bulk adapter port           ─── Gemini-dominant (longest phase)
Phase 5  Query layer cutover         ─── Mixed; contract preservation kept in Sonnet
Phase 6  Embed pipeline hardening    ─── Opus design, Gemini implement
Phase 7  Physical split              ─── R-dominant (Rob does the move)
Phase 8  Polish & publish            ─── Gemini-dominant (lots of docs)
```

No phase begins until the prior phase exits cleanly. Phase 0 cannot begin until current in-flight ingest work stabilizes.
