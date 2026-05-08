---
created: 2026-05-07
updated: 2026-05-08
status: phase-5-done
type: project-state
related:
  - "[[AI_SESSIONS_PLAN]]"
---

# AI Sessions — Adapter Port Log

Per-adapter ingest status for the AI sessions plan. Updated as each phase completes.

## Status legend

- `pending` — not started
- `in-progress` — active
- `done` — adapter complete, tests passing, production-applied
- `blocked` — waiting on upstream dependency

## Adapter ports

| # | Adapter | Phase | Status | Notes |
|--:|---|---|---|---|
| 1 | `claude-code` | Phase 2 | **done** | 29 unit tests + end-to-end; 138/138 rows on test session; 4 kind values (message/tool_use/tool_result/sidechain) |
| 2 | `gemini-web` | Phase 3 | **done** | 35 unit tests + e2e; 575 rows across 8 landmark files; state-machine block parser handles unclosed EOF blocks |
| 3 | `gemini-scribe` | Phase 4 | **done** | 17 unit tests + e2e; 146 rows across 5 Scribe files; accessed_files capped at 100 with total count |

## Schema migration

| Migration | Phase | Status | Notes |
|---|---|---|---|
| `0006_ai_sessions.sql` | Phase 1 | **done** | Adds `messages.kind`, `messages.role`, `messages.parent_uuid`, `messages.tool_name`, `messages.tool_use_id`, `messages.model`, `messages.payload`, `threads.metadata`, `threads.cwd`; 5 partial indexes; 16 migration tests passing |

## Phase progress

| Phase | Description | Status | Exit criteria met? |
|---|---|---|---|
| Phase 0 | Pre-flight & decisions | **done** | Yes — all 6 decisions resolved; MyActivity.html verified (440 cells); Gemini CLI auth confirmed; INVENTORY, BOUNDARY_TABLE, BOUNDARY_DERIVER_AGENT complete; architecture deviation documented |
| Phase 1 | Schema migration | **done** | 0006_ai_sessions.sql applied; AdapterRow + INSERT SQL updated; 616 tests passing |
| Phase 2 | claude-code adapter | **done** | claude_code.py; 29 unit + e2e tests; 138/138 rows on 47392296 session |
| Phase 3 | gemini-web adapter | **done** | gemini_web.py; 35 unit + e2e tests; 575 rows across 8 landmark files |
| Phase 4 | gemini-scribe adapter | **done** | gemini_scribe.py; 17 unit + e2e tests; 146 rows across 5 Scribe files |
| Phase 5 | MCP refresh + landmark retirement | **done** | is_bulk policy enforced (kind≠message → is_bulk=1); include_meta filter in search(); kind field in results; 677 tests passing; governance docs updated; landmark file moves pending Rob bless |

## Golden-diff results (to be filled per phase)

| Adapter | Source file | Expected rows | Actual rows | Delta % | Pass? |
| --- | --- | --- | --- | --- | --- |
| claude-code | 47392296-b7f0-46ad-acd0-42775f3463ff.jsonl | 138 | 138 | 0% | ✓ |

## Rob actions required before next phase

| Action | Blocking | Status |
| --- | --- | --- |
| Confirm Phase 0 decisions (6 decisions) | Phase 1 | ✓ done 2026-05-08 |
| Verify MyActivity.html intact on <host> | Phase 3a | ✓ done 2026-05-08 (2.5MB, 440 cells) |
| Confirm Gemini CLI auth on <host> | Phase 2/3/4 | ✓ done 2026-05-08 (v0.41.2) |
| Spot-check Branch 1 / Trunk timestamp anomaly | Phase 3b | ✓ done 2026-05-08 — explanation accepted (Trunk re-entry timestamp; see BOUNDARY_TABLE.md) |
| Spot-check boundary table before landmark ingest | Phase 3b | ✓ done 2026-05-08 — boundary table verified |
| Bless landmark retirement — move Timelines/AI Sessions/ to Archives/ | Phase 5 | ✓ done 2026-05-08 — 13 files → Archives/Timelines-AI-Sessions-2026-05-08/ |
