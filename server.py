#!/usr/bin/env python3
"""MCP server for personal-history-db.

Thin wrapper around ``phdb.query`` — each MCP tool delegates to a single
query-module function. All retrieval logic lives in the framework; this file
only handles MCP plumbing, connection lifecycle, and config resolution.

Config resolution (highest priority first):
    PHDB_DB_PATH          explicit path to .db file
    PHDB_INSTANCE_DIR     instance config dir (loads Settings from TOML)
    PERSONAL_HISTORY_DB   legacy env var (still honored)
    ./personal-history.db dev fallback

Embedding config comes from instance TOML when PHDB_INSTANCE_DIR is set,
with OLLAMA_URL / OLLAMA_MODEL env vars as overrides.

    DEFAULT_SINCE         baseline date filter for hybrid search (default: 2018)
"""
from __future__ import annotations

import contextlib
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

# When running `python server.py` from a checkout (not pip-installed),
# the src/ dir needs to be on sys.path for `from phdb.xxx` imports.
_src = Path(__file__).resolve().parent / "src"
if str(_src) not in sys.path:
    sys.path.insert(0, str(_src))

from phdb.db import connect_persistent  # noqa: E402, I001
from phdb.embed_service import EmbedClient  # noqa: E402
from phdb.query import (  # noqa: E402
    corpus_stats as _corpus_stats,
    find_messages_by_participant as _find_messages_by_participant,
    find_threads_by_subject as _find_threads_by_subject,
    get_chunk as _get_chunk,
    get_message as _get_message,
    get_thread as _get_thread,
    list_sources as _list_sources,
    nearest_neighbors as _nearest_neighbors,
    search as _search,
    server_info as _server_info,
    top_correspondents as _top_correspondents,
)
from phdb.scoring import record_engagement  # noqa: E402


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def _resolve_config() -> tuple[Path, str, str]:
    """Resolve DB path and embedding config via priority chain."""
    db_path: Path | None = None
    embed_url = os.environ.get("OLLAMA_URL", "").rstrip("/") or None
    embed_model = os.environ.get("OLLAMA_MODEL") or None

    if p := os.environ.get("PHDB_DB_PATH"):
        db_path = Path(p)

    if inst := os.environ.get("PHDB_INSTANCE_DIR"):
        from phdb.settings import Settings
        settings = Settings.load(instance_dir=inst, db_path=db_path)
        if db_path is None:
            db_path = settings.db_path
        if embed_url is None:
            embed_url = settings.embedding.endpoint.rstrip("/")
        if embed_model is None:
            embed_model = settings.embedding.model

    if db_path is None and (p := os.environ.get("PERSONAL_HISTORY_DB")):
        db_path = Path(p)

    return (
        db_path or Path("personal-history.db"),
        embed_url or "http://localhost:11434",
        embed_model or "nomic-embed-text",
    )


DB_PATH, OLLAMA_URL, OLLAMA_MODEL = _resolve_config()
DEFAULT_SINCE = os.environ.get("DEFAULT_SINCE", "2018")

# ---------------------------------------------------------------------------
# Shared state (lazy-init)
# ---------------------------------------------------------------------------
_conn = None
_embed: EmbedClient | None = None


def _get_conn():
    global _conn
    if _conn is None:
        if not DB_PATH.exists():
            raise FileNotFoundError(
                f"Personal History DB not found at {DB_PATH}. "
                f"Set PHDB_DB_PATH or PHDB_INSTANCE_DIR env var."
            )
        _conn = connect_persistent(DB_PATH, load_vec=True)
    return _conn


def _get_embed() -> EmbedClient:
    global _embed
    if _embed is None:
        _embed = EmbedClient(endpoint=OLLAMA_URL, model=OLLAMA_MODEL)
    return _embed


# ---------------------------------------------------------------------------
# MCP server + tools
# ---------------------------------------------------------------------------
mcp = FastMCP("personal-history-db")


