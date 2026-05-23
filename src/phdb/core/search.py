"""Hybrid retrieval primitives — semantic + FTS + RRF fusion.

Moved here from ``phdb.query`` as part of Phase 1 of the phdb Plugin
Architecture plan. The legacy module re-exports from this location.

Source-agnostic — operates over the ``chunks`` + ``doc_vectors`` +
``doc_fts`` triple, plus a polymorphic hydrate that LEFT JOINs against
the communication tables and ``documents``. Phase 3 will make the
hydrate dispatch via plugin manifests; the search/RRF/decay surface
stays unchanged.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from phdb.core.registry import Registry, default_registry
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
    """Filter chunk IDs by the date of their parent row."""
    if not (since or until) or not ids:
        return set(ids)
    placeholders = ",".join("?" * len(ids))
    args: list[Any] = list(ids)
    clauses: list[str] = []
    if since:
        clauses.append(
            "substr(COALESCE("
            "json_extract(d.metadata_json, '$.date_sent'), doc.mtime"
            "), 1, 10) >= ?"
        )
        args.append(since)
    if until:
        clauses.append(
            "substr(COALESCE("
            "json_extract(d.metadata_json, '$.date_sent'), doc.mtime"
            "), 1, 10) <= ?"
        )
        args.append(until)
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT d.id AS doc_id FROM chunks d"
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
    """Pull chunk + parent row metadata for a list of chunk IDs.

    Joins against the three communication typed tables (emails,
    chat_messages, conversations_messages) and the documents table to
    resolve parent metadata polymorphically. Phase 3+ will replace this
    with a plugin-dispatched hydrate where each plugin provides its own
    hydration query for its tables; the function signature is stable.
    """
    if not doc_ids:
        return []
    placeholders = ",".join(["?"] * len(doc_ids))
    rows = conn.execute(
        f"SELECT d.id AS doc_id, d.source_table, d.source_id, d.title, d.content,"
        f" d.chunk_index, d.schema_type AS doc_schema_type,"
        f" COALESCE(e.id, cm.id, cv.id, doc.id) AS source_row_id,"
        f" COALESCE(e.subject, cm.subject, cv.subject, doc.subject) AS subject,"
        f" COALESCE(e.sender_address, cm.sender_address, cv.sender_address) AS sender_address,"
        f" COALESCE(e.sender_name, cm.sender_name, cv.sender_name, doc.bucket) AS sender_name,"
        f" COALESCE(e.date_sent, cm.date_sent, cv.date_sent, doc.mtime) AS date_sent,"
        f" COALESCE(e.direction, cm.direction, cv.direction) AS direction,"
        f" e.gmail_thread_id,"
        f" COALESCE(e.is_bulk, cm.is_bulk, cv.is_bulk, doc.is_bulk) AS is_bulk,"
        f" cv.kind,"
        f" doc.file_path, doc.bucket"
        f" FROM chunks d"
        f" LEFT JOIN emails e ON e.id = d.source_id AND d.source_table = 'emails'"
        f" LEFT JOIN chat_messages cm ON cm.id = d.source_id AND d.source_table = 'chat_messages'"
        f" LEFT JOIN conversations_messages cv ON cv.id = d.source_id AND d.source_table = 'conversations_messages'"
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
        "SELECT substr(COALESCE("
        "  json_extract(d.metadata_json, '$.date_sent'), doc.mtime"
        "), 1, 4) AS year, COUNT(*) AS cnt"
        " FROM chunks d"
        " LEFT JOIN documents doc ON doc.id = d.source_id AND d.source_table = 'documents'"
        " WHERE COALESCE(json_extract(d.metadata_json, '$.date_sent'), doc.mtime) IS NOT NULL"
        " AND length(COALESCE(json_extract(d.metadata_json, '$.date_sent'), doc.mtime)) >= 4"
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
        f"SELECT d.id AS doc_id, substr(COALESCE("
        f"  json_extract(d.metadata_json, '$.date_sent'), doc.mtime"
        f"), 1, 4) AS year"
        f" FROM chunks d"
        f" LEFT JOIN documents doc ON doc.id = d.source_id AND d.source_table = 'documents'"
        f" WHERE d.id IN ({placeholders})",
        doc_ids,
    ).fetchall()
    return {r[0]: r[1] for r in rows}


def _lookup_decay_scores(
    conn: sqlite3.Connection, doc_ids: list[int]
) -> dict[int, float]:
    """Fetch decay scores for a set of chunk IDs. Returns {chunk_id: score}."""
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


def _count_typed_tables(
    conn: sqlite3.Connection,
    *,
    registry: Registry | None = None,
) -> int:
    """Sum row counts across all typed tables (replaces COUNT(*) FROM messages)."""
    import contextlib

    reg = registry or default_registry()
    total = 0
    for t in reg.typed_table_names:
        with contextlib.suppress(sqlite3.OperationalError):
            r = conn.execute(f"SELECT COUNT(*) FROM [{t}]").fetchone()
            if r:
                total += r[0]
    return total


# ===================================================================
# Public API
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

    if year_normalize and final:
        yr_counts = _corpus_year_counts(conn)
        yr_wts = _year_weights(yr_counts)
        doc_years = _lookup_doc_years(conn, [d for d, _ in final])
        final = [
            (d, score * yr_wts.get(doc_years.get(d, ""), 1.0))
            for d, score in final
        ]
        final.sort(key=lambda x: -x[1])

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
