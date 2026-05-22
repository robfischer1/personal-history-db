"""Embed pipeline — chunk source rows, embed via Ollama, store in chunks + doc_vectors.

Ported from embed_messages.py (322 LOC standalone script).  Requires:
- A migrated DB with typed tables (emails, chat_messages, etc.), chunks, doc_vectors
- Optionally a documents typed table (post-migration 0008)
- A running Ollama instance with the configured model loaded
- The write lock (acquired by the caller, not by this module)
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from phdb.embed_provider import EmbedProvider
from phdb.embed_service import EmbedClient  # noqa: F401 — re-export for backwards compat

# ---- Chunking constants (match legacy embed_messages.py exactly) ----
TARGET_CHUNK_CHARS: int = 2048
OVERLAP_CHARS: int = 200
MIN_CHUNK_CHARS: int = 50

DEFAULT_BATCH_SIZE: int = 32


# ---- Data classes ----


@dataclass
class EmbedStatus:
    """Snapshot of embedding progress."""

    total_eligible: int = 0
    done: int = 0
    pending: int = 0
    chunks_embedded: int = 0
    vectors_stored: int = 0


@dataclass
class EmbedProgress:
    """Running progress during an embed run."""

    messages_done: int = 0
    messages_total: int = 0
    chunks_done: int = 0
    elapsed_s: float = 0.0
    msg_rate: float = 0.0
    chunk_rate: float = 0.0
    pct: float = 0.0
    eta_min: float = 0.0


@dataclass
class EmbedResult:
    """Final result of an embed pipeline run."""

    messages_processed: int = 0
    chunks_embedded: int = 0
    elapsed_s: float = 0.0
    errors: list[str] = field(default_factory=list)


ProgressCallback = Callable[[EmbedProgress], None]


# ---- Chunking ----


def chunk_text(text: str) -> list[str]:
    """Split text into ~TARGET_CHUNK_CHARS chunks with OVERLAP_CHARS overlap.

    Boundary detection priority: paragraph > newline > sentence > space.
    Logic matches legacy embed_messages.py exactly.
    """
    text = text.strip()
    if not text:
        return []
    if len(text) <= TARGET_CHUNK_CHARS:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + TARGET_CHUNK_CHARS, len(text))
        if end < len(text):
            for sep, max_back in [("\n\n", 400), ("\n", 200), (". ", 200), (" ", 100)]:
                idx = text.rfind(sep, start, end)
                if idx != -1 and idx >= end - max_back:
                    end = idx + len(sep)
                    break
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        new_start = end - OVERLAP_CHARS
        if new_start <= start:
            new_start = end
        start = new_start
    return chunks


# ---- Status query ----


def _has_table(conn: sqlite3.Connection, table: str) -> bool:
    """Check if a table exists in the database."""
    r = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return r is not None


def get_embed_status(conn: sqlite3.Connection) -> EmbedStatus:
    """Query the DB for current embedding status counts.  Read-only."""
    _EMBEDDABLE_TABLES = ["emails", "chat_messages", "conversations_messages"]
    n_eligible = 0
    for _etbl in _EMBEDDABLE_TABLES:
        if _has_table(conn, _etbl):
            n_eligible += conn.execute(
                f"SELECT COUNT(*) FROM [{_etbl}] "
                "WHERE is_bulk=0 AND body_text IS NOT NULL AND length(body_text) >= ?",
                (MIN_CHUNK_CHARS,),
            ).fetchone()[0]

    if _has_table(conn, "documents"):
        n_eligible += conn.execute(
            "SELECT COUNT(*) FROM documents "
            "WHERE is_bulk=0 AND body_text IS NOT NULL AND length(body_text) >= ?",
            (MIN_CHUNK_CHARS,),
        ).fetchone()[0]

    if _has_table(conn, "articles"):
        n_eligible += conn.execute(
            "SELECT COUNT(*) FROM articles "
            "WHERE body_text IS NOT NULL AND length(body_text) >= ?",
            (MIN_CHUNK_CHARS,),
        ).fetchone()[0]

    if _has_table(conn, "clippings"):
        n_eligible += conn.execute(
            "SELECT COUNT(*) FROM clippings "
            "WHERE body_text IS NOT NULL AND length(body_text) >= ?",
            (MIN_CHUNK_CHARS,),
        ).fetchone()[0]

    n_done = conn.execute(
        "SELECT COUNT(DISTINCT d.source_id) "
        "FROM chunks d "
        "WHERE d.embedded_at IS NOT NULL",
    ).fetchone()[0]

    n_chunks = conn.execute(
        "SELECT COUNT(*) FROM chunks WHERE embedded_at IS NOT NULL",
    ).fetchone()[0]

    n_vec = conn.execute("SELECT COUNT(*) FROM doc_vectors").fetchone()[0]

    return EmbedStatus(
        total_eligible=n_eligible,
        done=n_done,
        pending=max(0, n_eligible - n_done),
        chunks_embedded=n_chunks,
        vectors_stored=n_vec,
    )


# ---- Pipeline ----

def _pending_sql(table: str) -> str:
    """Generate pending-embed SQL for a typed communication table."""
    return f"""\
