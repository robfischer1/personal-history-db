"""BrowserBookmarksPlugin — Netscape HTML and Chrome JSON bookmark ingester.

Writes to the shared ``bookmarks`` table (same as raindrop) with
``instrument='chrome'`` or ``instrument='firefox'`` (auto-detected from
file format, overridable at call time).

Supported formats:
  - Netscape Bookmark File (HTML): standard ``<!DOCTYPE NETSCAPE-Bookmark-file-1>``
    exported by Chrome, Firefox, Edge, and most browsers.  Parsed with
    stdlib ``html.parser`` — no third-party deps.
  - Chrome JSON: ``Bookmarks`` file from a Chrome profile directory.  Contains
    a ``roots`` tree with ``bookmark_bar``, ``other``, and ``synced`` subtrees.
    Timestamps are WebKit microseconds-since-1601-01-01.

Both formats produce ``BookmarkEvent`` records consumed by the shared
``phdb.formats.bookmark_upserts`` upsert layer, so entity FK, triple
emission, and dedup are identical to the raindrop plugin.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.plugin.summary import IngestSummary
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.bookmark_upserts import (
    emit_bookmark_triples,
    upsert_bookmark,
    upsert_web_page,
)
from phdb.log import get_logger
from phdb.plugins.browser_bookmarks.ingest import parse_chrome_json, parse_netscape_html
from phdb.records import BookmarkEvent

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.browser_bookmarks")

# File-kind constants
_FILE_KIND_HTML = "html"
_FILE_KIND_JSON = "json"


def _detect_format(path: Path) -> str | None:
    """Return ``'html'`` or ``'json'`` based on file content sniff; None if unrecognised."""
    suffix = path.suffix.lower()
    if suffix in {".html", ".htm"}:
        return _FILE_KIND_HTML
    if suffix == ".json":
        return _FILE_KIND_JSON
    # Fallback: sniff first 128 bytes
    try:
        header = path.read_bytes()[:128].decode("utf-8", errors="replace").lower()
    except OSError:
        return None
    if "netscape-bookmark-file" in header:
        return _FILE_KIND_HTML
    if '"roots"' in header or '"bookmark_bar"' in header:
        return _FILE_KIND_JSON
    return None


class BrowserBookmarksPlugin(PhdbSourcePlugin):
    """Browser bookmark exports — Netscape HTML and Chrome JSON formats.

    Writes to the shared ``bookmarks`` table with a browser-specific
    ``instrument`` value (``'chrome'``, ``'firefox'``, etc.).  The dedup
    key ``(web_page_id, instrument)`` ensures browser bookmarks coexist
    with Raindrop bookmarks for the same URLs without conflict.
    """

    SOURCE_KIND = "browser_bookmarks"
    FILE_KIND = "html"  # default; json also supported
    BATCH_SIZE = 500

    def __init__(self, manifest: PluginManifest | None = None) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk *root*; yield ``(path, source_kind)`` for every recognisable file.

        Accepts:
          - ``*.html`` / ``*.htm`` — Netscape Bookmark File format
          - ``Bookmarks`` (no extension) or ``*.json`` — Chrome JSON format
        """
        if root.is_file():
            if _detect_format(root) is not None:
                yield root, self.SOURCE_KIND
            return
        for path in sorted(root.rglob("*")):
            if not path.is_file():
                continue
            fmt = _detect_format(path)
            if fmt is not None:
                yield path, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[BookmarkEvent]:
        """Yield ``BookmarkEvent`` records from one source file."""
        fmt = _detect_format(path)
        if fmt == _FILE_KIND_HTML:
            yield from parse_netscape_html(path)
        elif fmt == _FILE_KIND_JSON:
            yield from parse_chrome_json(path)
        else:
            log.warning("[browser_bookmarks] Unrecognised format: %s — skipped", path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: BookmarkEvent,
        *,
        source_file_id: int | None = None,
    ) -> int:
        """Upsert WebPage entity + BookmarkAction row; emit triples; return bookmark id."""
        sf_id = source_file_id if source_file_id is not None else 0
        wp_id = upsert_web_page(
            conn, record.url, record.normalized_url,
            title=record.title,
            sighted=record.date_added or None,
            source_file_id=sf_id or None,
        )
        bm_id = upsert_bookmark(conn, sf_id, record, web_page_id=wp_id)
        emit_bookmark_triples(
            conn,
            bookmark_id=bm_id, web_page_id=wp_id,
            event=record, provenance="browser_bookmarks-emitted",
        )
        return bm_id

    def register_cli(self, parser: Any) -> None:
        """No plugin-specific CLI subcommands — generic ``phdb plugin ingest`` suffices."""
        return None

    def register_tools(self, server: Any) -> None:
        """No MCP tools for this plugin (Phase 1)."""
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        """End-to-end ingest of one source file.

        Detects format from *source_path*, registers the source_files row,
        then drives the discover → parse → ingest_row loop with batched commits.
        """
        fmt = _detect_format(source_path)
        file_kind = fmt or self.FILE_KIND

        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=file_kind,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            self.ingest_row(conn, record, source_file_id=source_file_id)
            report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[browser_bookmarks] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
