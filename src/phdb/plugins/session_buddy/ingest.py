"""session_buddy ingest helpers — add upsert helpers here when the plugin needs them.

Mirror ``phdb.formats.bookmark_upserts`` (raindrop / apple_dbs) or
``phdb.formats.email_upserts`` (gmail / mbox plugins) for the
COALESCE last-write-wins pattern used across the existing plugins.
"""

from __future__ import annotations
