"""phdb.plugins.consumed_media — Vault Entities/ consumed-media ingester.

Consumed Media Dissolution (Outputs/Plans/Consumed Media Dissolution.md).
Reads 7 Entities/ subdirectories (Books, Games, Movies, Podcasts, TV Series,
YouTube Channels, Twitch Channels) and writes one row per file into the
corresponding typed table (migration 0030). Single multi-type plugin
routing by ``@type`` to the correct table.
"""

from __future__ import annotations

from phdb.plugins.consumed_media.plugin import ConsumedMediaPlugin

__all__ = ["ConsumedMediaPlugin"]
