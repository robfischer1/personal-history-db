"""Unified query layer — hybrid retrieval, lookups, discovery, people queries.

All functions take a ``sqlite3.Connection`` as their first argument.
The module is stateless; callers own connection lifecycle.

Hybrid retrieval combines:
- vec0 semantic search (Ollama nomic-embed-text, 768-dim)
- FTS5 keyword search with stopword filtering + AND→OR fallback
- Reciprocal-rank fusion (RRF, K=60)
- Optional per-year IDF normalization to counter corpus skew
"""

from __future__ import annotations

import contextlib
import json
import re
import sqlite3
import urllib.request
from typing import Any

from phdb.embed_provider import EmbedProvider
from phdb.embed_service import EmbedClient  # noqa: F401 — re-export for backwards compat

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
RRF_K = 60
DATE_FILTER_OVERSAMPLE = 6

FTS_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "do", "did", "does", "for", "from", "had", "has", "have", "he", "her", "him",
    "his", "i", "in", "is", "it", "its", "me", "my", "of", "on", "or", "our",
    "she", "so", "that", "the", "their", "them", "they", "this", "those", "to",
    "was", "we", "were", "what", "when", "where", "which", "who", "why", "will",
    "with", "you", "your", "about", "into", "then", "than", "there", "over",
    "under", "before", "after", "just", "like", "not", "no", "yes",
}


# ---------------------------------------------------------------------------
# FTS query building
# ---------------------------------------------------------------------------
def build_fts_query(query: str, op: str = "AND") -> str:
    """Convert natural-language text to an FTS5 expression.

    Double-quoted substrings are preserved as FTS5 phrase queries.
    Remaining tokens are stripped of stopwords and joined with *op*.
    Returns ``""`` if nothing usable remains.
    """
    phrases = re.findall(r'"([^"]+)"', query)
    remainder = re.sub(r'"[^"]*"', "", query).replace("'", "")
    terms = [
        t for t in remainder.split()
        if t.isalnum() and t.lower() not in FTS_STOPWORDS
    ]
    parts = [f'"{p}"' for p in phrases if p.strip()] + terms
    return f" {op} ".join(parts)


# ---------------------------------------------------------------------------
# RRF fusion
# ---------------------------------------------------------------------------
def rrf_fuse(*ranked_lists: list[tuple[int, float, int]]) -> list[tuple[int, float]]:
    """Reciprocal-rank fusion: score = sum(1 / (K + rank))."""
    scores: dict[int, float] = {}
    for ranked in ranked_lists:
        for doc_id, _, rank in ranked:
            scores[doc_id] = scores.get(doc_id, 0.0) + 1.0 / (RRF_K + rank)
    return sorted(scores.items(), key=lambda x: -x[1])


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _date_filter_ids(
    conn: sqlite3.Connection,
    ids: list[int],
    since: str | None,
    until: str | None,
) -> set[int]:
    """Filter chunk IDs by the date of their parent row (messages or documents)."""
    if not (since or until) or not ids:
        return set(ids)
    placeholders = ",".join("?" * len(ids))
    args: list[Any] = list(ids)
    clauses: list[str] = []
    if since:
        clauses.append("substr(COALESCE(m.date_sent, doc.mtime), 1, 10) >= ?")
        args.append(since)
    if until:
        clauses.append("substr(COALESCE(m.date_sent, doc.mtime), 1, 10) <= ?")
        args.append(until)
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT d.id AS doc_id FROM chunks d"
        f" LEFT JOIN messages m ON m.id = d.source_id AND d.source_table = 'messages'"
        f" LEFT JOIN documents doc ON doc.id = d.source_id AND d.source_table = 'documents'"
        f" WHERE d.id IN ({placeholders}) AND {where}",
        args,
    ).fetchall()
    return {r["doc_id"] if isinstance(r, sqlite3.Row) else r[0] for r in rows}


def _semantic_search(
    conn: sqlite3.Connection,
    query_blob: bytes,
    k: int,
    since: str | None = None,
    until: str | None = None,
) -> list[tuple[int, float, int]]:
    fetch_k = k * DATE_FILTER_OVERSAMPLE if (since or until) else k
    rows = conn.execute(
        "SELECT rowid AS doc_id, distance FROM doc_vectors"
        " WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (query_blob, fetch_k),
    ).fetchall()
    if since or until:
        keep = _date_filter_ids(conn, [r[0] for r in rows], since, until)
        rows = [r for r in rows if r[0] in keep][:k]
    return [(r[0], r[1], i + 1) for i, r in enumerate(rows)]


