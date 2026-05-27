"""Source-specific query layer — communication tables, threads, writing deltas.

Phase 1 refactor (phdb Plugin Architecture plan, 2026-05-22):

- Cross-cutting hybrid retrieval primitives (``search``, ``nearest_neighbors``,
  ``rrf_fuse``, ``build_fts_query``, etc.) moved to ``phdb.core.search`` —
  imported back into this namespace for legacy callers.
- Generic chunk + source lookups (``get_chunk``, ``list_sources``,
  ``server_info``) moved to ``phdb.core.lookup`` — imported back into this
  namespace for legacy callers.
- Source-specific functions that depend on the three communication tables
  (emails / chat_messages / conversations_messages) and writing-deltas
  remain here as the "intermediate holding pen" until Phase 7 ports each
  to its plugin's ``queries.py``.

All functions take a ``sqlite3.Connection`` as their first argument.
The module is stateless; callers own connection lifecycle.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from phdb.core.lookup import get_chunk, list_sources, server_info
from phdb.core.registry import default_registry
from phdb.core.search import (  # noqa: F401 — re-exported for legacy callers
    DATE_FILTER_OVERSAMPLE,
    FTS_STOPWORDS,
    RRF_K,
    _corpus_year_counts,
    _count_typed_tables,
    _date_filter_ids,
    _fts_run,
    _fts_search,
    _hydrate,
    _lookup_decay_scores,
    _lookup_doc_years,
    _semantic_search,
    _year_weights,
    build_fts_query,
    nearest_neighbors,
    rrf_fuse,
    search,
)
from phdb.embed_provider import EmbedProvider  # noqa: F401 — legacy re-export
from phdb.embed_service import EmbedClient  # noqa: F401 — legacy re-export

__all__ = [
    # Cross-cutting (re-exported from phdb.core)
    "DATE_FILTER_OVERSAMPLE",
    "FTS_STOPWORDS",
    "RRF_K",
    "build_fts_query",
    "get_chunk",
    "list_sources",
    "nearest_neighbors",
    "rrf_fuse",
    "search",
    "server_info",
    # Source-specific (defined below — will move to plugins in Phase 7)
    "corpus_stats",
    "find_messages_by_participant",
    "find_threads_by_subject",
    "get_conversation",
    "get_message",
    "get_thread",
    "top_correspondents",
    "writing_arc",
    "writing_session_detail",
    "writing_stats",
]


# Communication tables — the ones with sender_address / direction / date_sent
# semantics. Sourced from the registry; Phase 7 retires this constant when
# the comm-table queries below move into their plugins.
def _comm_tables() -> list[str]:
    return default_registry().comm_table_names


def _comm_union_sql(
    *,
    select_cols: str = "date_sent, direction, sender_address, sender_name, is_bulk",
    alias: str = "msgs",
) -> str:
    """Build a CTE that unions the communication tables."""
    parts = [f"SELECT {select_cols} FROM [{t}]" for t in _comm_tables()]
    return f"WITH {alias} AS ({' UNION ALL '.join(parts)})"


def get_message(
    conn: sqlite3.Connection,
    msg_id: int,
    *,
    include_recipients: bool = True,
    include_attachments: bool = True,
) -> dict[str, Any]:
    """Fetch a full message by ID, searching across typed tables."""
    _table_queries: list[tuple[str, str]] = [
        ("emails", (
            "SELECT id, schema_type, rfc822_message_id, gmail_thread_id, gmail_labels,"
            " subject, sender_address, sender_name, sender_domain, direction,"
            " date_sent, date_received, body_text, body_text_source,"
            " is_multipart, has_attachments, attachment_count, is_bulk,"
            " bulk_signal, source_file_id"
            " FROM emails WHERE id = ?"
        )),
        ("chat_messages", (
            "SELECT id, schema_type, message_key AS rfc822_message_id,"
            " NULL AS gmail_thread_id, NULL AS gmail_labels,"
            " subject, sender_address, sender_name, sender_domain, direction,"
            " date_sent, date_received, body_text, body_text_source,"
            " is_multipart, has_attachments, attachment_count, is_bulk,"
            " bulk_signal, source_file_id"
            " FROM chat_messages WHERE id = ?"
        )),
        ("conversations_messages", (
            "SELECT id, schema_type, conversation_key AS rfc822_message_id,"
            " NULL AS gmail_thread_id, NULL AS gmail_labels,"
            " subject, sender_address, sender_name, sender_domain, direction,"
            " date_sent, NULL AS date_received, body_text, body_text_source,"
            " 0 AS is_multipart, 0 AS has_attachments, 0 AS attachment_count, is_bulk,"
            " bulk_signal, source_file_id"
            " FROM conversations_messages WHERE id = ?"
        )),
    ]
    row = None
    matched_table = None
    for table_name, sql in _table_queries:
        row = conn.execute(sql, (msg_id,)).fetchone()
        if row is not None:
            matched_table = table_name
            break
    if row is None:
        return {"error": f"No message with id={msg_id}"}
    out = dict(row)
    out["source_table"] = matched_table
    if include_recipients:
        rs = conn.execute(
            "SELECT n_obj.label AS address"
            " FROM triples t"
            " JOIN nodes n_sub ON n_sub.id = t.subject_node_id"
            " JOIN nodes n_obj ON n_obj.id = t.object_node_id"
            " JOIN predicates p ON p.id = t.predicate_id"
            " WHERE p.name = 'sentTo'"
            " AND n_sub.source_table = ? AND n_sub.source_id = ?"
            " ORDER BY n_obj.label",
            (matched_table, msg_id),
        ).fetchall()
        out["recipients"] = [{"address": r[0], "name": None, "rtype": "to"} for r in rs]
    if include_attachments and row["attachment_count"]:
        atts = conn.execute(
            "SELECT filename, content_type, size_bytes, on_disk_path"
            " FROM attachments WHERE message_id = ? ORDER BY id",
            (msg_id,),
        ).fetchall()
        out["attachments"] = [dict(a) for a in atts]
    return out


def get_thread(
    conn: sqlite3.Connection,
    *,
    thread_id: str | None = None,
    msg_id: int | None = None,
    max_messages: int = 50,
) -> dict[str, Any]:
    """Fetch all emails in a thread, ordered by date.

    Gmail threads are email-only — queries the emails table directly.
    """
    if thread_id is None and msg_id is None:
        return {"error": "provide thread_id or msg_id"}
    if thread_id is None:
        r = conn.execute(
            "SELECT gmail_thread_id FROM emails WHERE id = ?", (msg_id,)
        ).fetchone()
        if r is None or not r["gmail_thread_id"]:
            return {"error": f"msg_id={msg_id} has no gmail_thread_id"}
        thread_id = r["gmail_thread_id"]
    rows = conn.execute(
        "SELECT id AS msg_id, subject, sender_address, sender_name, direction,"
        " date_sent, is_bulk, substr(body_text, 1, 300) AS body_preview"
        " FROM emails WHERE gmail_thread_id = ? ORDER BY date_sent LIMIT ?",
        (thread_id, max_messages),
    ).fetchall()
    return {
        "thread_id": thread_id,
        "message_count": len(rows),
        "messages": [dict(r) for r in rows],
    }


def get_conversation(
    conn: sqlite3.Connection,
    participant: str,
    *,
    since: str | None = None,
    until: str | None = None,
    max_messages: int = 100,
) -> dict[str, Any]:
    """Fetch a full bidirectional conversation with a participant.

    Resolves the participant name via contact_name_lookup, then retrieves
    both inbound (from them) and outbound (to them) messages across all
    communication tables, interleaved chronologically.
    """
    resolved = _resolve_addresses(conn, participant)
    p = f"%{participant.lower()}%"

    direct_matches = conn.execute(
        "SELECT DISTINCT LOWER(sender_address) FROM chat_messages"
        " WHERE LOWER(sender_name) LIKE ? OR LOWER(sender_address) LIKE ?",
        (p, p),
    ).fetchall()
    all_addrs = list(set(resolved + [r[0] for r in direct_matches if r[0]]))

    if not all_addrs:
        return {"error": f"No addresses found for participant {participant!r}"}

    placeholders = ",".join("?" for _ in all_addrs)

    where_clauses: list[str] = []
    where_args: list[Any] = []
    if since:
        where_clauses.append("date_sent >= ?")
        where_args.append(since)
    if until:
        where_clauses.append("date_sent <= ?")
        where_args.append(until)
    extra_where = (" AND " + " AND ".join(where_clauses)) if where_clauses else ""

    inbound_sql = (
        f"SELECT id AS msg_id, 'inbound' AS direction, sender_name, sender_address,"
        f" date_sent, substr(body_text, 1, 500) AS body_preview, 'chat_messages' AS source_table"
        f" FROM chat_messages"
        f" WHERE LOWER(sender_address) IN ({placeholders}){extra_where}"
    )
    outbound_sql = (
        f"SELECT cm.id AS msg_id, 'outbound' AS direction, cm.sender_name, cm.sender_address,"
        f" cm.date_sent, substr(cm.body_text, 1, 500) AS body_preview, 'chat_messages' AS source_table"
        f" FROM chat_messages cm"
        f" JOIN nodes rn ON rn.source_table = 'chat_messages' AND rn.source_id = cm.id"
        f" JOIN triples t ON t.subject_node_id = rn.id"
        f" JOIN predicates pred ON pred.id = t.predicate_id AND pred.name = 'sentTo'"
        f" JOIN nodes cn ON cn.id = t.object_node_id AND cn.kind = 'contact'"
        f" WHERE cm.direction = 'outbound'"
        f" AND cn.normalized_label IN ({placeholders}){extra_where}"
    )
    sql = (
        f"SELECT * FROM ({inbound_sql} UNION {outbound_sql})"
        f" ORDER BY date_sent DESC LIMIT ?"
    )
    args = all_addrs + where_args + all_addrs + where_args + [max_messages]
    rows = conn.execute(sql, args).fetchall()

    return {
        "participant": participant,
        "resolved_addresses": all_addrs,
        "since": since,
        "until": until,
        "message_count": len(rows),
        "messages": [dict(r) for r in rows],
    }


def corpus_stats(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    until: str | None = None,
) -> dict[str, Any]:
    """Year distribution + direction/sender breakdowns."""
    args: list[Any] = []
    where: list[str] = []
    if since:
        where.append("substr(date_sent, 1, 10) >= ?")
        args.append(since)
    if until:
        where.append("substr(date_sent, 1, 10) <= ?")
        args.append(until)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    cte = _comm_union_sql(
        select_cols="date_sent, direction, sender_address, is_bulk",
    )

    by_year = conn.execute(
        f"{cte}"
        f" SELECT substr(date_sent, 1, 4) AS year, COUNT(*) AS messages"
        f" FROM msgs {where_sql} GROUP BY year ORDER BY year",
        args,
    ).fetchall()
    by_dir = conn.execute(
        f"{cte}"
        f" SELECT direction, COUNT(*) AS n FROM msgs {where_sql}"
        f" GROUP BY direction",
        args,
    ).fetchall()
    top_senders = conn.execute(
        f"{cte}"
        f" SELECT sender_address, COUNT(*) AS n FROM msgs {where_sql}"
        f" {'AND' if where_sql else 'WHERE'} is_bulk = 0"
        f" GROUP BY sender_address ORDER BY n DESC LIMIT 20",
        args,
    ).fetchall()
    return {
        "since": since,
        "until": until,
        "by_year": [dict(r) for r in by_year],
        "by_direction": [dict(r) for r in by_dir],
        "top_senders_nonbulk": [dict(r) for r in top_senders],
    }


def _resolve_addresses(conn: sqlite3.Connection, participant: str) -> list[str]:
    """Resolve a name fragment to matching addresses via contact_name_lookup."""
    p = f"%{participant.lower()}%"
    try:
        rows = conn.execute(
            "SELECT DISTINCT LOWER(address) FROM contact_name_lookup"
            " WHERE LOWER(display_name) LIKE ?",
            (p,),
        ).fetchall()
        return [r[0] for r in rows]
    except sqlite3.OperationalError:
        return []


def find_messages_by_participant(
    conn: sqlite3.Connection,
    participant: str,
    *,
    role: str = "any",
    direction: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    include_bulk: bool = False,
) -> dict[str, Any]:
    """Find messages where a person appears as sender, recipient, or either."""
    p = f"%{participant.lower()}%"
    resolved_addrs = _resolve_addresses(conn, participant)

    comm_cte = (
        "WITH comm AS ("
        " SELECT id, date_sent, direction, sender_address, sender_name,"
        "  subject, is_bulk, gmail_thread_id"
        "  FROM emails"
        " UNION ALL"
        " SELECT id, date_sent, direction, sender_address, sender_name,"
        "  subject, is_bulk, NULL AS gmail_thread_id"
        "  FROM chat_messages"
        " UNION ALL"
        " SELECT id, date_sent, direction, sender_address, sender_name,"
        "  subject, is_bulk, NULL AS gmail_thread_id"
        "  FROM conversations_messages"
        ")"
    )

    selectors: list[tuple[str, list[Any]]] = []
    if role in ("sender", "any"):
        sender_clauses = [
            "LOWER(sender_address) LIKE ?",
            "LOWER(COALESCE(sender_name, '')) LIKE ?",
        ]
        sender_args: list[Any] = [p, p]
        if resolved_addrs:
            placeholders = ",".join("?" for _ in resolved_addrs)
            sender_clauses.append(f"LOWER(sender_address) IN ({placeholders})")
            sender_args.extend(resolved_addrs)
        selectors.append((
            "SELECT id AS msg_id, 'sender' AS matched_via FROM comm"
            f" WHERE {' OR '.join(sender_clauses)}",
            sender_args,
        ))
    if role in ("recipient", "any"):
        recip_clauses = ["n_obj.normalized_label LIKE ?"]
        recip_args: list[Any] = [p]
        if resolved_addrs:
            placeholders = ",".join("?" for _ in resolved_addrs)
            recip_clauses.append(f"n_obj.normalized_label IN ({placeholders})")
            recip_args.extend(resolved_addrs)
        selectors.append((
            "SELECT n_sub.source_id AS msg_id, 'recipient' AS matched_via"
            " FROM triples t"
            " JOIN nodes n_sub ON n_sub.id = t.subject_node_id"
            " JOIN nodes n_obj ON n_obj.id = t.object_node_id"
            " JOIN predicates p ON p.id = t.predicate_id"
            " WHERE p.name = 'sentTo'"
            f" AND ({' OR '.join(recip_clauses)})",
            recip_args,
        ))
    if not selectors:
        return {"error": f"role must be 'sender', 'recipient', or 'any'; got {role!r}"}

    union_sql = " UNION ".join(s[0] for s in selectors)
    union_args: list[Any] = []
    for _, a in selectors:
        union_args.extend(a)

    where_clauses: list[str] = []
    where_args: list[Any] = []
    if direction:
        where_clauses.append("m.direction = ?")
        where_args.append(direction)
    if since:
        where_clauses.append("substr(m.date_sent, 1, 10) >= ?")
        where_args.append(since)
    if until:
        where_clauses.append("substr(m.date_sent, 1, 10) <= ?")
        where_args.append(until)
    if not include_bulk:
        where_clauses.append("m.is_bulk = 0")
    where_sql = (" AND " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = (
        f"{comm_cte},"
        f" matches AS ({union_sql})"
        f" SELECT m.id AS msg_id, m.date_sent, m.direction,"
        f" m.sender_address, m.sender_name, m.subject,"
        f" m.gmail_thread_id, m.is_bulk,"
        f" (SELECT GROUP_CONCAT(DISTINCT matched_via)"
        f"  FROM matches mt WHERE mt.msg_id = m.id) AS matched_via"
        f" FROM comm m"
        f" WHERE m.id IN (SELECT msg_id FROM matches){where_sql}"
        f" ORDER BY m.date_sent DESC LIMIT ?"
    )
    rows = conn.execute(sql, union_args + where_args + [limit]).fetchall()

    result: dict[str, Any] = {
        "participant": participant,
        "role": role,
        "since": since,
        "until": until,
        "match_count": len(rows),
        "messages": [
            {
                "msg_id": r["msg_id"],
                "date": (r["date_sent"] or "")[:10] or None,
                "direction": r["direction"],
                "sender": r["sender_name"] or r["sender_address"],
                "sender_address": r["sender_address"],
                "subject": r["subject"],
                "thread_id": r["gmail_thread_id"],
                "is_bulk": bool(r["is_bulk"]),
                "matched_via": r["matched_via"],
            }
            for r in rows
        ],
    }
    if resolved_addrs:
        result["resolved_addresses"] = resolved_addrs
    return result


def find_threads_by_subject(
    conn: sqlite3.Connection,
    query: str,
    *,
    since: str | None = None,
    until: str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """Find conversation threads by canonical subject line."""
    q = f"%{query.lower()}%"

    where = [
        "gmail_thread_id IS NOT NULL",
        "(LOWER(COALESCE(subject, '')) LIKE ? OR gmail_thread_id LIKE ?)",
    ]
    args: list[Any] = [q, q]
    if since:
        where.append("substr(date_sent, 1, 10) >= ?")
        args.append(since)
    if until:
        where.append("substr(date_sent, 1, 10) <= ?")
        args.append(until)
    where_sql = " AND ".join(where)

    rows = conn.execute(
        f"SELECT gmail_thread_id,"
        f" MIN(subject) AS subject_canonical,"
        f" COUNT(*) AS message_count,"
        f" MIN(date_sent) AS date_first,"
        f" MAX(date_sent) AS date_last"
        f" FROM emails WHERE {where_sql}"
        f" GROUP BY gmail_thread_id"
        f" ORDER BY date_last DESC LIMIT ?",
        args + [limit],
    ).fetchall()

    out = []
    for r in rows:
        out.append({
            "thread_db_id": None,
            "thread_id": r["gmail_thread_id"],
            "subject": r["subject_canonical"],
            "message_count": r["message_count"],
            "date_first": r["date_first"],
            "date_last": r["date_last"],
            "participants": None,
        })
    return {
        "query": query,
        "since": since,
        "until": until,
        "match_count": len(out),
        "threads": out,
    }


def top_correspondents(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    until: str | None = None,
    role: str = "sender",
    limit: int = 20,
    exclude_bulk: bool = True,
    exclude_self: bool = True,
) -> dict[str, Any]:
    """Most-frequent correspondents in a date window."""
    where: list[str] = []
    args: list[Any] = []
    if since:
        where.append("substr(m.date_sent, 1, 10) >= ?")
        args.append(since)
    if until:
        where.append("substr(m.date_sent, 1, 10) <= ?")
        args.append(until)
    if exclude_bulk:
        where.append("m.is_bulk = 0")
    if exclude_self:
        where.append("m.direction != 'self'")
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""

    comm_cte = (
        "WITH comm AS ("
        " SELECT id, date_sent, direction, sender_address, sender_name, is_bulk"
        "  FROM emails"
        " UNION ALL"
        " SELECT id, date_sent, direction, sender_address, sender_name, is_bulk"
        "  FROM chat_messages"
        " UNION ALL"
        " SELECT id, date_sent, direction, sender_address, sender_name, is_bulk"
        "  FROM conversations_messages"
        ")"
    )

    if role == "sender":
        sql = (
            f"{comm_cte}"
            f" SELECT m.sender_address AS address,"
            f" MAX(m.sender_name) AS name, COUNT(*) AS message_count"
            f" FROM comm m {where_sql}"
            f" GROUP BY m.sender_address ORDER BY message_count DESC LIMIT ?"
        )
        rows = conn.execute(sql, args + [limit]).fetchall()
    elif role == "recipient":
        sql = (
            f"{comm_cte}"
            f" SELECT n_obj.label AS address, NULL AS name,"
            f" COUNT(*) AS message_count"
            f" FROM triples t"
            f" JOIN predicates p ON p.id = t.predicate_id"
            f" JOIN nodes n_sub ON n_sub.id = t.subject_node_id"
            f" JOIN nodes n_obj ON n_obj.id = t.object_node_id"
            f" JOIN comm m ON m.id = n_sub.source_id"
            f" WHERE p.name = 'sentTo'"
            f" AND n_sub.source_table IN ('emails', 'chat_messages', 'conversations_messages')"
            f" {('AND ' + ' AND '.join(where)) if where else ''}"
            f" GROUP BY n_obj.label ORDER BY message_count DESC LIMIT ?"
        )
        rows = conn.execute(sql, args + [limit]).fetchall()
    elif role == "both":
        sql = (
            f"{comm_cte},"
            f" combined AS ("
            f" SELECT m.sender_address AS address, m.sender_name AS name"
            f"  FROM comm m {where_sql}"
            f" UNION ALL"
            f" SELECT n_obj.label AS address, NULL AS name"
            f"  FROM triples t"
            f"  JOIN predicates p ON p.id = t.predicate_id"
            f"  JOIN nodes n_sub ON n_sub.id = t.subject_node_id"
            f"  JOIN nodes n_obj ON n_obj.id = t.object_node_id"
            f"  JOIN comm m ON m.id = n_sub.source_id"
            f"  WHERE p.name = 'sentTo'"
            f"  AND n_sub.source_table IN ('emails', 'chat_messages', 'conversations_messages')"
            f"  {('AND ' + ' AND '.join(where)) if where else ''})"
            f" SELECT address, MAX(name) AS name, COUNT(*) AS message_count"
            f" FROM combined GROUP BY address ORDER BY message_count DESC LIMIT ?"
        )
        rows = conn.execute(sql, args + args + [limit]).fetchall()
    else:
        return {"error": f"role must be 'sender', 'recipient', or 'both'; got {role!r}"}

    return {
        "role": role,
        "since": since,
        "until": until,
        "correspondents": [
            {
                "address": r["address"],
                "name": r["name"],
                "message_count": r["message_count"],
            }
            for r in rows
        ],
    }


# ---------------------------------------------------------------------------
# Writing delta-stream queries — back the `obsidian-delta-stream` capture
# (will move into `phdb.plugins.writing_deltas/queries.py` during Phase 7)
# ---------------------------------------------------------------------------

def _iso_date_to_epoch_ms(iso_date: str, *, end_of_day: bool = False) -> int | None:
    """Convert 'YYYY-MM-DD' to epoch milliseconds (UTC midnight, or next-day midnight)."""
    try:
        from datetime import UTC, datetime, timedelta

        d = datetime.strptime(iso_date, "%Y-%m-%d").replace(tzinfo=UTC)
        if end_of_day:
            d = d + timedelta(days=1)
        return int(d.timestamp() * 1000)
    except (ValueError, TypeError):
        return None


def _serialize_delta(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "ts": r["ts"],
        "event_type": r["event_type"],
        "user_event": r["user_event"],
        "inserted_text": r["inserted_text"],
        "deleted_text": r["deleted_text"],
        "from_a": r["from_a"],
        "to_a": r["to_a"],
        "from_b": r["from_b"],
        "to_b": r["to_b"],
    }


def writing_arc(
    conn: sqlite3.Connection,
    note_path: str,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Return writing sessions for a given note, most recent first."""
    rows = conn.execute(
        """SELECT session_id, note_path, vault_folder, note_type,
                  started_at, ended_at, ended_reason,
                  doc_change_count, selection_change_count,
                  insert_count, delete_count,
                  total_inserted_chars, total_deleted_chars,
                  undo_count, paste_count
           FROM writing_sessions
           WHERE note_path = ?
           ORDER BY started_at DESC
           LIMIT ?""",
        (note_path, limit),
    ).fetchall()

    sessions: list[dict[str, Any]] = []
    for r in rows:
        duration_ms: int | None = None
        if r["ended_at"] is not None and r["started_at"] is not None:
            duration_ms = int(r["ended_at"]) - int(r["started_at"])
        inserted = int(r["total_inserted_chars"] or 0)
        deleted = int(r["total_deleted_chars"] or 0)
        rewrite_ratio = (deleted / inserted) if inserted > 0 else 0.0
        sessions.append({
            "session_id": r["session_id"],
            "started_at": r["started_at"],
            "ended_at": r["ended_at"],
            "ended_reason": r["ended_reason"],
            "duration_ms": duration_ms,
            "vault_folder": r["vault_folder"],
            "note_type": r["note_type"],
            "doc_change_count": int(r["doc_change_count"] or 0),
            "selection_change_count": int(r["selection_change_count"] or 0),
            "insert_count": int(r["insert_count"] or 0),
            "delete_count": int(r["delete_count"] or 0),
            "total_inserted_chars": inserted,
            "total_deleted_chars": deleted,
            "undo_count": int(r["undo_count"] or 0),
            "paste_count": int(r["paste_count"] or 0),
            "rewrite_ratio": round(rewrite_ratio, 3),
        })

    return {
        "note_path": note_path,
        "session_count": len(sessions),
        "sessions": sessions,
    }


