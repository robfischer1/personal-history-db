"""Shared upsert helpers for WebPage entities and BookmarkAction actions.

Extracted from ``phdb.plugins.raindrop.ingest`` as part of Phase 7 of the
phdb Plugin Architecture plan. These helpers provide the bespoke
COALESCE + temporal-merge logic required for idempotent upserts.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3

from phdb.formats.url import extract_domain, is_junk
from phdb.records import BookmarkEvent


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
    """Create or update a WebPage URL-entity row. Returns ``web_page.id``.

    The COALESCE last-write-wins pattern (NULLIF on empty strings)
    plus the CASE-based first_seen/last_seen merge is bespoke logic
    that the generic ``phdb.schemas.upsert.upsert_entity`` can't
    express directly.
    """
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


def hash_canonical_bookmark(event: BookmarkEvent) -> str:
    """Generate a stable hash for a bookmark event for deduplication."""
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
    """Insert or increment-on-conflict a BookmarkAction row."""
    url = event.url
    norm = event.normalized_url
    instrument = event.instrument
    junk = is_junk(url)
    rh = hash_canonical_bookmark(event)
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
