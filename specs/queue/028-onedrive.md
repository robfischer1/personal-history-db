# Port `onedrive` adapter to plugin

Port `phdb.adapters.onedrive` → `phdb.plugins.onedrive/`. Source:
OneDrive local sync directory — file extraction + body text. Sibling
to `027-google_drive` for the Microsoft side.

## Manifest declarations

- `emits = ["DigitalDocument"]`
- `entity_refs = []`
- `formats_used = ["onedrive_local"]`
- `records_required = ["DigitalDocument"]`
- `facets_projected = ["Time"]`

## Initial scope

- Port the directory walker + per-file body extraction.
- Reuse the document_extract EXTRACTORS dispatch.
- Respect the OneDrive Reference/ allowlist policy
  (`project_onedrive_reference_allowlist`) for body-extract scope.

## Out of scope

- The reorganization status reflected in
  `project_onedrive_relocation_2026_05_13` — that's a one-shot pass.

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_onedrive_adapter.py` passes verbatim.

## Context

OneDrive is post-2026-05-13 relocated to F: drive; verify the test
fixtures still resolve. Brief flags the allowlist policy explicitly
so the port doesn't accidentally widen body-extract scope.
