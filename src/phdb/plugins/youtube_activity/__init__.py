"""phdb.plugins.youtube_activity — Google Takeout YouTube activity ingester.

Canonical-emitting plugin. Routes parsed records to ``watch_actions`` /
``search_actions`` / ``follow_actions`` with ``web_pages`` FK plumbing
via ``upsert_web_page``. ``follow_actions`` was introduced in migration
0040; the pre-existing ``youtube_activity`` table (migration 0037) was
dropped during the canonical refactor.
"""

from __future__ import annotations

from phdb.plugins.youtube_activity.plugin import YouTubeActivityPlugin

__all__ = ["YouTubeActivityPlugin"]
