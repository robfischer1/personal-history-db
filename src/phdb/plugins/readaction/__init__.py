"""phdb.plugins.readaction — stub plugin for Pocket / Instapaper reading-list sources.

WPEF inherited follow-on (brief 102). Schema + DDL are wired; the
plugin's ``parse()`` raises NotImplementedError until a Pocket or
Instapaper format parser is added to ``phdb.formats``.
"""

from __future__ import annotations

from phdb.plugins.readaction.plugin import ReadActionPlugin

__all__ = ["ReadActionPlugin"]
