# Port `discord` adapter to plugin

Port `phdb.adapters.discord` → `phdb.plugins.discord/`. Source: Discord
data-export JSON files (per-channel `messages.json`). Typical input:
`package/messages/<channel_id>/messages.json` files from a Discord data
export. 49,421 rows from 142 channels in the live DB.

## Manifest declarations

- `emits = ["Message"]`
- `entity_refs = []`
- `formats_used = ["discord_json"]`
- `records_required = ["ChatMessage", "Attachment", "Recipient"]`
- `facets_projected = ["Person", "Time", "Thread"]`

## Initial scope

- Port the JSON parser + per-channel iteration into the plugin.
- Each Discord channel is a Thread; project via facet bus.
- Each Discord user (per `author.id`) is a Person; project via bus.
- Attachments sidecar preserved.

## Out of scope

- Voice-call metadata (Discord export doesn't include reliable signal).
- Reactions detail beyond the existing `Reaction` record type.

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_discord_adapter.py` passes verbatim.
- Channel-level Thread emission count matches distinct channel count.

## Context

Discord is the broadest chat-source by channel count — a good stress
test for the Thread facet's cross-source thread model. After this
ships, the Thread facet has email-thread + discord-channel + imessage-
thread inputs to coalesce.
