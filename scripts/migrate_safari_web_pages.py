#!/usr/bin/env python3
"""Migrate 30 Safari rows from _web_pages_safari_backup into the new schema.

Run AFTER migration 0023_web_pages_entity.sql has been applied.

Safari bookmarks  → web_pages entity + bookmarks action row
Safari visits     → web_pages entity only (BrowseAction deferred)

Usage:
    python scripts/migrate_safari_web_pages.py          # dry-run (default)
    python scripts/migrate_safari_web_pages.py --apply   # commit changes
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from phdb.formats.url import extract_domain, is_junk, normalize_url


def get_db_path() -> Path:
    import os
    p = os.environ.get("PHDB_DB_PATH")
    if p:
        return Path(p)
    default = Path.home() / "Forge" / "personal-history-instance" / "personal_history.db"
    if default.exists():
        return default
    print("ERROR: Set PHDB_DB_PATH or ensure default DB exists", file=sys.stderr)
    sys.exit(1)


def extract_url_from_body(body_text: str, body_text_source: str) -> str | None:
    """Extract the raw URL from body_text like 'Bookmark: <url>' or 'Visited: <url>'."""
    if not body_text:
        return None
    if body_text_source == "safari-bookmark" and body_text.startswith("Bookmark: "):
        return body_text[10:].strip()
    if body_text_source == "safari-visit" and body_text.startswith("Visited: "):
        return body_text[9:].strip()
    return None


def migrate(conn: sqlite3.Connection, *, apply: bool) -> dict[str, int]:
    stats = {"safari_rows": 0, "web_pages_created": 0, "web_pages_merged": 0,
             "bookmarks_created": 0, "visits_noted": 0, "skipped": 0}

    try:
        rows = conn.execute(
            """SELECT id, page_key, subject, body_text, body_text_source,
                      date_recorded, source_file_id, raw_hash
               FROM _web_pages_safari_backup"""
        ).fetchall()
    except sqlite3.OperationalError:
        print("No _web_pages_safari_backup table found. Migration 0023 may not have run yet.")
        return stats

    stats["safari_rows"] = len(rows)
    print(f"Found {len(rows)} Safari rows in backup table.")

    for row_id, page_key, subject, body_text, body_text_source, date_recorded, sfid, raw_hash in rows:
        url = extract_url_from_body(body_text or "", body_text_source or "")
        if not url:
            print(f"  SKIP row {row_id}: could not extract URL from body_text={body_text!r}")
            stats["skipped"] += 1
            continue

        norm = normalize_url(url)
        domain = extract_domain(norm)
        title = subject  # subject column held the page title
        is_bookmark = (body_text_source == "safari-bookmark")

        existing = conn.execute(
            "SELECT id FROM web_pages WHERE normalized_url = ?", (norm,)
        ).fetchone()

        if existing:
            wp_id = existing[0]
            if apply:
                conn.execute(
                    """UPDATE web_pages SET
                        title = COALESCE(NULLIF(?, ''), title),
                        first_seen = CASE
                            WHEN ? IS NOT NULL AND (first_seen IS NULL OR ? < first_seen) THEN ?
                            ELSE first_seen END,
                        last_seen = CASE
                            WHEN ? IS NOT NULL AND (last_seen IS NULL OR ? > last_seen) THEN ?
                            ELSE last_seen END
                    WHERE id = ?""",
                    (title,
                     date_recorded, date_recorded, date_recorded,
                     date_recorded, date_recorded, date_recorded,
                     wp_id),
                )
            stats["web_pages_merged"] += 1
            print(f"  MERGE row {row_id} → existing web_page {wp_id}: {norm[:60]}")
        else:
            if apply:
                cur = conn.execute(
                    """INSERT INTO web_pages (url, normalized_url, title, domain,
                                             first_seen, last_seen, source_file_id)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (url, norm, title, domain, date_recorded, date_recorded, sfid),
                )
                wp_id = cur.lastrowid
            else:
                wp_id = -1
            stats["web_pages_created"] += 1
            print(f"  CREATE web_page for row {row_id}: {norm[:60]}")

        if is_bookmark:
            junk = is_junk(url)
            if apply:
                conn.execute(
                    """INSERT INTO bookmarks
                       (schema_type, instrument, url, normalized_url, title,
                        first_seen_in_instrument, appearance_count,
                        excluded, excluded_reason,
                        source_file_id, raw_hash, web_page_id)
                       VALUES ('BookmarkAction', 'safari', ?, ?, ?,
                               ?, 1, ?, ?, ?, ?, ?)
                       ON CONFLICT(normalized_url, instrument) DO UPDATE SET
                           title = COALESCE(NULLIF(excluded.title, ''), bookmarks.title),
                           appearance_count = bookmarks.appearance_count + 1,
                           web_page_id = excluded.web_page_id""",
                    (url, norm, title, date_recorded,
                     1 if junk else 0, junk, sfid, raw_hash, wp_id),
                )
            stats["bookmarks_created"] += 1
            print(f"    + bookmark action (instrument=safari)")
        else:
            stats["visits_noted"] += 1
            print(f"    (visit — web_page entity only, BrowseAction deferred)")

    if apply:
        conn.execute("DROP TABLE IF EXISTS _web_pages_safari_backup")
        conn.commit()
        print("\nDropped _web_pages_safari_backup.")
    else:
        print("\nDRY RUN — no changes written. Use --apply to commit.")

    return stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Commit changes (default is dry-run)")
    parser.add_argument("--db", type=Path, help="Override DB path")
    args = parser.parse_args()

    db_path = args.db or get_db_path()
    print(f"Using DB: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")

    try:
        stats = migrate(conn, apply=args.apply)
        print(f"\nStats: {stats}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
