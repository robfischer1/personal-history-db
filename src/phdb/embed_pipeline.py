"""Embed pipeline — chunk messages, embed via Ollama, store in documents + doc_vectors.

Ported from embed_messages.py (322 LOC standalone script).  Requires:
- A migrated DB with messages, documents, doc_vectors tables
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

from phdb.embed_service import EmbedClient

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


def get_embed_status(conn: sqlite3.Connection) -> EmbedStatus:
    """Query the DB for current embedding status counts.  Read-only."""
    n_msg = conn.execute(
        "SELECT COUNT(*) FROM messages "
        "WHERE is_bulk=0 AND body_text IS NOT NULL AND length(body_text) >= ?",
        (MIN_CHUNK_CHARS,),
    ).fetchone()[0]

    n_done = conn.execute(
        "SELECT COUNT(DISTINCT m.id) "
        "FROM messages m "
        "JOIN documents d ON d.source_table='messages' AND d.source_id=m.id "
        "WHERE m.is_bulk=0 AND d.embedded_at IS NOT NULL",
    ).fetchone()[0]

    n_chunks = conn.execute(
        "SELECT COUNT(*) FROM documents WHERE embedded_at IS NOT NULL",
    ).fetchone()[0]

    n_vec = conn.execute("SELECT COUNT(*) FROM doc_vectors").fetchone()[0]

    return EmbedStatus(
        total_eligible=n_msg,
        done=n_done,
        pending=n_msg - n_done,
        chunks_embedded=n_chunks,
        vectors_stored=n_vec,
    )


# ---- Pipeline ----

_PENDING_SQL = """\
SELECT m.id, m.subject, m.body_text, m.sender_address, m.date_sent
  FROM messages m
 WHERE m.is_bulk = 0
   AND m.body_text IS NOT NULL
   AND length(m.body_text) >= ?
   AND NOT EXISTS (
       SELECT 1 FROM documents d
        WHERE d.source_table = 'messages'
          AND d.source_id = m.id
          AND d.embedded_at IS NOT NULL
   )
 ORDER BY m.id
"""

_UPSERT_DOC_SQL = """\
INSERT INTO documents
  (schema_type, source_table, source_id, chunk_index, chunk_strategy,
   title, content, content_hash, metadata_json,
   embedding_model, embedded_at)
VALUES
  ('EmailMessage', 'messages', ?, ?, 'message_body_512tok',
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
    client: EmbedClient,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    limit: int | None = None,
    dry_run: bool = False,
    progress_cb: ProgressCallback | None = None,
) -> EmbedResult:
    """Embed pending messages into documents + doc_vectors.

    The caller must hold the write lock and manage the connection.
    """
    query = _PENDING_SQL
    if limit is not None:
        query += f" LIMIT {limit}"
    pending = conn.execute(query, (MIN_CHUNK_CHARS,)).fetchall()

    if not pending:
        return EmbedResult()

    t_start = time.time()
    n_msgs_done = 0
    n_chunks_done = 0
    errors: list[str] = []

    # Buffer: (msg_id, chunk_idx, content, content_hash, title, metadata_json)
    buf: list[tuple[int, int, str, str, str, str]] = []

    def _flush(batch: list[tuple[int, int, str, str, str, str]]) -> None:
        nonlocal n_chunks_done
        texts = [b[2] for b in batch]
        embeddings = client.embed_batch(texts)
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        with conn:
            for (msg_id, cidx, content, chash, title, meta), emb in zip(
                batch, embeddings, strict=True
            ):
                cur = conn.execute(
                    _UPSERT_DOC_SQL,
                    (msg_id, cidx, title, content, chash, meta, client.model, now),
                )
                doc_id = cur.fetchone()[0]
                vec_blob = struct.pack(f"{client.dim}f", *emb)
                conn.execute("DELETE FROM doc_vectors WHERE rowid = ?", (doc_id,))
                conn.execute(
                    "INSERT INTO doc_vectors (rowid, embedding) VALUES (?, ?)",
                    (doc_id, vec_blob),
                )
        n_chunks_done += len(batch)

    for row in pending:
        msg_id, subject, body_text, sender, date_sent = (
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
        )
        chunks = chunk_text(body_text)
        if not chunks:
            n_msgs_done += 1
            continue

        title = (subject or "(no subject)")[:160]
        meta = json.dumps({"sender": sender, "date_sent": date_sent})

        if dry_run:
            n_chunks_done += len(chunks)
        else:
            for cidx, chunk_content in enumerate(chunks):
                chash = hashlib.sha256(chunk_content.encode("utf-8")).hexdigest()
                buf.append((msg_id, cidx, chunk_content, chash, title, meta))

            while len(buf) >= batch_size:
                _flush(buf[:batch_size])
                buf = buf[batch_size:]

        n_msgs_done += 1

        if n_msgs_done % 100 == 0 and progress_cb is not None:
            el = time.time() - t_start
            mrate = n_msgs_done / el if el else 0
            crate = n_chunks_done / el if el else 0
            pct = 100 * n_msgs_done / len(pending)
            eta = ((len(pending) - n_msgs_done) / mrate / 60) if mrate else float("inf")
            progress_cb(
                EmbedProgress(
                    messages_done=n_msgs_done,
                    messages_total=len(pending),
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
        messages_processed=n_msgs_done,
        chunks_embedded=n_chunks_done,
        elapsed_s=elapsed,
        errors=errors,
    )
