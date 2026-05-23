# Port `phone_calls_xml` adapter to plugin

Port `phdb.adapters.phone_calls_xml` → `phdb.plugins.phone_calls_xml/`.
Source: Call log XML export (the SMS Backup & Restore sibling for
calls). Typical input: `calls-YYYYMMDD.xml` files.

## Manifest declarations

- `emits = ["Action"]`
- `entity_refs = []`
- `formats_used = []`
- `records_required = ["CallRecord"]`
- `facets_projected = ["Person", "Time"]`

## Initial scope

- Port XML parsing + per-row insert into the `actions` typed table
  (schema_type='Action').
- Project each remote phone number to the Person facet.

## Out of scope

- Cross-source dedup against google_voice call records (separate brief).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_phone_calls_xml_adapter.py` passes verbatim.

## Context

Calls land in the generic `actions` table; future entity-factoring
could split them into a `PhoneCall` action schema, but Phase 7 keeps
the current shape.
