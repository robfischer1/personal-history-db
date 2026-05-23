"""Facebook connections ingest logic — ported from legacy adapter."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from phdb.formats.facebook_connections_html import normalize_name

if TYPE_CHECKING:
    from phdb.records import Connection


# Map connection_status back to source file label for the DB appearance log.
_STATUS_TO_FILE: dict[str, str] = {
    "active": "your_friends.html",
    "inactive": "removed_friends.html",
    "pending_outbound": "sent_friend_requests.html",
    "pending_inbound": "received_friend_requests.html",
    "rejected": "rejected_friend_requests.html",
}


def make_dedupe_key(profile_url: str | None, name_normalized: str) -> str:
    if profile_url:
        return f"url:{profile_url}"
    return f"name:{name_normalized}"


def hash_row(record: Connection, dedupe_key: str, source_file_label: str) -> str:
    canonical = json.dumps({
        "key": dedupe_key,
        "instrument": record.platform,
        "name": record.display_name,
        "status": record.connection_status,
        "friends_since": record.friends_since,
        "source_file": source_file_label,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upsert_connection(
    conn: sqlite3.Connection,
    record: Connection,
    *,
    export_id: str,
    export_date: str,
    source_file_id: int,
) -> int:
    name_norm = normalize_name(record.display_name)
    # Note: legacy adapter had profile_url and profile_id in ConnectionRow,
    # but the Connection record in phdb.records doesn't have them yet.
    # Looking at phdb.formats.facebook_connections_html, it doesn't extract them anyway.
    profile_url = None
    profile_id = None
    vanity_slug = None

    dedupe_key = make_dedupe_key(profile_url, name_norm)
    source_file_label = _STATUS_TO_FILE.get(record.connection_status, "your_friends.html")
    rh = hash_row(record, dedupe_key, source_file_label)

    appearance = {
        "export_id": export_id,
        "export_date": export_date,
        "file": source_file_label,
        "status": record.connection_status,
        "friends_since": record.friends_since,
        "raw_name": record.display_name,
        "raw_date": None, # legacy raw_extra.get("raw_date_str")
    }

    existing = conn.execute(
        """SELECT id, connection_status, friends_since, friends_since_source,
                  display_name, last_seen_at, appearance_count, appearances_json,
                  inactive_reason
             FROM connections
            WHERE instrument=? AND dedupe_key=?""",
        (record.platform, dedupe_key),
    ).fetchone()

    now_iso = datetime.now(UTC).isoformat()
    if existing is None:
        appearances_json = json.dumps([appearance], ensure_ascii=False)
        inactive_reason = record.inactive_reason
        cur = conn.execute(
            """INSERT INTO connections
               (schema_type, instrument, dedupe_key, profile_url, profile_id, vanity_slug,
                display_name, name_normalized, person_link,
                connection_status, inactive_reason,
                friends_since, friends_since_source,
                first_seen_export, last_seen_export, last_seen_at,
                appearance_count, appearances_json, source_file_id, raw_hash,
                ingested_at, updated_at)
               VALUES ('BefriendAction', ?, ?, ?, ?, ?,
                       ?, ?, NULL,
                       ?, ?,
                       ?, ?,
                       ?, ?, ?,
                       1, ?, ?, ?,
                       ?, ?)
               RETURNING id""",
            (record.platform, dedupe_key, profile_url, profile_id, vanity_slug,
             record.display_name, name_norm,
             record.connection_status, inactive_reason,
             record.friends_since, (export_id if record.friends_since else None),
             export_id, export_id, export_date,
             appearances_json, source_file_id, rh,
             now_iso, now_iso),
        )
        return int(cur.fetchone()[0])

    (row_id, prev_status, prev_fs, prev_fs_src, prev_name,
     prev_last_seen_at, prev_count, prev_app_json, prev_inactive_reason) = existing

    try:
        appearances = json.loads(prev_app_json) if prev_app_json else []
    except json.JSONDecodeError:
        appearances = []
    appearances.append(appearance)
    appearances_json = json.dumps(appearances, ensure_ascii=False)

    if export_date >= (prev_last_seen_at or ""):
        new_status = record.connection_status
        new_display_name = record.display_name
        if record.connection_status == "inactive":
            new_inactive_reason = record.inactive_reason or prev_inactive_reason
        else:
            new_inactive_reason = None
        new_last_seen_export = export_id
        new_last_seen_at = export_date
    else:
        new_status = prev_status
        new_display_name = prev_name
        new_inactive_reason = prev_inactive_reason
        new_last_seen_export = None
        new_last_seen_at = None

    candidates = [(prev_fs, prev_fs_src),
                  (record.friends_since, export_id if record.friends_since else None)]
    candidates_filtered = [(d, s) for d, s in candidates if d]
    if candidates_filtered:
        candidates_filtered.sort(key=lambda t: str(t[0]))
        new_fs, new_fs_src = candidates_filtered[0]
    else:
        new_fs, new_fs_src = None, None

    if new_last_seen_export is not None:
        conn.execute(
            """UPDATE connections
                  SET connection_status=?, display_name=?, inactive_reason=?,
                      friends_since=?, friends_since_source=?,
                      last_seen_export=?, last_seen_at=?,
                      appearance_count=appearance_count+1,
                      appearances_json=?, source_file_id=?, raw_hash=?,
                      updated_at=?
                WHERE id=?""",
            (new_status, new_display_name, new_inactive_reason,
             new_fs, new_fs_src,
             new_last_seen_export, new_last_seen_at,
             appearances_json, source_file_id, rh,
             now_iso, row_id),
        )
    else:
        conn.execute(
            """UPDATE connections
                  SET friends_since=?, friends_since_source=?,
                      appearance_count=appearance_count+1,
                      appearances_json=?, updated_at=?
                WHERE id=?""",
            (new_fs, new_fs_src, appearances_json, now_iso, row_id),
        )
    return int(row_id)


def post_pass_infer_inactive(conn: sqlite3.Connection, current_export_id: str) -> int:
    """Flip rows that were active but missing from the latest current export."""
    cur = conn.execute(
        """UPDATE connections
              SET connection_status='inactive',
                  inactive_reason='missing_from_latest_export',
                  updated_at=strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE instrument='facebook'
              AND connection_status='active'
              AND last_seen_export <> ?""",
        (current_export_id,),
    )
    return cur.rowcount
