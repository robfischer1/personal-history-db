"""phdb.plugins.spotify — Spotify Extended Streaming History ingester.

Phase 7 port of the phdb Plugin Architecture plan. Replaces the legacy
``phdb.adapters.spotify`` module deleted in the same commit per Phase 0
Q14 (no shim). Reuses ``listen_actions`` (migration 0021); no schema
changes.
"""

from __future__ import annotations

from phdb.plugins.spotify.plugin import SpotifyPlugin

__all__ = ["SpotifyPlugin"]
