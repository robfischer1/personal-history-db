# Port `sms_xml` adapter to plugin

Port `phdb.adapters.sms_xml` → `phdb.plugins.sms_xml/`. Source: SMS
Backup & Restore XML format — the older Android SMS export shape.
Typical input: `sms-YYYYMMDD.xml` files.

## Manifest declarations

- `emits = ["Message"]`
- `entity_refs = []`
- `formats_used = ["smsbr_xml"]`
- `records_required = ["ChatMessage"]`
- `facets_projected = ["Person", "Time"]`

## Initial scope

- Port the XML scanner + per-row insert.
- Each remote phone number projects to Person.
- Thread facet projection: SMS Backup & Restore lacks per-conversation
  IDs; group by remote number for thread emission.

## Out of scope

- MMS attachments (this format doesn't preserve them — use phone_sms
  for that).
- Cross-source dedup against phone_sms (separate brief).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_sms_xml_adapter.py` passes verbatim.

## Context

Sibling to `007-phone_sms`. The two adapters should share a common
SMS row shape; the difference is parser-level only.
