"""Facebook connections adapter — ingests FB friends graph from takeout exports.

Source: Facebook export zip containing connections/friends/*.html files.
Writes to the `connections` table (not messages). Custom run() override.

Name normalization: NFKD strip + lowercase + collapse whitespace + drop minor punct.
Dedupe key: url:{profile_url} if available, else name:{normalized_name}.
Reconciliation: latest sighting wins for status, earliest non-null for friends_since.
Post-pass: mark missing-from-latest as inactive.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy, IngestReport
from phdb.formats.facebook_connections_html import (
    derive_export_date,
    derive_export_id,
    detect,
    normalize_name,
    parse,
)
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.records import Connection
    from phdb.settings import Settings

log = get_logger("phdb.adapters.facebook_connections")


# ---------------------------------------------------------------------------
# Row model
# ---------------------------------------------------------------------------

@dataclass
class ConnectionRow:
    instrument: str
    display_name: str
    connection_status: str
    source_file_label: str
    friends_since: str | None = None
    profile_url: str | None = None
    profile_id: str | None = None
    vanity_slug: str | None = None
    raw_extra: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Dedupe key
# ---------------------------------------------------------------------------

def make_dedupe_key(profile_url: str | None, name_normalized: str) -> str:
    if profile_url:
        return f"url:{profile_url}"
    return f"name:{name_normalized}"


# ---------------------------------------------------------------------------
# Upsert + reconciliation
# ---------------------------------------------------------------------------

def hash_row(row: ConnectionRow, dedupe_key: str) -> str:
    canonical = json.dumps({
        "key": dedupe_key,
        "instrument": row.instrument,
        "name": row.display_name,
        "status": row.connection_status,
        "friends_since": row.friends_since,
        "source_file": row.source_file_label,
    }, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upsert_connection(
    conn: sqlite3.Connection,
    row: ConnectionRow,
    *,
    export_id: str,
    export_date: str,
    source_file_id: int,
) -> int:
    name_norm = normalize_name(row.display_name)
    dedupe_key = make_dedupe_key(row.profile_url, name_norm)
    rh = hash_row(row, dedupe_key)
    appearance = {
        "export_id": export_id,
        "export_date": export_date,
        "file": row.source_file_label,
        "status": row.connection_status,
        "friends_since": row.friends_since,
        "raw_name": row.display_name,
        "raw_date": row.raw_extra.get("raw_date_str"),
    }

    existing = conn.execute(
        """SELECT id, connection_status, friends_since, friends_since_source,
                  display_name, last_seen_at, appearance_count, appearances_json,
                  inactive_reason
             FROM connections
            WHERE instrument=? AND dedupe_key=?""",
        (row.instrument, dedupe_key),
    ).fetchone()

    now_iso = datetime.now(UTC).isoformat()
    if existing is None:
        appearances_json = json.dumps([appearance], ensure_ascii=False)
        inactive_reason = ("removed_friends_file"
                           if (row.connection_status == "inactive"
                               and row.source_file_label == "removed_friends.html")
                           else None)
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
            (row.instrument, dedupe_key, row.profile_url, row.profile_id, row.vanity_slug,
             row.display_name, name_norm,
             row.connection_status, inactive_reason,
             row.friends_since, (export_id if row.friends_since else None),
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
        new_status = row.connection_status
        new_display_name = row.display_name
        if row.connection_status == "inactive":
            new_inactive_reason = ("removed_friends_file"
                                   if row.source_file_label == "removed_friends.html"
                                   else prev_inactive_reason)
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
                  (row.friends_since, export_id if row.friends_since else None)]
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


# ---------------------------------------------------------------------------
# Connection record → ConnectionRow conversion
# ---------------------------------------------------------------------------

def _connection_to_row(rec: Connection, source_file_label: str) -> ConnectionRow:
    """Convert a typed Connection record to a ConnectionRow for DB upsert."""
    return ConnectionRow(
        instrument=rec.platform,
        display_name=rec.display_name,
        connection_status=rec.connection_status,
        source_file_label=source_file_label,
        friends_since=rec.friends_since,
        raw_extra={},
    )


# Map connection_status back to source file label for the DB appearance log.
_STATUS_TO_FILE: dict[str, str] = {
    "active": "your_friends.html",
    "inactive": "removed_friends.html",
    "pending_outbound": "sent_friend_requests.html",
    "pending_inbound": "received_friend_requests.html",
    "rejected": "rejected_friend_requests.html",
}


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------

class FacebookConnectionsAdapter(Adapter):
    """Ingest Facebook friends graph from takeout exports."""

    name = "facebook_connections"
    source_kind = "facebook-connections"
    file_kind = "zip"
    schema_type = "BefriendAction"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        raise NotImplementedError("Use run() directly -- writes to connections table")

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

        if not detect(source_path):
            report.errors.append(f"No FB takeout detected at: {source_path}")
            return report

        export_date = derive_export_date(source_path)
        export_id = derive_export_id(source_path)

        batch_count = 0
        for rec in parse(source_path):
            report.rows_yielded += 1
            source_file_label = _STATUS_TO_FILE.get(
                rec.connection_status, "your_friends.html"
            )
            row = _connection_to_row(rec, source_file_label)
            upsert_connection(
                conn, row,
                export_id=export_id,
                export_date=export_date,
                source_file_id=source_file_id,
            )
            report.rows_inserted += 1

            batch_count += 1
            if batch_count >= self.batch_size:
                conn.commit()
                batch_count = 0

        conn.commit()

        # Post-pass: mark missing-from-latest as inactive
        n_inactive = post_pass_infer_inactive(conn, export_id)
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d inferred-inactive",
            self.name, report.rows_yielded, report.rows_inserted, n_inactive,
        )
        return report
