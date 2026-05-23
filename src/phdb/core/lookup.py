"""Chunk + source inventory lookups.

Moved here from ``phdb.query`` as part of Phase 1 of the phdb Plugin
Architecture plan. The legacy module re-exports from this location.

Generic lookups that don't depend on a specific source: chunk fetch by
id, source-file inventory, and server diagnostic. ``server_info`` and
``list_sources`` use the typed-table registry for counts.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import urllib.request
from typing import Any

from phdb.core.registry import Registry, default_registry
from phdb.core.search import _count_typed_tables
from phdb.embed_provider import EmbedProvider


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


def list_sources(
    conn: sqlite3.Connection,
    *,
    registry: Registry | None = None,
) -> dict[str, Any]:
    """Inventory of ingested sources with counts."""
    reg = registry or default_registry()
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

    typed_total = _count_typed_tables(conn, registry=reg)

    chunk_count = 0
    vec_count = 0
    thread_count = 0
    with contextlib.suppress(sqlite3.OperationalError):
        r = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()
        if r:
            chunk_count = r[0]
    with contextlib.suppress(sqlite3.OperationalError):
        r = conn.execute("SELECT COUNT(*) FROM doc_vectors").fetchone()
        if r:
            vec_count = r[0]
    with contextlib.suppress(sqlite3.OperationalError):
        r = conn.execute("SELECT COUNT(*) FROM nodes WHERE kind = 'thread'").fetchone()
        if r:
            thread_count = r[0]

    doc_count = 0
    with contextlib.suppress(sqlite3.OperationalError):
        r = conn.execute("SELECT COUNT(*) FROM documents").fetchone()
        if r:
            doc_count = r[0]

    t = {
        "messages": typed_total,
        "chunks": chunk_count,
        "vectors": vec_count,
        "threads": thread_count,
        "documents": doc_count,
    }
    return {
        "totals": t,
        "source_files": [dict(r) for r in sf],
        "chunks_by_table": [dict(r) for r in chunk_stats],
    }


def server_info(
    db_path: str | Any,
    conn: sqlite3.Connection,
    *,
    embed_client: EmbedProvider | None = None,
    registry: Registry | None = None,
) -> dict[str, Any]:
    """Diagnostic: DB location, size, Ollama reachability, corpus counts."""
    from pathlib import Path

    reg = registry or default_registry()
    p = Path(db_path)
    info: dict[str, Any] = {
        "db_path": str(p),
        "db_exists": p.exists(),
        "db_size_bytes": p.stat().st_size if p.exists() else None,
        "ollama_url": embed_client.endpoint if embed_client else None,
        "ollama_model": embed_client.model if embed_client else None,
    }
    try:
        typed_total = _count_typed_tables(conn, registry=reg)
        chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        vec_count = conn.execute("SELECT COUNT(*) FROM doc_vectors").fetchone()[0]
        info["counts"] = {
            "messages": typed_total,
            "chunks": chunk_count,
            "vectors": vec_count,
        }
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
