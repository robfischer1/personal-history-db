"""Discord plugin — ports the legacy Discord adapter to the new architecture.

This plugin ingests Discord data-export ZIP files, emitting Message rows
and projecting into Person, Time, and Thread facets.
"""

from __future__ import annotations

from phdb.plugins.discord.plugin import DiscordPlugin

__all__ = ["DiscordPlugin"]
