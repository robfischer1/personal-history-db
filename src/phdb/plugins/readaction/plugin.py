"""ReadActionPlugin — stub plugin for Pocket / Instapaper reading-list sources.

Phase 7 follow-on (WPEF inherited brief 102). The ReadAction schema +
read_actions table (migration 0027) are ready; this plugin is the
seam waiting for an actual Pocket or Instapaper format parser.

Third consumer of the entity-FK pattern after raindrop (BookmarkAction
to web_pages) and apple_dbs (BrowseAction to web_pages): when a
parser lands, ``parse()`` will yield records whose ingest path mirrors
``RaindropPlugin.ingest_row`` — upsert the WebPage entity then insert
a ReadAction row with the entity FK.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.plugin.manifest import PluginManifest


class ReadActionPlugin(PhdbSourcePlugin):
    """Stub plugin for Pocket / Instapaper / future read-it-later sources.

    The ReadAction schema + DDL ship in canonical.py + migration 0027;
    this plugin's ``parse()`` raises NotImplementedError until a
    Pocket or Instapaper format parser is wired into
    ``phdb.formats``.
    """

    SOURCE_KIND = "readaction"

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """No format parser yet — discovery is a no-op."""
        return iter(())

    def parse(self, path: Path) -> Iterator[Any]:
        """Raise — Pocket / Instapaper parser is the pending deliverable."""
        raise NotImplementedError(
            "No Pocket/Instapaper format parser yet — schema is ready, "
            "plugin awaits a parser implementation."
        )

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: Any,
        *,
        source_file_id: int | None = None,
    ) -> int:
        """Raise — no records can be produced until ``parse()`` works."""
        raise NotImplementedError(
            "ReadActionPlugin.ingest_row is unreachable until a Pocket / "
            "Instapaper parser lands and parse() yields records."
        )

    def register_cli(self, parser: Any) -> None:
        """No-op — no CLI commands until the parser ships."""
        return None

    def register_tools(self, server: Any) -> None:
        """No-op — no MCP tools until the parser ships."""
        return None


__all__ = ["ReadActionPlugin"]
