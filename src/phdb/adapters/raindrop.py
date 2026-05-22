"""Raindrop.io bookmarks adapter — ingests Raindrop CSV + scattered older backups.

Writes to the `bookmarks` table (not messages). Custom run() override.
Supports multiple format parsers: raindrop_csv, netscape_html, session_buddy_csv,
session_buddy_json, safari_db.

URL normalization: lowercase scheme+host, strip default ports, drop fragment,
drop tracking params (utm_*, fbclid, etc.), http->https collapse.
Dedup: UNIQUE(normalized_url, instrument) with ON CONFLICT incrementing
appearance_count and extending [first_seen, last_seen] window.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.formats.raindrop import parse
from phdb.formats.url import extract_domain, is_junk
from phdb.log import get_logger
from phdb.records import BookmarkEvent

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.adapters.raindrop")


# ---------------------------------------------------------------------------
# WebPage entity upsert
# ---------------------------------------------------------------------------

def upsert_web_page(
    conn: sqlite3.Connection,
    url: str,
    normalized_url: str,
    *,
    title: str | None = None,
    excerpt: str | None = None,
    cover_url: str | None = None,
    sighted: str | None = None,
    source_file_id: int | None = None,
) -> int:
    """Create or update a WebPage URL-entity row. Returns web_page.id."""
    domain = extract_domain(normalized_url)
    cur = conn.execute(
        """INSERT INTO web_pages
           (url, normalized_url, title, excerpt, cover_url, domain,
            first_seen, last_seen, source_file_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(normalized_url) DO UPDATE SET
               title    = COALESCE(NULLIF(excluded.title, ''),    web_pages.title),
               excerpt  = COALESCE(NULLIF(excluded.excerpt, ''),  web_pages.excerpt),
               cover_url= COALESCE(NULLIF(excluded.cover_url, ''),web_pages.cover_url),
               first_seen = CASE
                   WHEN excluded.first_seen IS NULL THEN web_pages.first_seen
                   WHEN web_pages.first_seen IS NULL THEN excluded.first_seen
                   WHEN excluded.first_seen < web_pages.first_seen THEN excluded.first_seen
                   ELSE web_pages.first_seen
               END,
               last_seen = CASE
                   WHEN excluded.last_seen IS NULL THEN web_pages.last_seen
                   WHEN web_pages.last_seen IS NULL THEN excluded.last_seen
                   WHEN excluded.last_seen > web_pages.last_seen THEN excluded.last_seen
                   ELSE web_pages.last_seen
               END,
               source_file_id = COALESCE(excluded.source_file_id, web_pages.source_file_id)
           RETURNING id""",
        (url, normalized_url, title, excerpt, cover_url, domain,
         sighted, sighted, source_file_id),
    )
    return int(cur.fetchone()[0])


# ---------------------------------------------------------------------------
# Bookmark upsert
# ---------------------------------------------------------------------------

def hash_canonical(event: BookmarkEvent) -> str:
    canonical = json.dumps({
        "url": event.url,
        "instrument": event.instrument,
        "title": event.title or "",
        "folder": event.folder or "",
        "tags": sorted(event.tags),
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upsert_bookmark(
    conn: sqlite3.Connection,
    source_file_id: int,
    event: BookmarkEvent,
    *,
    web_page_id: int,
) -> int:
    """Insert or increment-on-conflict a bookmark row."""
    url = event.url
    norm = event.normalized_url
    instrument = event.instrument
    junk = is_junk(url)
    rh = hash_canonical(event)
    tags_json = json.dumps(list(event.tags))
    sighted = event.date_added or None
    raindrop_created = sighted if instrument == "raindrop" else None

    cur = conn.execute(
        """INSERT INTO bookmarks
           (schema_type, instrument, raindrop_id, url, normalized_url,
            title, note, excerpt, cover_url, folder, tags, favorite, highlights,
            first_seen_in_instrument, last_seen_in_instrument, raindrop_created,
            appearance_count, excluded, excluded_reason, source_file_id, raw_hash,
            web_page_id)
           VALUES ('BookmarkAction', ?, ?, ?, ?,
                   ?, ?, ?, ?, ?, ?, ?, ?,
                   ?, ?, ?,
                   1, ?, ?, ?, ?,
                   ?)
           ON CONFLICT(normalized_url, instrument) DO UPDATE SET
               raindrop_id  = COALESCE(excluded.raindrop_id, bookmarks.raindrop_id),
               title        = COALESCE(NULLIF(excluded.title,''),    bookmarks.title),
               note         = COALESCE(NULLIF(excluded.note,''),     bookmarks.note),
               excerpt      = COALESCE(NULLIF(excluded.excerpt,''),  bookmarks.excerpt),
               cover_url    = COALESCE(NULLIF(excluded.cover_url,''),bookmarks.cover_url),
               folder       = COALESCE(NULLIF(excluded.folder,''),   bookmarks.folder),
               tags         = excluded.tags,
               favorite     = excluded.favorite,
               highlights   = COALESCE(NULLIF(excluded.highlights,''), bookmarks.highlights),
               first_seen_in_instrument = CASE
                   WHEN excluded.first_seen_in_instrument IS NULL THEN bookmarks.first_seen_in_instrument
                   WHEN bookmarks.first_seen_in_instrument IS NULL THEN excluded.first_seen_in_instrument
                   WHEN excluded.first_seen_in_instrument < bookmarks.first_seen_in_instrument THEN excluded.first_seen_in_instrument
                   ELSE bookmarks.first_seen_in_instrument
               END,
               last_seen_in_instrument = CASE
                   WHEN excluded.last_seen_in_instrument IS NULL THEN bookmarks.last_seen_in_instrument
                   WHEN bookmarks.last_seen_in_instrument IS NULL THEN excluded.last_seen_in_instrument
                   WHEN excluded.last_seen_in_instrument > bookmarks.last_seen_in_instrument THEN excluded.last_seen_in_instrument
                   ELSE bookmarks.last_seen_in_instrument
               END,
               raindrop_created = COALESCE(excluded.raindrop_created, bookmarks.raindrop_created),
               appearance_count = bookmarks.appearance_count + 1,
               source_file_id   = excluded.source_file_id,
               web_page_id      = excluded.web_page_id
           RETURNING id""",
        (instrument, event.raindrop_id, url, norm,
         event.title, event.note, event.excerpt,
         event.cover_url, event.folder, tags_json,
         1 if event.favorite else 0, event.highlights,
         sighted, sighted, raindrop_created,
         1 if junk else 0, junk, source_file_id, rh,
         web_page_id),
    )
    return int(cur.fetchone()[0])


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class RaindropAdapter(Adapter):
    """Ingest Raindrop.io bookmarks and scattered older bookmark backups."""

    name = "raindrop"
    source_kind = "raindrop"
    file_kind = "csv"
    schema_type = "BookmarkAction"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        raise NotImplementedError("Use run() directly — writes to bookmarks table")

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestReport:
        report = IngestReport(
            adapter_name=self.name,
            source_path=str(source_path),
            source_file_id=0,
        )

        source_file_id = self._register_source(conn, source_path)
        report.source_file_id = source_file_id

        batch_count = 0

        for event in parse(source_path):
            report.rows_yielded += 1

            sighted = event.date_added or None
            wp_id = upsert_web_page(
                conn, event.url, event.normalized_url,
                title=event.title, excerpt=event.excerpt,
                cover_url=event.cover_url, sighted=sighted,
                source_file_id=source_file_id,
            )
            upsert_bookmark(conn, source_file_id, event, web_page_id=wp_id)
            report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.batch_size:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.name, report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report
