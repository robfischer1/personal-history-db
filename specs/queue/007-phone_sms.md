# Port `phone_sms` adapter to plugin

Port `phdb.adapters.phone_sms` → `phdb.plugins.phone_sms/`. Source:
Android phone SMS database (SQLite from a Titanium Backup or similar
extract). Typical input: `mmssms.db`-style files.

## Manifest declarations

- `emits = ["Message"]`
- `entity_refs = []`
- `formats_used = ["phone_sms_sqlite"]`
- `records_required = ["ChatMessage", "Attachment", "Recipient"]`
- `facets_projected = ["Person", "Time", "Thread"]`

## Initial scope

- Port the SQLite parser invocation + per-thread iteration.
- Each remote phone number projects to the Person facet.
- Per-conversation thread ID projects to the Thread facet.
- MMS attachments preserved via the attachments sidecar.

## Out of scope

- Cross-source dedup against sms_xml (which is the older XML-export
  variant of the same data) — separate cross-source coalescence brief.

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_phone_sms_adapter.py` passes verbatim.
- Multi-attachment MMS preserves attachment ordering.

## Context

phone_sms + sms_xml + google_voice + imessage all emit `Message` rows;
the Thread facet's cross-source coalescence (Phase 8) reconciles
duplicates across these sources.