@mcp.tool()
def search(
    query: str,
    k: int = 10,
    since: str | None = None,
    until: str | None = None,
    mode: str = "hybrid",
    include_bulk: bool = False,
    include_meta: bool = False,
) -> dict[str, Any]:
    """Hybrid retrieval over Rob's personal history corpus.

    Combines vec0 semantic search (Ollama nomic-embed-text) with FTS5 keyword
    search and fuses with reciprocal-rank fusion. Returns top-k chunks with
    parent message metadata (sender, date, subject, thread).

    Args:
        query: Natural-language query.
        k: Number of fused results to return (default 10).
        since: Lower date bound, "YYYY", "YYYY-MM", or "YYYY-MM-DD". If None,
               defaults to DEFAULT_SINCE env (2018) for hybrid mode to suppress
               the 2003 forum corpus skew. Pass "1900" to disable.
        until: Upper date bound, same format.
        mode: "hybrid" (default), "semantic", or "fts".
        include_bulk: If False (default), filters out is_bulk=1 messages
                      (mailing lists, no-reply, etc.) from results.
        include_meta: If False (default), filters out AI session meta-turns
                      (tool_use, tool_result, sidechain) — only kind='message'
                      and non-AI rows are returned. Pass True to include all
                      turn kinds for debugging session content.

    Returns:
        {"query": str, "mode": str, "since": str, "until": str | None,
         "fts_mode": str, "n_semantic": int, "n_fts": int,
         "results": [{doc_id, msg_id, date, sender, subject, thread_id,
                      snippet, kind, ...}]}
    """
    effective_since = since
    if effective_since is None and mode == "hybrid":
        effective_since = DEFAULT_SINCE

    embed_client = _get_embed() if mode in ("hybrid", "semantic") else None

    return _search(
        _get_conn(),
        query,
        embed_client=embed_client,
        k=k,
        per_source_k=max(k * 5, 50),
        since=effective_since,
        until=until,
        mode=mode,
        include_bulk=include_bulk,
        include_meta=include_meta,
    )


@mcp.tool()
def get_message(msg_id: int, include_recipients: bool = True,
                include_attachments: bool = True) -> dict[str, Any]:
    """Fetch a full message by its messages.id, with body and metadata.

    Args:
        msg_id: messages.id integer.
        include_recipients: If True, include to/cc/bcc list.
        include_attachments: If True, include attachment metadata.

    Returns full row + (optional) recipients + attachments.
    """
    return _get_message(
        _get_conn(), msg_id,
        include_recipients=include_recipients,
        include_attachments=include_attachments,
    )


@mcp.tool()
def get_chunk(doc_id: int) -> dict[str, Any]:
    """Fetch the full content of a document chunk by its documents.id.

    Use after `search` returns a snippet you want to read in full.
    Records an engagement event (boosts future retrieval weight).
    """
    conn = _get_conn()
    result = _get_chunk(conn, doc_id)
    if "error" not in result:
        with contextlib.suppress(Exception):
            record_engagement(conn, doc_id, "read", source="mcp")
    return result


@mcp.tool()
def get_thread(thread_id: str | None = None,
               msg_id: int | None = None,
               max_messages: int = 50) -> dict[str, Any]:
    """Fetch all messages in a Gmail thread, ordered by date.

    Provide either thread_id (gmail_thread_id) OR msg_id (we'll resolve its
    thread). Returns lightweight rows (no full body) — use get_message to
    drill into a specific one.
    """
    return _get_thread(
        _get_conn(),
        thread_id=thread_id,
        msg_id=msg_id,
        max_messages=max_messages,
    )


@mcp.tool()
def list_sources() -> dict[str, Any]:
    """Inventory of what's been ingested into the corpus.

    Returns counts grouped by source organization, file kind, and document
    source_table — useful for understanding what queries can hit and what
    can't.
    """
    return _list_sources(_get_conn())


@mcp.tool()
def corpus_stats(since: str | None = None, until: str | None = None) -> dict[str, Any]:
    """Year distribution + direction/sender breakdowns of the messages table.

    Helps diagnose corpus skew (e.g. the 2003 forum dump dominating chunks).
    """
    return _corpus_stats(_get_conn(), since=since, until=until)


@mcp.tool()
def nearest_neighbors(doc_id: int, k: int = 10) -> dict[str, Any]:
    """Find documents semantically similar to a given chunk.

    Pulls the chunk's embedding and queries vec0 for nearest neighbors.
    Useful for "more like this" exploration.
    """
    return _nearest_neighbors(_get_conn(), doc_id, k=k)


@mcp.tool()
def server_info() -> dict[str, Any]:
    """Diagnostic: where is the DB, what's its size, is Ollama reachable, what's embedded."""
    info = _server_info(DB_PATH, _get_conn(), embed_client=_get_embed())
    info["default_since"] = DEFAULT_SINCE
    return info


