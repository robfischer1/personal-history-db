---
created: 2026-05-06
status: scaffold
type: project-state
related:
  - "[[REWRITE_PLAN]]"
  - "[[../../System/Prompts/gemini-prompt-scripter]]"
---

# Gemini Prompts Archive

Captured Gemini CLI prompts and outputs for reproducibility. Per the rewrite plan's hygiene rule:

> Capture the prompt with the result. Save Gemini prompts alongside outputs (e.g., `docs/gemini-prompts/`) for reproducibility. Helps when an adapter needs a re-port a year later.

## Naming convention

`<phase>-<adapter_name>-<timestamp>.md` — the prompt itself
`<phase>-<adapter_name>-<timestamp>.cmd.txt` — the exact `gemini` invocation that was run
`<phase>-<adapter_name>-<timestamp>.out.py` — the captured output (or `.json`, `.md`, etc. depending on target)

Timestamp format: `YYYYMMDD-HHMMSS` UTC.

Example:
- `phase4-imessage-20260506-141500.md`
- `phase4-imessage-20260506-141500.cmd.txt`
- `phase4-imessage-20260506-141500.out.py`

## What goes here

- Every prompt that gets sent to Gemini CLI
- The invocation command exactly as run
- The output exactly as received (don't post-edit before saving)
- A brief outcome marker at the bottom of the prompt file: `outcome: clean | iterating | superseded by <ref>`

## What does NOT go here

- Conversational queries to Gemini (web app exploration)
- One-off prompts that didn't produce shipping artifacts
- Anything containing real PII (Gemini prompts target the project tier; project tier should be PII-clean by construction)

## Pinning Gemini model versions

Each prompt file's frontmatter includes `gemini_model: <version>`. Output style and quality drift across model revisions; pinning the version makes the run reproducible.

## When you're re-porting a year later

1. Find the relevant prompt file
2. Verify the input files (legacy adapter, base class, fixtures) still exist at the paths referenced
3. Run the saved `.cmd.txt` exactly
4. Diff the new output against the saved `.out.py`
5. Investigate any divergence as a Gemini-model-drift signal

## Currently empty

This directory is a scaffold. Phase 4 onward populates it.