def _fts_run(
    conn: sqlite3.Connection, fts_q: str, k: int
) -> list[sqlite3.Row]:
    if not fts_q:
        return []
    try:
        return conn.execute(
            "SELECT rowid AS doc_id, bm25(doc_fts) AS score"
            " FROM doc_fts WHERE doc_fts MATCH ? ORDER BY score LIMIT ?",
            (fts_q, k),
        ).fetchall()
    except sqlite3.OperationalError:
        return []


def _fts_search(
    conn: sqlite3.Connection,
    query: str,
    k: int,
    since: str | None = None,
    until: str | None = None,
) -> tuple[list[tuple[int, float, int]], str]:
    """FTS5 search with AND→OR fallback. Returns (ranked_list, mode_label)."""
    fetch_k = k * DATE_FILTER_OVERSAMPLE if (since or until) else k

    and_q = build_fts_query(query, op="AND")
    rows = _fts_run(conn, and_q, fetch_k)
    if since or until:
        keep = _date_filter_ids(conn, [r[0] for r in rows], since, until)
        rows = [r for r in rows if r[0] in keep]
    mode = f"AND ({len(rows)} hits)"

    if len(rows) < max(5, k // 4):
        or_q = build_fts_query(query, op="OR")
        rows = _fts_run(conn, or_q, fetch_k)
        if since or until:
            keep = _date_filter_ids(conn, [r[0] for r in rows], since, until)
            rows = [r for r in rows if r[0] in keep]
        mode = f"OR ({len(rows)} hits)"

    rows = rows[:k]
    return [(r[0], r[1], i + 1) for i, r in enumerate(rows)], mode


def _hydrate(
    conn: sqlite3.Connection,
    doc_ids: list[int],
    snippet_chars: int = 240,
) -> list[dict[str, Any]]:
    """Pull chunk + parent row metadata for a list of chunk IDs."""
    if not doc_ids:
        return []
    placeholders = ",".join(["?"] * len(doc_ids))
    rows = conn.execute(
        f"SELECT d.id AS doc_id, d.source_table, d.source_id, d.title, d.content,"
        f" d.chunk_index, d.schema_type AS doc_schema_type,"
        f" COALESCE(m.id, doc.id) AS source_row_id,"
        f" COALESCE(m.subject, doc.subject) AS subject,"
        f" m.sender_address,"
        f" COALESCE(m.sender_name, doc.bucket) AS sender_name,"
        f" COALESCE(m.date_sent, doc.mtime) AS date_sent,"
        f" m.direction,"
        f" m.gmail_thread_id,"
        f" COALESCE(m.is_bulk, doc.is_bulk) AS is_bulk,"
        f" m.kind,"
        f" doc.file_path, doc.bucket"
        f" FROM chunks d"
        f" LEFT JOIN messages m ON m.id = d.source_id AND d.source_table = 'messages'"
        f" LEFT JOIN documents doc ON doc.id = d.source_id AND d.source_table = 'documents'"
        f" WHERE d.id IN ({placeholders})",
        doc_ids,
    ).fetchall()
    by_id = {r[0]: r for r in rows}
    out: list[dict[str, Any]] = []
    for did in doc_ids:
        r = by_id.get(did)
        if r is None:
            continue

        def _g(row: Any, key: str, idx: int) -> Any:
            try:
                return row[key]
            except (IndexError, KeyError):
                return row[idx]

        content = _g(r, "content", 4) or ""
        sender_name = _g(r, "sender_name", 10)
        sender_addr = _g(r, "sender_address", 9)
        date_sent = _g(r, "date_sent", 11) or ""
        is_bulk_raw = _g(r, "is_bulk", 14)
        out.append({
            "doc_id": _g(r, "doc_id", 0),
            "source_table": _g(r, "source_table", 1),
            "source_id": _g(r, "source_id", 2),
            "schema_type": _g(r, "doc_schema_type", 6),
            "title": _g(r, "title", 3),
            "chunk_index": _g(r, "chunk_index", 5),
            "snippet": content.replace("\n", " ").strip()[:snippet_chars],
            "msg_id": _g(r, "source_row_id", 7),
            "subject": _g(r, "subject", 8),
            "sender": sender_name or sender_addr,
            "sender_address": sender_addr,
            "date": date_sent[:10] or None,
            "direction": _g(r, "direction", 12),
            "thread_id": _g(r, "gmail_thread_id", 13),
            "is_bulk": bool(is_bulk_raw) if is_bulk_raw is not None else None,
            "kind": _g(r, "kind", 15),
            "file_path": _g(r, "file_path", 16),
            "bucket": _g(r, "bucket", 17),
        })
    return out


def _corpus_year_counts(conn: sqlite3.Connection) -> dict[str, int]:
    """Return {year_str: chunk_count} for the entire embedded corpus."""
    rows = conn.execute(
        "SELECT substr(COALESCE(m.date_sent, doc.mtime), 1, 4) AS year, COUNT(*) AS cnt"
        " FROM chunks d"
        " LEFT JOIN messages m ON m.id = d.source_id AND d.source_table = 'messages'"
        " LEFT JOIN documents doc ON doc.id = d.source_id AND d.source_table = 'documents'"
        " WHERE COALESCE(m.date_sent, doc.mtime) IS NOT NULL"
        " AND length(COALESCE(m.date_sent, doc.mtime)) >= 4"
        " GROUP BY year"
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _year_weights(year_counts: dict[str, int]) -> dict[str, float]:
    """Per-year normalization weights. Overrepresented years are penalised."""
    if not year_counts:
        return {}
    total = sum(year_counts.values())
    mean = total / len(year_counts)
    cap = mean * 2
    return {
        year: min(1.0, mean / min(cnt, cap))
        for year, cnt in year_counts.items()
    }


def _lookup_doc_years(
    conn: sqlite3.Connection, doc_ids: list[int]
) -> dict[int, str]:
    if not doc_ids:
        return {}
    placeholders = ",".join(["?"] * len(doc_ids))
    rows = conn.execute(
        f"SELECT d.id AS doc_id, substr(COALESCE(m.date_sent, doc.mtime), 1, 4) AS year"
        f" FROM chunks d"
        f" LEFT JOIN messages m ON m.id = d.source_id AND d.source_table = 'messages'"
        f" LEFT JOIN documents doc ON doc.id = d.source_id AND d.source_table = 'documents'"
        f" WHERE d.id IN ({placeholders})",
        doc_ids,
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _lookup_decay_scores(
    conn: sqlite3.Connection, doc_ids: list[int]
) -> dict[int, float]:
    """Fetch decay scores for a set of chunk IDs. Returns {chunk_id: score}.

    Chunks without a score row get 1.0 (no penalty) — graceful degradation
    when chunk_scores is unpopulated or the table doesn't exist yet.
    """
    if not doc_ids:
        return {}
    try:
        placeholders = ",".join(["?"] * len(doc_ids))
        rows = conn.execute(
            f"SELECT chunk_id, score FROM chunk_scores"
            f" WHERE chunk_id IN ({placeholders})",
            doc_ids,
        ).fetchall()
        return {r[0]: r[1] for r in rows}
    except sqlite3.OperationalError:
        return {}


# ===================================================================
# Public API — 11 operations matching MCP tool contracts
# ===================================================================


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    embed_client: EmbedProvider | None = None,
    k: int = 10,
    per_source_k: int = 50,
    since: str | None = None,
    until: str | None = None,
    mode: str = "hybrid",
    include_bulk: bool = False,
    include_meta: bool = False,
    year_normalize: bool = False,
    snippet_chars: int = 240,
) -> dict[str, Any]:
    """Hybrid retrieval — semantic + FTS + RRF fusion.

    Returns dict matching the MCP ``search`` tool contract.
    """
    sem_results: list[tuple[int, float, int]] = []
    fts_results: list[tuple[int, float, int]] = []
    fts_mode_label = "n/a"

    effective_k = max(k * 5, per_source_k)

    if mode in ("hybrid", "semantic"):
        if embed_client is None:
            if mode == "semantic":
                return {"error": "semantic search requires an embed client"}
            # hybrid degrades to FTS-only when no embed client
        else:
            qblob = embed_client.embed(query)
            sem_results = _semantic_search(
                conn, qblob, effective_k, since=since, until=until
            )

    if mode in ("hybrid", "fts"):
        fts_results, fts_mode_label = _fts_search(
            conn, query, effective_k, since=since, until=until
        )

    final: list[tuple[int, float]]
    if mode == "semantic":
        final = [(d, 1.0 / (RRF_K + r)) for d, _, r in sem_results[: k * 2]]
    elif mode == "fts":
        final = [(d, 1.0 / (RRF_K + r)) for d, _, r in fts_results[: k * 2]]
    else:
        final = rrf_fuse(sem_results, fts_results)[: k * 2]

    # Optional year normalization
    if year_normalize and final:
        yr_counts = _corpus_year_counts(conn)
        yr_wts = _year_weights(yr_counts)
        doc_years = _lookup_doc_years(conn, [d for d, _ in final])
        final = [
            (d, score * yr_wts.get(doc_years.get(d, ""), 1.0))
            for d, score in final
        ]
        final.sort(key=lambda x: -x[1])

    # Decay score weighting — multiply relevance by retrieval weight
    decay_scores = _lookup_decay_scores(conn, [d for d, _ in final])
    if decay_scores:
        final = [
            (d, score * decay_scores.get(d, 1.0))
            for d, score in final
        ]
        final.sort(key=lambda x: -x[1])

    rows = _hydrate(conn, [d for d, _ in final], snippet_chars=snippet_chars)
    score_by_id = dict(final)

    if not include_bulk:
        rows = [r for r in rows if not r.get("is_bulk")]

    if not include_meta:
        rows = [r for r in rows if r.get("kind") in (None, "message")]

    rows = rows[:k]
    for r in rows:
        r["score"] = round(score_by_id.get(r["doc_id"], 0.0), 5)
        r["decay_score"] = round(decay_scores.get(r["doc_id"], 1.0), 4)

    return {
        "query": query,
        "mode": mode,
        "since": since,
        "until": until,
        "fts_mode": fts_mode_label,
        "n_semantic": len(sem_results),
        "n_fts": len(fts_results),
        "results": rows,
    }


def get_message(
    conn: sqlite3.Connection,
    msg_id: int,
    *,
    include_recipients: bool = True,
    include_attachments: bool = True,
) -> dict[str, Any]:
    """Fetch a full message by messages.id."""
    row = conn.execute(
        "SELECT id, schema_type, rfc822_message_id, gmail_thread_id, gmail_labels,"
        " subject, sender_address, sender_name, sender_domain, direction,"
        " date_sent, date_received, body_text, body_text_source,"
        " is_multipart, has_attachments, attachment_count, is_bulk,"
        " bulk_signal, source_file_id"
        " FROM messages WHERE id = ?",
        (msg_id,),
    ).fetchone()
    if row is None:
        return {"error": f"No message with id={msg_id}"}
    out = dict(row)
    if include_recipients:
        rs = conn.execute(
            "SELECT address, name, rtype FROM recipients"
            " WHERE message_id = ? ORDER BY rtype, id",
            (msg_id,),
        ).fetchall()
        out["recipients"] = [dict(r) for r in rs]
    if include_attachments and row["attachment_count"]:
        atts = conn.execute(
            "SELECT filename, content_type, size_bytes, on_disk_path"
            " FROM attachments WHERE message_id = ? ORDER BY id",
            (msg_id,),
        ).fetchall()
        out["attachments"] = [dict(a) for a in atts]
    return out


def get_chunk(conn: sqlite3.Connection, doc_id: int) -> dict[str, Any]:
    """Fetch the full content of a chunk by chunks.id."""
    row = conn.execute(
        "SELECT id, schema_type, source_table, source_id, chunk_index,"
        " chunk_strategy, title, content, metadata_json, embedding_model,"
        " embedded_at, created_at"
        " FROM chunks WHERE id = ?",
        (doc_id,),
    ).fetchone()
    if row is None:
        return {"error": f"No chunk with id={doc_id}"}
    out = dict(row)
    if out.get("metadata_json"):
        with contextlib.suppress(Exception):
            out["metadata"] = json.loads(out["metadata_json"])
    return out


def get_thread(
    conn: sqlite3.Connection,
    *,
    thread_id: str | None = None,
    msg_id: int | None = None,
    max_messages: int = 50,
) -> dict[str, Any]:
    """Fetch all messages in a thread, ordered by date."""
    if thread_id is None and msg_id is None:
        return {"error": "provide thread_id or msg_id"}
    if thread_id is None:
        r = conn.execute(
            "SELECT gmail_thread_id FROM messages WHERE id = ?", (msg_id,)
        ).fetchone()
        if r is None or not r["gmail_thread_id"]:
            return {"error": f"msg_id={msg_id} has no gmail_thread_id"}
        thread_id = r["gmail_thread_id"]
    rows = conn.execute(
        "SELECT id AS msg_id, subject, sender_address, sender_name, direction,"
        " date_sent, is_bulk, substr(body_text, 1, 300) AS body_preview"
        " FROM messages WHERE gmail_thread_id = ? ORDER BY date_sent LIMIT ?",
        (thread_id, max_messages),
    ).fetchall()
    return {
        "thread_id": thread_id,
        "message_count": len(rows),
        "messages": [dict(r) for r in rows],
    }


def list_sources(conn: sqlite3.Connection) -> dict[str, Any]:
    """Inventory of ingested sources with counts."""
    sf = conn.execute(
        "SELECT source_org, file_kind, COUNT(*) AS files,"
        " SUM(message_count) AS messages"
        " FROM source_files GROUP BY source_org, file_kind"
        " ORDER BY messages DESC NULLS LAST"
    ).fetchall()
    chunk_stats = conn.execute(
        "SELECT source_table, COUNT(*) AS chunks,"
        " SUM(CASE WHEN embedded_at IS NOT NULL THEN 1 ELSE 0 END) AS embedded"
        " FROM chunks GROUP BY source_table ORDER BY chunks DESC"
    ).fetchall()
    totals = conn.execute(
        "SELECT (SELECT COUNT(*) FROM messages) AS messages,"
        " (SELECT COUNT(*) FROM chunks) AS chunks,"
        " (SELECT COUNT(*) FROM doc_vectors) AS vectors,"
        " (SELECT COUNT(*) FROM threads) AS threads"
    ).fetchone()
    doc_count = 0
    with contextlib.suppress(sqlite3.OperationalError):
        r = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        if r:
            doc_count = r[0]
    t = dict(totals)
    t["documents"] = doc_count
    return {
        "totals": t,
        "source_files": [dict(r) for r in sf],
        "chunks_by_table": [dict(r) for r in chunk_stats],
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

    by_year = conn.execute(
        f"SELECT substr(date_sent, 1, 4) AS year, COUNT(*) AS messages"
        f" FROM messages {where_sql} GROUP BY year ORDER BY year",
        args,
    ).fetchall()
    by_dir = conn.execute(
        f"SELECT direction, COUNT(*) AS n FROM messages {where_sql}"
        f" GROUP BY direction",
        args,
    ).fetchall()
    top_senders = conn.execute(
        f"SELECT sender_address, COUNT(*) AS n FROM messages {where_sql}"
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


def nearest_neighbors(
    conn: sqlite3.Connection,
    doc_id: int,
    *,
    k: int = 10,
) -> dict[str, Any]:
    """Find documents semantically similar to a given chunk."""
    r = conn.execute(
        "SELECT embedding FROM doc_vectors WHERE rowid = ?", (doc_id,)
    ).fetchone()
    if r is None:
        return {"error": f"No vector for doc_id={doc_id} (not embedded?)"}
    blob = r["embedding"] if isinstance(r, sqlite3.Row) else r[0]
    rows = conn.execute(
        "SELECT rowid AS doc_id, distance FROM doc_vectors"
        " WHERE embedding MATCH ? AND k = ? ORDER BY distance",
        (blob, k + 1),
    ).fetchall()
    nbr_ids = [row[0] for row in rows if row[0] != doc_id][:k]
    nbrs = _hydrate(conn, nbr_ids)
    dist_by_id = {row[0]: row[1] for row in rows}
    for n in nbrs:
        n["distance"] = round(dist_by_id.get(n["doc_id"], 0.0), 5)
    return {"seed_doc_id": doc_id, "neighbors": nbrs}


def server_info(
    db_path: str | Any,
    conn: sqlite3.Connection,
    *,
    embed_client: EmbedProvider | None = None,
) -> dict[str, Any]:
    """Diagnostic: DB location, size, Ollama reachability, corpus counts."""
    from pathlib import Path

    p = Path(db_path)
    info: dict[str, Any] = {
        "db_path": str(p),
        "db_exists": p.exists(),
        "db_size_bytes": p.stat().st_size if p.exists() else None,
        "ollama_url": embed_client.endpoint if embed_client else None,
        "ollama_model": embed_client.model if embed_client else None,
    }
    try:
        info["counts"] = dict(
            conn.execute(
                "SELECT (SELECT COUNT(*) FROM messages) AS messages,"
                " (SELECT COUNT(*) FROM chunks) AS chunks,"
                " (SELECT COUNT(*) FROM doc_vectors) AS vectors"
            ).fetchone()
        )
    except Exception as e:
        info["db_error"] = str(e)
    if embed_client:
        try:
            with urllib.request.urlopen(
                f"{embed_client.endpoint}/api/tags", timeout=3
            ) as r:
                info["ollama_reachable"] = r.status == 200
        except Exception as e:
            info["ollama_reachable"] = False
            info["ollama_error"] = str(e)
    return info


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

    selectors: list[tuple[str, list[Any]]] = []
    if role in ("sender", "any"):
        selectors.append((
            "SELECT id AS msg_id, 'sender' AS matched_via FROM messages"
            " WHERE LOWER(sender_address) LIKE ?"
            " OR LOWER(COALESCE(sender_name, '')) LIKE ?",
            [p, p],
        ))
    if role in ("recipient", "any"):
        selectors.append((
            "SELECT message_id AS msg_id, 'recipient' AS matched_via"
            " FROM recipients"
            " WHERE LOWER(address) LIKE ? OR LOWER(COALESCE(name, '')) LIKE ?",
            [p, p],
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
        f"WITH matches AS ({union_sql})"
        f" SELECT m.id AS msg_id, m.date_sent, m.direction,"
        f" m.sender_address, m.sender_name, m.subject,"
        f" m.gmail_thread_id, m.is_bulk,"
        f" (SELECT GROUP_CONCAT(DISTINCT matched_via)"
        f"  FROM matches mt WHERE mt.msg_id = m.id) AS matched_via"
        f" FROM messages m"
        f" WHERE m.id IN (SELECT msg_id FROM matches){where_sql}"
        f" ORDER BY m.date_sent DESC LIMIT ?"
    )
    rows = conn.execute(sql, union_args + where_args + [limit]).fetchall()

    return {
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

    where = ["LOWER(COALESCE(subject_canonical, '')) LIKE ?"]
    args: list[Any] = [q]
    if since:
        where.append("substr(date_last, 1, 10) >= ?")
        args.append(since)
    if until:
        where.append("substr(date_first, 1, 10) <= ?")
        args.append(until)
    where_sql = " AND ".join(where)

    rows = conn.execute(
        f"SELECT id, gmail_thread_id, subject_canonical,"
        f" message_count, date_first, date_last, participants"
        f" FROM threads WHERE {where_sql} ORDER BY date_last DESC LIMIT ?",
        args + [limit],
    ).fetchall()

    out = []
    for r in rows:
        participants: Any = r["participants"]
        if participants:
            with contextlib.suppress(Exception):
                participants = json.loads(participants)
        out.append({
            "thread_db_id": r["id"],
            "thread_id": r["gmail_thread_id"],
            "subject": r["subject_canonical"],
            "message_count": r["message_count"],
            "date_first": r["date_first"],
            "date_last": r["date_last"],
            "participants": participants,
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

    if role == "sender":
        sql = (
            f"SELECT m.sender_address AS address,"
            f" MAX(m.sender_name) AS name, COUNT(*) AS message_count"
            f" FROM messages m {where_sql}"
            f" GROUP BY m.sender_address ORDER BY message_count DESC LIMIT ?"
        )
        rows = conn.execute(sql, args + [limit]).fetchall()
    elif role == "recipient":
        sql = (
            f"SELECT r.address AS address, MAX(r.name) AS name,"
            f" COUNT(*) AS message_count"
            f" FROM recipients r JOIN messages m ON m.id = r.message_id"
            f" {where_sql}"
            f" GROUP BY r.address ORDER BY message_count DESC LIMIT ?"
        )
        rows = conn.execute(sql, args + [limit]).fetchall()
    elif role == "both":
        sql = (
            f"WITH combined AS ("
            f" SELECT m.sender_address AS address, m.sender_name AS name"
            f"  FROM messages m {where_sql}"
            f" UNION ALL"
            f" SELECT r.address AS address, r.name AS name"
            f"  FROM recipients r JOIN messages m ON m.id = r.message_id"
            f"  {where_sql})"
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
    """Corpus-level writing-stream stats with optional date / note_path filters.

    `since` and `until` are 'YYYY-MM-DD' strings interpreted as UTC date bounds.
    `since` is inclusive (>= midnight UTC of that date), `until` is inclusive
    (< midnight UTC of the next day).
    """
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
