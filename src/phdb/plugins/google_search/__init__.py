"""phdb.plugins.google_search — Google Takeout Search History HTML ingester.

Parses Google Takeout MyActivity HTML exports for Search into the
``search_history`` table (migration 0036).
"""

from __future__ import annotations

from phdb.plugins.google_search.plugin import GoogleSearchPlugin

__all__ = ["GoogleSearchPlugin"]