SELECT m.id, m.subject, m.body_text, m.sender_address, m.date_sent
  FROM [{table}] m
 WHERE m.is_bulk = 0
   AND m.body_text IS NOT NULL
   AND length(m.body_text) >= ?
   AND NOT EXISTS (
       SELECT 1 FROM chunks d
        WHERE d.source_table = '{table}'
          AND d.source_id = m.id
          AND d.embedded_at IS NOT NULL
   )
 ORDER BY m.id
"""


_PENDING_DOCUMENTS_SQL = """\
SELECT doc.id, doc.subject, doc.body_text, doc.bucket, doc.mtime
  FROM documents doc
 WHERE doc.is_bulk = 0
   AND doc.body_text IS NOT NULL
   AND length(doc.body_text) >= ?
   AND NOT EXISTS (
       SELECT 1 FROM chunks d
        WHERE d.source_table = 'documents'
          AND d.source_id = doc.id
          AND d.embedded_at IS NOT NULL
   )
 ORDER BY doc.id
"""

_PENDING_ARTICLES_SQL = """\
SELECT a.id, a.subject, a.body_text, a.bucket, a.mtime
  FROM articles a
 WHERE a.body_text IS NOT NULL
   AND length(a.body_text) >= ?
   AND NOT EXISTS (
       SELECT 1 FROM chunks d
        WHERE d.source_table = 'articles'
          AND d.source_id = a.id
          AND d.embedded_at IS NOT NULL
   )
 ORDER BY a.id
"""

_PENDING_CLIPPINGS_SQL = """\
SELECT c.id, c.subject, c.body_text, c.bucket, c.mtime
  FROM clippings c
 WHERE c.body_text IS NOT NULL
   AND length(c.body_text) >= ?
   AND NOT EXISTS (
       SELECT 1 FROM chunks d
        WHERE d.source_table = 'clippings'
          AND d.source_id = c.id
          AND d.embedded_at IS NOT NULL
   )
 ORDER BY c.id
"""

_UPSERT_CHUNK_SQL = """\
INSERT INTO chunks
  (schema_type, source_table, source_id, chunk_index, chunk_strategy,
   title, content, content_hash, metadata_json,
   embedding_model, embedded_at)
VALUES
  (?, ?, ?, ?, 'message_body_512tok',
   ?, ?, ?, ?, ?, ?)
ON CONFLICT(source_table, source_id, chunk_index) DO UPDATE SET
  content = excluded.content,
  content_hash = excluded.content_hash,
  embedding_model = excluded.embedding_model,
  embedded_at = excluded.embedded_at
