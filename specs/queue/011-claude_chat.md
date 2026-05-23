# Port `claude_chat` adapter to plugin

Port `phdb.adapters.claude_chat` → `phdb.plugins.claude_chat/`. Source:
Claude.ai web export JSON — chat history. Typical input: the JSON
export bundle from Claude.ai settings.

## Manifest declarations

- `emits = ["Conversation"]`
- `entity_refs = []`
- `formats_used = ["claude_chat_json"]`
- `records_required = ["AISessionMessage"]`
- `facets_projected = ["Time", "Topic"]`

## Initial scope

- Port the JSON parser + per-turn insert into `conversations_messages`.
- Preserve `model`, `role`, `parent_uuid`, `tool_name`, `tool_use_id`,
  `payload` columns.
- Project session-start timestamps to the Time facet.

## Out of scope

- Conversation-thread coalescence across claude_chat + claude_code +
  gemini sources (Phase 8 cross-source dedup).
- Tool-use payload parsing beyond storing the raw JSON.

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_claude_chat_adapter.py` passes verbatim.

## Context

claude_chat + claude_code + (deferred) gemini_web all emit
`Conversation` rows. The Topic facet will eventually cluster these by
embedding similarity — Phase 8+.
