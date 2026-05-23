# Port `google_contacts` adapter to plugin

Port `phdb.adapters.google_contacts` → `phdb.plugins.google_contacts/`.
Source: Google Contacts Takeout — CSV / vCard contact export with
name, email, phone, address.

## Manifest declarations

- `emits = ["Person"]`
- `entity_refs = []`
- `formats_used = ["vcard"]`
- `records_required = ["Contact"]`
- `facets_projected = ["Person"]`

## Initial scope

- Port CSV / vCard parser + per-row insert into the `persons` typed
  table.
- Each contact (with name + email + phone) projects to the Person
  facet with the contact metadata as payload — highest-fidelity
  input for identity coalescence after facebook_connections.

## Out of scope

- Entity-factoring `persons` (deferred — Person remains action-shaped
  in Phase 7; entity-factor pass lands later).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_google_contacts_adapter.py` passes verbatim.

## Context

google_contacts is the highest-confidence Person input — every row
has multiple identifiers (email + phone + name) tied to a single
person. Phase 8's coalescence rules will use Google Contacts rows
as the "canonical anchor" against which fuzzy matches from email
senders, Discord handles, etc. are resolved.
