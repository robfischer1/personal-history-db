"""phdb.plugins.chrome_history — Chrome browser history ingester.

Consumes Google Takeout Chrome History JSON exports and inserts
page-visit rows into the ``browser_history`` table (migration 0035).
"""

from __future__ import annotations

from phdb.plugins.chrome_history.plugin import ChromeHistoryPlugin

__all__ = ["ChromeHistoryPlugin"]
