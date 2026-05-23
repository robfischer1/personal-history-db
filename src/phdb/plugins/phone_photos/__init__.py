"""phdb.plugins.phone_photos — Phone-camera directory walker.

Phase 7 brief 031 port of the phdb Plugin Architecture plan. Replaces
the legacy ``phdb.adapters.phone_photos`` module deleted in the same
commit per Phase 0 Q14 (no shim). Reuses the ``photographs`` typed
table introduced in migration 0016; no schema changes.

Sibling to the digikam adapter precedent — both ingest photograph
metadata, but phone_photos walks a camera-synced directory tree while
digikam queries the DigiKam SQLite store.
"""

from __future__ import annotations

from phdb.plugins.phone_photos.plugin import PhonePhotosPlugin

__all__ = ["PhonePhotosPlugin"]
