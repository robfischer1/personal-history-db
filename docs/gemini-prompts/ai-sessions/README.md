---
created: 2026-05-07
status: scaffold
type: project-state
related:
  - "[[AI_SESSIONS_PLAN]]"
  - "[[../../README]]"
---

# Gemini Prompts — AI Sessions

Gemini CLI prompts for `claude-code`, `gemini-web`, and `gemini-scribe` adapter scaffolding.

Inherits the naming convention from the parent `gemini-prompts/` directory:
`<phase>-<adapter_name>-<YYYYMMDD-HHMMSS>.{md|cmd.txt|out.py}`

## Planned prompts (by phase)

| Phase | Adapter | Purpose | Status |
|---|---|---|---|
| Phase 1 | schema | Generate `0XXX_ai_sessions.sql` from Opus schema design | pending |
| Phase 2 | claude-code | Scaffold adapter class from spec | pending |
| Phase 2 | claude-code | Generate synthetic JSONL fixtures (all 5 kind values) | pending |
| Phase 3 | gemini-web | Scaffold adapter class from spec | pending |
| Phase 3 | gemini-web | Generate synthetic mini-MyActivity.html + synthetic landmarks | pending |
| Phase 4 | gemini-scribe | Scaffold adapter class from spec | pending |

## Hygiene reminders

- Never paste real conversation content into Gemini prompts (PII + session content).
- Fixtures for test suites must be synthetic only; real-corpus validation is Sonnet/Opus on <host>.
- Each prompt file must include `gemini_model: <version>` in frontmatter.
- One `.cmd.txt` per prompt capturing the exact `gemini` invocation.
