# Port `phone_photos` adapter to plugin

Port `phdb.adapters.phone_photos` → `phdb.plugins.phone_photos/`.
Source: Phone-camera directory walk — EXIF + GPS metadata extraction
from photos in a synced phone-photos folder. Sibling to the digikam
adapter precedent (referenced in `project_digikam_adapter`).

## Manifest declarations

- `emits = ["Photograph"]`
- `entity_refs = []`
- `formats_used = ["phone_photos_dir"]`
- `records_required = ["Photograph"]`
- `facets_projected = ["Place", "Time", "Person"]`

## Initial scope

- Port the directory walker + EXIF extraction.
- Project EXIF GPS coordinates to the Place facet; captured_at to
  Time; depicted persons (when FaceID metadata is available) to
  Person.

## Out of scope

- CLIP / vision-model embeddings (deferred per Phase 0 #7).
- Cross-source dedup against the digikam adapter (separate brief if
  digikam stays as a separate plugin; consolidate if not).

## Success criteria

- Plugin discovers + describes cleanly.
- `tests/test_phone_photos_adapter.py` passes verbatim.

## Context

The Place + Person facet emissions from phone_photos exercise multi-
facet projection from a single source plugin. Phase 8's Place
coalescence will reconcile phone_photos GPS with google_timeline
visited-place data.