RETURNING id
"""


def run_embed_pipeline(
    conn: sqlite3.Connection,
    client: EmbedProvider,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
    dry_run: bool = False,
    progress_cb: ProgressCallback | None = None,
) -> EmbedResult:
    """Embed pending rows from typed tables + documents into chunks + doc_vectors.

    The caller must hold the write lock and manage the connection.
    """
    t_start = time.time()
    n_rows_done = 0
    n_chunks_done = 0
    errors: list[str] = []

    sources: list[tuple[str, str, str]] = [
        (_pending_sql("emails"), "emails", "EmailMessage"),
        (_pending_sql("chat_messages"), "chat_messages", "Message"),
        (_pending_sql("conversations_messages"), "conversations_messages", "Conversation"),
    ]
    if _has_table(conn, "documents"):
        sources.append((_PENDING_DOCUMENTS_SQL, "documents", "DigitalDocument"))
    if _has_table(conn, "articles"):
        sources.append((_PENDING_ARTICLES_SQL, "articles", "Article"))
    if _has_table(conn, "clippings"):
        sources.append((_PENDING_CLIPPINGS_SQL, "clippings", "Quotation"))

    buf: list[tuple[str, str, int, int, str, str, str, str]] = []

    def _flush(
        batch: list[tuple[str, str, int, int, str, str, str, str]],
    ) -> None:
        nonlocal n_chunks_done
        texts = [b[4] for b in batch]
        embeddings = client.embed_batch(texts)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with conn:
            for (schema_type, source_table, row_id, cidx, content, chash, title, meta), emb in zip(
                batch, embeddings, strict=True
            ):
                cur = conn.execute(
                    _UPSERT_CHUNK_SQL,
                    (schema_type, source_table, row_id, cidx,
                     title, content, chash, meta, client.model, now),
                )
                doc_id = cur.fetchone()[0]
                vec_blob = struct.pack(f"{client.dim}f", *emb)
                conn.execute("DELETE FROM doc_vectors WHERE rowid = ?", (doc_id,))
                conn.execute(
                    "INSERT INTO doc_vectors (rowid, embedding) VALUES (?, ?)",
                    (doc_id, vec_blob),
                )
        n_chunks_done += len(batch)

    total_pending = 0
    all_pending: list[tuple[str, str, list[sqlite3.Row]]] = []
    for pending_sql, source_table, schema_type in sources:
        query = pending_sql
        if limit is not None:
            remaining = limit - total_pending
            if remaining <= 0:
                break
            query += f" LIMIT {remaining}"
        rows = conn.execute(query, (MIN_CHUNK_CHARS,)).fetchall()
        if rows:
            all_pending.append((source_table, schema_type, rows))
            total_pending += len(rows)

    if not total_pending:
        return EmbedResult()

    for source_table, schema_type, pending in all_pending:
        for row in pending:
            row_id = row[0]
            subject = row[1]
            body_text = row[2]
            meta_col3 = row[3]
            meta_col4 = row[4]

            chunks = chunk_text(body_text)
            if not chunks:
                n_rows_done += 1
                continue

            title = (subject or "(no subject)")[:160]
            if source_table in ("emails", "chat_messages", "conversations_messages"):
                meta = json.dumps({"sender": meta_col3, "date_sent": meta_col4})
            else:
                meta = json.dumps({"bucket": meta_col3, "mtime": meta_col4})

            if dry_run:
                n_chunks_done += len(chunks)
            else:
                for cidx, chunk_content in enumerate(chunks):
                    chash = hashlib.sha256(chunk_content.encode("utf-8")).hexdigest()
                    buf.append((schema_type, source_table, row_id, cidx,
                                chunk_content, chash, title, meta))

                while len(buf) >= batch_size:
                    _flush(buf[:batch_size])
                    buf = buf[batch_size:]

            n_rows_done += 1

            if n_rows_done % 100 == 0 and progress_cb is not None:
                el = time.time() - t_start
                mrate = n_rows_done / el if el else 0
                crate = n_chunks_done / el if el else 0
                pct = 100 * n_rows_done / total_pending
                eta = ((total_pending - n_rows_done) / mrate / 60) if mrate else float("inf")
                progress_cb(
                    EmbedProgress(
                        messages_done=n_rows_done,
                        messages_total=total_pending,
                        chunks_done=n_chunks_done,
                        elapsed_s=el,
                        msg_rate=mrate,
                        chunk_rate=crate,
                        pct=pct,
                        eta_min=eta,
                    )
                )

    if buf:
        _flush(buf)

    elapsed = time.time() - t_start
    return EmbedResult(
        messages_processed=n_rows_done,
        chunks_embedded=n_chunks_done,
        elapsed_s=elapsed,
        errors=errors,
    )