@mcp.tool()
def find_messages_by_participant(
    participant: str,
    role: str = "any",
    direction: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 50,
    include_bulk: bool = False,
) -> dict[str, Any]:
    """Find messages where a specific person appears as sender, recipient, or either.

    Use this when the question is about a person rather than a topic — e.g.
    "everything with Erica", "messages to/from sean@", "what did Mom send me
    last year". For topic queries about that person, use `search` and let the
    name be one of the query tokens.

    Args:
        participant: Substring matched case-insensitively against
            sender_address, sender_name, recipients.address, and recipients.name.
            Pass an email fragment ("ashleigh@") or a name fragment ("Erica").
            Avoid single common words.
        role: "sender" (only as From), "recipient" (only as To/Cc/Bcc), or
            "any" (default).
        direction: Optional filter on messages.direction
            ("inbound", "outbound", "self", "unknown").
        since: Lower date bound, "YYYY", "YYYY-MM", or "YYYY-MM-DD".
        until: Upper date bound, same format.
        limit: Max messages returned (default 50).
        include_bulk: If False (default), filters out is_bulk=1.

    Returns:
        {"participant": ..., "role": ..., "match_count": int,
         "messages": [{msg_id, date, direction, sender, sender_address,
                       subject, thread_id, is_bulk, matched_via}]}
    """
    return _find_messages_by_participant(
        _get_conn(), participant,
        role=role, direction=direction, since=since, until=until,
        limit=limit, include_bulk=include_bulk,
    )


@mcp.tool()
def find_threads(
    query: str,
    since: str | None = None,
    until: str | None = None,
    limit: int = 30,
) -> dict[str, Any]:
    """Find conversation threads by canonical subject line.

    Use this when you remember a thread's title or topic phrase but not its
    contents. For content-based finding, use `search` and call `get_thread`
    on a hit's msg_id.

    Args:
        query: Substring (case-insensitive) matched against
            threads.subject_canonical (Re:/Fwd: stripped at thread-build time).
        since: Filter on threads.date_last >= since.
        until: Filter on threads.date_first <= until.
        limit: Max threads returned (default 30).

    Returns:
        {"query": ..., "match_count": int,
         "threads": [{thread_db_id, thread_id, subject, message_count,
                      date_first, date_last, participants}]}
    """
    return _find_threads_by_subject(
        _get_conn(), query,
        since=since, until=until, limit=limit,
    )


@mcp.tool()
def top_correspondents(
    since: str | None = None,
    until: str | None = None,
    role: str = "sender",
    limit: int = 20,
    exclude_bulk: bool = True,
    exclude_self: bool = True,
) -> dict[str, Any]:
    """Most-frequent correspondents in a date window.

    Useful for relationship and era mapping ("who was I emailing in 2019",
    "top contacts since I started Amazon"). For an individual person's
    activity over time, use `find_messages_by_participant` instead.

    Args:
        since: Lower date bound on messages.date_sent.
        until: Upper date bound on messages.date_sent.
        role: "sender" (top senders, default), "recipient" (top recipients of
            Rob's outbound), or "both" (combined frequency).
        limit: Top N (default 20).
        exclude_bulk: Drop is_bulk=1 (mailing lists, no-reply) — default True.
        exclude_self: Drop direction='self' (Discord/SMS where Rob is both
            sides; usually noise for relationship view) — default True.

    Returns:
        {"role": ..., "since": ..., "until": ...,
         "correspondents": [{address, name, message_count}]}
    """
    return _top_correspondents(
        _get_conn(),
        since=since, until=until, role=role, limit=limit,
        exclude_bulk=exclude_bulk, exclude_self=exclude_self,
    )


@mcp.tool()
def log_engagement(
    chunk_id: int,
    event_type: str = "cite",
    source: str | None = None,
) -> dict[str, Any]:
    """Record an explicit engagement event for a chunk.

    This boosts the chunk's future retrieval weight via the decay scoring
    system. Call when a chunk is explicitly referenced, cited, backlinkd to,
    or otherwise acted upon outside of a direct search result expansion.

    Args:
        chunk_id: The chunks.id to engage with.
        event_type: Category of engagement ("read", "cite", "backlink",
            "promote"). Default: "cite".
        source: Optional source identifier (e.g., "vault-mcp", "manual").

    Returns:
        {"status": "ok", "chunk_id": int, "event_type": str}
    """
    conn = _get_conn()
    with contextlib.suppress(Exception):
        record_engagement(conn, chunk_id, event_type, source=source)
    return {"status": "ok", "chunk_id": chunk_id, "event_type": event_type}


def main() -> None:
    if not DB_PATH.exists():
        print(f"WARNING: DB not found at {DB_PATH}", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
