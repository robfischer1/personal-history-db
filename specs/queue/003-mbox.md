# Port `mbox` adapter to plugin

Port `phdb.adapters.mbox` → `phdb.plugins.mbox/`. Source: RFC 5322 mbox
files (one giant flat file holding many emails). Typical input:
Thunderbird / Apple Mail / Takeout mailbox archives. Largest emit volume
of any source: 68K EmailMessage rows in the live DB.

## Manifest declarations

- `emits = ["EmailMessage"]`
- `entity_refs = []`
- `formats_used = ["mbox"]`
- `records_required = ["EmailMessage", "Attachment", "Recipient"]`
- `facets_projected = ["Person", "Time", "Thread"]`

## Initial scope

- Port the mbox parser invocation + per-row insert logic into the
  plugin.
- Preserve byte-offset capture (`source_byte_offset` +
  `source_byte_length`) — mbox is the canonical use case for those
  columns.
- Recipients (`To:` / `Cc:` / `Bcc:` headers) project to the Person
  facet; gmail_thread_id projects to the Thread facet; date_sent
  projects to the Time facet.
- Attachments sidecar: this plugin owns the `attachments` table (or
  pulls from the existing one if shared). Declare `sidecars =
  ["attachments"]` in the manifest if owned.

## Out of scope

- Deduplicating against the `gmail` adapter (separate brief; gmail
  shouldn't exist as a separate adapter — verify and consolidate or
  declare cross-source dedup logic).
- Body-text MIME-multipart edge cases beyond what the existing parser
  handles.

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_mbox_adapter.py` passes verbatim.
- Idempotent rerun on the same mbox produces zero new rows.
- Person facet emissions: every distinct `sender_address` produces
  one Person emission.

## Context

EmailMessage is the highest-volume typed table; this brief's golden-
diff bar is the strictest. The Person facet projection here is the
first source plugin to feed `phdb.facets.people` in production —
exercises the EmissionBus end-to-end.
