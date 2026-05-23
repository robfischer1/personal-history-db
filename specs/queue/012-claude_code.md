# Port `claude_code` adapter to plugin

Port `phdb.adapters.claude_code` → `phdb.plugins.claude_code/`. Source:
Claude Code session JSONL files — one line per turn with tool-use
payloads. Typical input: `.claude/projects/<sanitized-cwd>/<session-id>.jsonl`.

## Manifest declarations

- `emits = ["Conversation"]`
- `entity_refs = []`
- `formats_used = ["claude_code_jsonl"]`
- `records_required = ["AISessionMessage"]`
- `facets_projected = ["Time", "Topic"]`

## Initial scope

- Port the JSONL parser + per-turn insert into `conversations_messages`.
- Preserve session metadata (model, working directory, tool-use IDs).
- Project session-start timestamps to Time; session subject to Topic.

## Out of scope

- Reconstructing project-tree structure across sessions (separate
  analysis brief).
- Embedding tool-use payloads (those land via the embed pipeline once
  the `embeddable_tables` declaration is wired).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_claude_code_adapter.py` passes verbatim.

## Context

Sibling to `011-claude_chat`. Most-active source by row count among
the Conversation-emitting plugins — both feed the Topic facet for
project-context clustering.
