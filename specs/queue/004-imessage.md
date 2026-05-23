# Port `imessage` adapter to plugin

Port `phdb.adapters.imessage` → `phdb.plugins.imessage/`. Source:
iPhone Messages export (HTML format produced by iMazing /
PhoneView). Distinct from the iMessage rows already pulled by
`apple_dbs` (chat.db SQLite); this one is the HTML-export sibling.

## Manifest declarations

- `emits = ["Message"]`
- `entity_refs = []`
- `formats_used = ["imessage_html"]`
- `records_required = ["ChatMessage", "Attachment", "Recipient"]`
- `facets_projected = ["Person", "Time", "Thread"]`

## Initial scope

- Port HTML parser invocation + insert logic.
- Sidecar: attachments + recipients tables (or shared with other
  chat plugins — coordinate via shared table if so).
- Each phone-number / Apple-ID handle in a conversation projects to
  the Person facet; per-conversation thread ID projects to Thread.

## Out of scope

- Reconciling with apple_dbs Safari/chat.db rows (separate
  cross-source dedup brief).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_imessage_adapter.py` passes verbatim.
- Multi-attachment messages preserve attachment ordering.

## Context

iMessage is the first chat-shaped plugin after the email anchor
(`003-mbox`). Its Person + Thread facet emissions are the
cross-source-coalescence canary: same phone number across iMessage +
phone_sms + facebook_unified should resolve to one Person node by
Phase 8.
