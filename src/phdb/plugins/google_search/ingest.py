"""Google Search ingest helpers — re-exports from plugin module.

Thin re-export layer matching the raindrop plugin pattern.
"""

from __future__ import annotations

from phdb.plugins.google_search.plugin import (
    SearchEntry,
    parse_block,
    parse_search_html,
    parse_timestamp,
)

__all__ = ["SearchEntry", "parse_block", "parse_search_html", "parse_timestamp"]
