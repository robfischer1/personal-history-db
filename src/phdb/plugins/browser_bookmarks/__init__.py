"""phdb.plugins.browser_bookmarks — Netscape HTML and Chrome JSON bookmark ingester.

Writes to the shared ``bookmarks`` table with ``instrument='chrome'``,
``'firefox'``, ``'edge'``, etc. (auto-detected from filename).  Dedup key
is ``(web_page_id, instrument)`` — coexists cleanly with raindrop bookmarks.
"""

from __future__ import annotations

from phdb.plugins.browser_bookmarks.plugin import BrowserBookmarksPlugin

__all__ = ["BrowserBookmarksPlugin"]