def writing_session_detail(
    conn: sqlite3.Connection,
    session_id: str,
    *,
    delta_sample_size: int = 10,
) -> dict[str, Any]:
    """Return one writing session + first/last/reversal samples of its deltas."""
    session = conn.execute(
        """SELECT id, session_id, note_path, vault_folder, note_type,
                  started_at, ended_at, ended_reason,
                  doc_change_count, selection_change_count,
                  insert_count, delete_count,
                  total_inserted_chars, total_deleted_chars,
                  undo_count, paste_count, ingested_at
           FROM writing_sessions WHERE session_id = ?""",
        (session_id,),
    ).fetchone()
    if session is None:
        return {"error": f"No writing session with session_id={session_id!r}"}

    session_pk = int(session["id"])
    first_rows = conn.execute(
        """SELECT ts, event_type, user_event, inserted_text, deleted_text,
                  from_a, to_a, from_b, to_b
           FROM writing_deltas WHERE session_pk = ?
           ORDER BY ts ASC LIMIT ?""",
        (session_pk, delta_sample_size),
    ).fetchall()
    last_rows = conn.execute(
        """SELECT ts, event_type, user_event, inserted_text, deleted_text,
                  from_a, to_a, from_b, to_b
           FROM writing_deltas WHERE session_pk = ?
           ORDER BY ts DESC LIMIT ?""",
        (session_pk, delta_sample_size),
    ).fetchall()
    reversals = conn.execute(
        """SELECT ts, event_type, user_event, inserted_text, deleted_text,
                  from_a, to_a, from_b, to_b
           FROM writing_deltas WHERE session_pk = ?
             AND user_event IN ('undo', 'input.paste')
           ORDER BY ts ASC""",
        (session_pk,),
    ).fetchall()

    duration_ms: int | None = None
    if session["ended_at"] is not None and session["started_at"] is not None:
        duration_ms = int(session["ended_at"]) - int(session["started_at"])

    return {
        "session_id": session["session_id"],
        "note_path": session["note_path"],
        "vault_folder": session["vault_folder"],
        "note_type": session["note_type"],
        "started_at": session["started_at"],
        "ended_at": session["ended_at"],
        "ended_reason": session["ended_reason"],
        "duration_ms": duration_ms,
        "aggregates": {
            "doc_change_count": int(session["doc_change_count"] or 0),
            "selection_change_count": int(session["selection_change_count"] or 0),
            "insert_count": int(session["insert_count"] or 0),
            "delete_count": int(session["delete_count"] or 0),
            "total_inserted_chars": int(session["total_inserted_chars"] or 0),
            "total_deleted_chars": int(session["total_deleted_chars"] or 0),
            "undo_count": int(session["undo_count"] or 0),
            "paste_count": int(session["paste_count"] or 0),
        },
        "first_events": [_serialize_delta(r) for r in first_rows],
        "last_events": [_serialize_delta(r) for r in reversed(list(last_rows))],
        "reversals": [_serialize_delta(r) for r in reversals],
        "ingested_at": session["ingested_at"],
    }


