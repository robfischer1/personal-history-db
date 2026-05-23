"""phdb.plugins.apple_notes_full — Apple Notes full-body ingester.

Phase 7 brief 023 port of the phdb Plugin Architecture plan. Replaces
the legacy ``phdb.adapters.apple_notes_full`` module deleted in the
same commit per Phase 0 Q14 (no shim). Writes to the existing
``documents`` typed table (migration 0008); no schema changes.

UPDATE-oriented: pairs with ``apple_dbs`` — when a row already exists
keyed by the synthetic ``notes:{Z_PK}`` id, the body_text gets
upgraded from the truncated snippet to the full proto-decoded text.
Otherwise the plugin inserts a fresh DigitalDocument row.

Proto extraction path: gunzip -> field 2 (Document) -> field 3 (Note)
-> field 2 (NoteText). The path is a tested-and-fragile invariant
(per ``feedback_apple_notes_proto_path``) and must not be
"simplified" to 2->2->1.
"""

from __future__ import annotations

from phdb.plugins.apple_notes_full.plugin import AppleNotesFullPlugin

__all__ = ["AppleNotesFullPlugin"]
