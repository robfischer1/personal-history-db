"""Chrome history ingest helpers — re-exports from plugin module.

The core logic lives in ``plugin.py``. This module provides
a convenience import surface matching the plugin directory convention.
"""

from __future__ import annotations

from phdb.plugins.chrome_history.plugin import (
    BrowserHistoryRecord,
    parse_chrome_history,
)

__all__ = ["BrowserHistoryRecord", "parse_chrome_history"]