def writing_stats(
    conn: sqlite3.Connection,
    *,
    since: str | None = None,
    until: str | None = None,
    note_path: str | None = None,
    top_n: int = 10,
) -> dict[str, Any]:
    """Corpus-level writing-stream stats with optional date / note_path filters."""
    where: list[str] = []
    args: list[Any] = []

    if since:
        since_ms = _iso_date_to_epoch_ms(since)
        if since_ms is not None:
            where.append("started_at >= ?")
            args.append(since_ms)
    if until:
        until_ms = _iso_date_to_epoch_ms(until, end_of_day=True)
        if until_ms is not None:
            where.append("started_at < ?")
            args.append(until_ms)
    if note_path:
        where.append("note_path = ?")
        args.append(note_path)

    where_sql = (" WHERE " + " AND ".join(where)) if where else ""

    agg = conn.execute(
        f"""SELECT
            COUNT(*) as session_count,
            COUNT(DISTINCT note_path) as notes_touched,
            COALESCE(SUM(doc_change_count), 0) as total_doc_changes,
            COALESCE(SUM(selection_change_count), 0) as total_selection_changes,
            COALESCE(SUM(insert_count), 0) as total_inserts,
            COALESCE(SUM(delete_count), 0) as total_deletes,
            COALESCE(SUM(total_inserted_chars), 0) as total_inserted_chars,
            COALESCE(SUM(total_deleted_chars), 0) as total_deleted_chars,
            COALESCE(SUM(undo_count), 0) as total_undos,
            COALESCE(SUM(paste_count), 0) as total_pastes,
            MIN(started_at) as earliest_start,
            MAX(COALESCE(ended_at, started_at)) as latest_end
            FROM writing_sessions{where_sql}""",
        args,
    ).fetchone()

    top_notes = conn.execute(
        f"""SELECT note_path,
                   COUNT(*) as session_count,
                   COALESCE(SUM(doc_change_count), 0) as total_doc_changes,
                   COALESCE(SUM(total_inserted_chars), 0) as total_inserted_chars,
                   COALESCE(SUM(total_deleted_chars), 0) as total_deleted_chars
           FROM writing_sessions{where_sql}
           GROUP BY note_path
           ORDER BY total_doc_changes DESC, session_count DESC
           LIMIT ?""",
        args + [top_n],
    ).fetchall()

    total_inserted = int(agg["total_inserted_chars"] or 0)
    total_deleted = int(agg["total_deleted_chars"] or 0)
    rewrite_ratio = (total_deleted / total_inserted) if total_inserted > 0 else 0.0

    return {
        "since": since,
        "until": until,
        "note_path": note_path,
        "session_count": int(agg["session_count"] or 0),
        "notes_touched": int(agg["notes_touched"] or 0),
        "total_doc_changes": int(agg["total_doc_changes"] or 0),
        "total_selection_changes": int(agg["total_selection_changes"] or 0),
        "total_inserts": int(agg["total_inserts"] or 0),
        "total_deletes": int(agg["total_deletes"] or 0),
        "total_inserted_chars": total_inserted,
        "total_deleted_chars": total_deleted,
        "total_undos": int(agg["total_undos"] or 0),
        "total_pastes": int(agg["total_pastes"] or 0),
        "rewrite_ratio": round(rewrite_ratio, 3),
        "earliest_start": agg["earliest_start"],
        "latest_end": agg["latest_end"],
        "top_notes": [
            {
                "note_path": r["note_path"],
                "session_count": int(r["session_count"] or 0),
                "total_doc_changes": int(r["total_doc_changes"] or 0),
                "total_inserted_chars": int(r["total_inserted_chars"] or 0),
                "total_deleted_chars": int(r["total_deleted_chars"] or 0),
            }
            for r in top_notes
        ],
    }
