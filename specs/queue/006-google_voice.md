# Port `google_voice` adapter to plugin

Port `phdb.adapters.google_voice` → `phdb.plugins.google_voice/`.
Source: Google Voice Takeout export — call records + SMS conversations.
Typical input: `Takeout/Voice/Calls/<name>-<type>.html` files (each
file is one conversation or call summary).

## Manifest declarations

- `emits = ["Message", "Action"]`
- `entity_refs = []`
- `formats_used = ["google_voice_html"]`
- `records_required = ["ChatMessage", "CallRecord"]`
- `facets_projected = ["Person", "Time", "Thread"]`

## Initial scope

- Port HTML parsing + per-file routing to Message vs Action.
- Calls land as `Action` rows (date_performed, direction); SMS lands
  as `Message` rows (date_sent, body_text).
- Each remote-party phone number projects to the Person facet.
- Per-conversation thread ID projects to the Thread facet.

## Out of scope

- Voicemail transcription cleanup (use existing parser output verbatim).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_google_voice_adapter.py` passes verbatim.
- Calls + SMS for the same number land under the same Person facet
  node (post-Phase-8 coalescence — Phase 7 emits, doesn't yet merge).

## Context

google_voice is the only adapter that emits to TWO typed tables
(actions + chat_messages) from a single source. Validates that a
plugin's `emits = [...]` can declare two @types and route per-row at
ingest time.
