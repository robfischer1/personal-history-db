# Port `google_drive` adapter to plugin

Port `phdb.adapters.google_drive` → `phdb.plugins.google_drive/`.
Source: Google Drive Takeout ZIP — file extraction + body text via
document extractors.

## Manifest declarations

- `emits = ["DigitalDocument"]`
- `entity_refs = []`
- `formats_used = ["google_drive_zip"]`
- `records_required = ["DigitalDocument"]`
- `facets_projected = ["Time"]`

## Initial scope

- Port the ZIP scanner + per-file body extraction.
- Insert into the `documents` typed table.
- Preserve the EXTRACTORS dispatch (PDF, DOCX, XLSX, etc.) from
  `phdb.formats.document_extract`.

## Out of scope

- Folder-as-bucket hierarchy reconstruction beyond the existing logic.

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_google_drive_adapter.py` passes verbatim.

## Context

Pair with `028-onedrive` — both are file-extraction adapters sharing
the document_extract format module. Confirms `formats_used = [...]`
declarations make cross-source dependencies explicit.
