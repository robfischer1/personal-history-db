# Port `chat_logs` adapter to plugin

Port `phdb.adapters.chat_logs` → `phdb.plugins.chat_logs/`. Source:
Plain-text chat-log files (older IRC / AIM / forum format). Typical
input: directory of `.txt` files, one per conversation or day.

## Manifest declarations

- `emits = ["Message"]`
- `entity_refs = []`
- `formats_used = ["chat_logs_text"]`
- `records_required = ["ChatMessage"]`
- `facets_projected = ["Person", "Time", "Thread"]`

## Initial scope

- Port the line-based parser + heuristics for handle / timestamp
  extraction.
- Each handle projects to Person; each file projects to one Thread.

## Out of scope

- Per-platform parser variants beyond the existing supported formats.

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_chat_logs_adapter.py` passes verbatim.

## Context

chat_logs covers the historical chat data that predates structured
exports — EQII chat from 2003-2020, AIM, IRC. The Person + Thread
emissions here are the canonical input for back-filling the
identity-coalescence rules engine in Phase 8.
