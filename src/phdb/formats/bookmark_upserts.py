"""Shared WebPage and Bookmark/BrowseAction upsert logic.

Extracted from ``phdb.plugins.raindrop.ingest`` in Phase 7 to support
cross-plugin entity FKs (Raindrop, Apple DBs, future Chrome/Firefox).

Also exposes ``emit_bookmark_triples`` — the WPEF follow-on (brief 100)
write-time emission of bookmark↔web_page triples (taggedWith / inFolder /
mentions / relatesTo) per the phdb Plugin Architecture plan (phase 7).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3

from phdb.core.graph import add_triple, resolve_node
from phdb.formats.url import extract_domain
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
    """Create or update a WebPage URL-entity row. Returns ``web_page.id``."""
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
    """Insert or increment-on-conflict a BookmarkAction row.

    Post-migration 0028 the bookmarks row holds only action-specific
    columns; URL identity (url / normalized_url / title / excerpt /
    cover_url) lives on the `web_pages` entity joinable via
    `web_page_id`. Dedup key is now `(web_page_id, instrument)`.
    """
    from phdb.formats.url import is_junk

    url = event.url
    instrument = event.instrument
    junk = is_junk(url)
    rh = hash_canonical_bookmark(event)
    tags_json = json.dumps(list(event.tags))
    sighted = event.date_added or None
    raindrop_created = sighted if instrument == "raindrop" else None

    cur = conn.execute(
        """INSERT INTO bookmarks
           (schema_type, instrument, raindrop_id,
            note, folder, tags, favorite, highlights,
            first_seen_in_instrument, last_seen_in_instrument, raindrop_created,
            appearance_count, excluded, excluded_reason, source_file_id, raw_hash,
            web_page_id)
           VALUES ('BookmarkAction', ?, ?,
                   ?, ?, ?, ?, ?,
                   ?, ?, ?,
                   1, ?, ?, ?, ?,
                   ?)
           ON CONFLICT(web_page_id, instrument) DO UPDATE SET
               raindrop_id  = COALESCE(excluded.raindrop_id, bookmarks.raindrop_id),
               note         = COALESCE(NULLIF(excluded.note,''),     bookmarks.note),
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
               source_file_id   = excluded.source_file_id
           RETURNING id""",
        (instrument, event.raindrop_id,
         event.note, event.folder, tags_json,
         1 if event.favorite else 0, event.highlights,
         sighted, sighted, raindrop_created,
         1 if junk else 0, junk, source_file_id, rh,
         web_page_id),
    )
    return int(cur.fetchone()[0])


def upsert_browse_action(
    conn: sqlite3.Connection,
    source_file_id: int,
    web_page_id: int,
    visit_time: str,
    source_device: str,
    raw_hash: str,
) -> int:
    """Insert a BrowseAction row. Dedups on (source_file_id, raw_hash)."""
    cur = conn.execute(
        """INSERT INTO browse_actions
           (schema_type, web_page_id, visit_time, source_device,
            source_file_id, raw_hash)
           VALUES ('BrowseAction', ?, ?, ?, ?, ?)
           ON CONFLICT(source_file_id, raw_hash) DO NOTHING
           RETURNING id""",
        (web_page_id, visit_time, source_device, source_file_id, raw_hash),
    )
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(
        "SELECT id FROM browse_actions WHERE source_file_id = ? AND raw_hash = ?",
        (source_file_id, raw_hash),
    )
    return int(cur.fetchone()[0])


# ============================================================================
# WPEF follow-on — write-time triple emission (brief 100)
# ============================================================================

# Conservative English stopword list for mention-concept extraction.
# Kept small on purpose — this is graph-substrate, not NLP (per the brief).
_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "from",
    "has", "have", "he", "her", "his", "how", "i", "in", "into", "is", "it",
    "its", "of", "on", "or", "our", "out", "she", "so", "than", "that",
    "the", "their", "them", "then", "there", "these", "they", "this", "to",
    "up", "was", "we", "were", "what", "when", "where", "which", "who",
    "why", "will", "with", "you", "your", "yours", "about", "after", "all",
    "also", "any", "been", "being", "can", "could", "did", "do", "does",
    "had", "if", "just", "like", "make", "more", "most", "much", "my",
    "no", "not", "now", "off", "one", "only", "over", "some", "such",
    "via", "vs",
})

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9'\-]{2,}")


def _extract_concepts(text: str | None) -> list[str]:
    """Extract concept tokens from free-form text.

    Basic tokenization: words 3+ chars, stripped of punctuation, deduped,
    stopwords removed. Order preserved (first-occurrence wins). Mention
    extraction is intentionally simple — the brief flags anything beyond
    a basic noun-phrase splitter as out of scope.
    """
    if not text:
        return []
    seen: dict[str, None] = {}
    for match in _WORD_RE.finditer(text):
        token = match.group(0).lower()
        if token in _STOPWORDS:
            continue
        if token not in seen:
            seen[token] = None
    return list(seen.keys())


def _bookmark_node(
    conn: sqlite3.Connection,
    bookmark_id: int,
) -> int:
    """Resolve or create the graph node for a bookmark row."""
    label = f"bookmarks:{bookmark_id}"
    node_id = resolve_node(
        conn, label, kind="bookmark",
        source_table="bookmarks", source_id=bookmark_id,
    )
    assert node_id is not None
    return node_id


def _web_page_node(
    conn: sqlite3.Connection,
    web_page_id: int,
) -> int:
    """Resolve or create the graph node for a web_page row."""
    label = f"web_pages:{web_page_id}"
    node_id = resolve_node(
        conn, label, kind="web_page",
        source_table="web_pages", source_id=web_page_id,
    )
    assert node_id is not None
    return node_id


def emit_bookmark_triples(
    conn: sqlite3.Connection,
    *,
    bookmark_id: int,
    web_page_id: int,
    event: BookmarkEvent,
    provenance: str,
) -> int:
    """Emit the four bookmark-relationship triples at ingest time.

    Predicates emitted (per WPEF follow-on brief 100):

    - ``taggedWith`` — one bookmark → tag triple per tag in ``event.tags``.
    - ``inFolder`` — bookmark → folder-name (if event.folder set).
    - ``mentions`` — web_page → concept token from title+note (one per
      extracted concept; falls back silently if neither field has text).
    - ``relatesTo`` — bookmark → web_page (structural anchor).

    Returns the count of newly-created triples (idempotent re-runs
    return 0 — see ``add_triple`` INSERT OR IGNORE semantics).

    ``provenance`` should be plugin-scoped (e.g. ``raindrop-emitted``,
    ``apple_dbs-emitted``) so back-fill and audit queries can filter
    by emission source.
    """
    bookmark_node_id = _bookmark_node(conn, bookmark_id)
    web_page_node_id = _web_page_node(conn, web_page_id)

    source_ref = event.normalized_url or event.url or None
    created = 0

    # 1. relatesTo — bookmark → web_page (always emitted; one per bookmark)
    result = add_triple(
        conn, bookmark_node_id, "relatesTo", web_page_node_id,
        provenance=provenance, source_ref=source_ref,
    )
    if result["created"]:
        created += 1

    # 2. taggedWith — bookmark → tag (one per tag)
    for tag in event.tags:
        tag = (tag or "").strip()
        if not tag:
            continue
        result = add_triple(
            conn, bookmark_node_id, "taggedWith", tag,
            provenance=provenance, source_ref=source_ref,
            object_kind="tag",
        )
        if result["created"]:
            created += 1

    # 3. inFolder — bookmark → folder
    folder = (event.folder or "").strip()
    if folder:
        result = add_triple(
            conn, bookmark_node_id, "inFolder", folder,
            provenance=provenance, source_ref=source_ref,
            object_kind="folder",
        )
        if result["created"]:
            created += 1

    # 4. mentions — web_page → concept from title + note
    text_parts: list[str] = []
    if event.title:
        text_parts.append(event.title)
    if event.note:
        text_parts.append(event.note)
    if text_parts:
        concepts = _extract_concepts(" ".join(text_parts))
        for concept in concepts:
            result = add_triple(
                conn, web_page_node_id, "mentions", concept,
                provenance=provenance, source_ref=source_ref,
                object_kind="concept",
            )
            if result["created"]:
                created += 1

    return created
