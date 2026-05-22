"""Tests for the embed pipeline (chunking → storage orchestration)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from phdb.db import connect, ensure_vec_table
from phdb.embed_pipeline import (
    EmbedProgress,
    get_embed_status,
    run_embed_pipeline,
)
from phdb.migrations.runner import MigrationRunner

# ---- Helpers ----


@dataclass
class FakeEmbedClient:
    """Drop-in replacement that returns deterministic vectors."""

    dim: int = 768
    model: str = "fake-model"
    call_count: int = 0
    texts_seen: list[str] = field(default_factory=list)

    def embed_batch(
        self,
        texts: list[str],
        *,
        prefix: str = "search_document",
        timeout: int = 60,
    ) -> list[list[float]]:
        self.call_count += 1
        self.texts_seen.extend(texts)
        return [[float(i % 256) / 256.0] * self.dim for i in range(len(texts))]


def _seed_messages(
    conn: sqlite3.Connection,
    *,
    count: int = 3,
    body_len: int = 100,
    is_bulk: int = 0,
) -> None:
    """Insert synthetic messages into a migrated DB."""
    conn.execute(
        "INSERT INTO source_files (id, source_path, source_org, file_kind, message_count)"
        " VALUES (1, '/test/inbox.mbox', 'gmail', 'mbox', ?)",
        (count,),
    )
    for i in range(1, count + 1):
        conn.execute(
            "INSERT INTO emails"
            " (id, schema_type, subject, sender_address, direction,"
            "  date_sent, body_text, is_bulk, source_file_id)"
            " VALUES (?, 'EmailMessage', ?, 'alice@example.com', 'inbound',"
            "  '2024-01-01T00:00:00Z', ?, ?, 1)",
            (i, f"Subject {i}", f"Body content number {i}. " * (body_len // 25 + 1), is_bulk),
        )
    conn.commit()


@pytest.fixture
def embed_db(tmp_path: Path) -> Path:
    """Migrated DB with 3 eligible messages seeded."""
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True, load_vec=True) as conn:
        MigrationRunner(conn).apply_pending()
        ensure_vec_table(conn)
        _seed_messages(conn, count=3, body_len=100)
    return db_path


@pytest.fixture
def fake_client() -> FakeEmbedClient:
    return FakeEmbedClient()


# ---- Status tests ----


class TestGetEmbedStatus:
    def test_empty_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "test.db"
        with connect(db_path, create=True, load_vec=True) as conn:
            MigrationRunner(conn).apply_pending()
            ensure_vec_table(conn)
            st = get_embed_status(conn)
        assert st.total_eligible == 0
        assert st.pending == 0
        assert st.done == 0

    def test_with_seeded_messages(self, embed_db: Path) -> None:
        with connect(embed_db, load_vec=True, readonly=True) as conn:
            st = get_embed_status(conn)
        assert st.total_eligible == 3
        assert st.pending == 3
        assert st.done == 0

    def test_after_embed(self, embed_db: Path, fake_client: FakeEmbedClient) -> None:
        with connect(embed_db, load_vec=True) as conn:
            run_embed_pipeline(conn, fake_client)
            st = get_embed_status(conn)
        assert st.done == 3
        assert st.pending == 0
        assert st.chunks_embedded > 0
        assert st.vectors_stored > 0


# ---- Pipeline tests ----


class TestEmbedPipeline:
    def test_embeds_eligible_messages(
        self, embed_db: Path, fake_client: FakeEmbedClient
    ) -> None:
        with connect(embed_db, load_vec=True) as conn:
            result = run_embed_pipeline(conn, fake_client)
        assert result.messages_processed == 3
        assert result.chunks_embedded > 0

    def test_skips_bulk_messages(self, tmp_path: Path, fake_client: FakeEmbedClient) -> None:
        db_path = tmp_path / "test.db"
        with connect(db_path, create=True, load_vec=True) as conn:
            MigrationRunner(conn).apply_pending()
            ensure_vec_table(conn)
            _seed_messages(conn, count=2, is_bulk=1)
            result = run_embed_pipeline(conn, fake_client)
        assert result.messages_processed == 0
        assert result.chunks_embedded == 0

    def test_skips_short_body(self, tmp_path: Path, fake_client: FakeEmbedClient) -> None:
        db_path = tmp_path / "test.db"
        with connect(db_path, create=True, load_vec=True) as conn:
            MigrationRunner(conn).apply_pending()
            ensure_vec_table(conn)
            conn.execute(
                "INSERT INTO source_files (id, source_path, source_org, file_kind, message_count)"
                " VALUES (1, '/test', 'test', 'mbox', 1)"
            )
            conn.execute(
                "INSERT INTO emails"
                " (id, schema_type, subject, sender_address, direction,"
                "  date_sent, body_text, is_bulk, source_file_id)"
                " VALUES (1, 'EmailMessage', 'Short', 'a@b.com', 'inbound',"
                "  '2024-01-01', 'hi', 0, 1)"
            )
            conn.commit()
            result = run_embed_pipeline(conn, fake_client)
        assert result.messages_processed == 0

    def test_skips_already_embedded(
        self, embed_db: Path, fake_client: FakeEmbedClient
    ) -> None:
        with connect(embed_db, load_vec=True) as conn:
            run_embed_pipeline(conn, fake_client)
            result2 = run_embed_pipeline(conn, fake_client)
        assert result2.messages_processed == 0
        assert result2.chunks_embedded == 0

    def test_limit_parameter(
        self, embed_db: Path, fake_client: FakeEmbedClient
    ) -> None:
        with connect(embed_db, load_vec=True) as conn:
            result = run_embed_pipeline(conn, fake_client, limit=1)
        assert result.messages_processed == 1

    def test_dry_run_no_writes(
        self, embed_db: Path, fake_client: FakeEmbedClient
    ) -> None:
        with connect(embed_db, load_vec=True) as conn:
            result = run_embed_pipeline(conn, fake_client, dry_run=True)
            doc_count = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
        assert result.messages_processed == 3
        assert result.chunks_embedded > 0
        assert doc_count == 0
        assert fake_client.call_count == 0

    def test_creates_correct_document_rows(
        self, embed_db: Path, fake_client: FakeEmbedClient
    ) -> None:
        with connect(embed_db, load_vec=True) as conn:
            run_embed_pipeline(conn, fake_client)
            docs = conn.execute(
                "SELECT schema_type, source_table, chunk_strategy, embedding_model"
                " FROM chunks"
            ).fetchall()
        assert len(docs) > 0
        for d in docs:
            assert d[0] == "EmailMessage"
            assert d[1] == "emails"
            assert d[2] == "message_body_512tok"
            assert d[3] == "fake-model"

    def test_creates_correct_vector_rows(
        self, embed_db: Path, fake_client: FakeEmbedClient
    ) -> None:
        with connect(embed_db, load_vec=True) as conn:
            run_embed_pipeline(conn, fake_client)
            n_docs = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
            n_vecs = conn.execute("SELECT count(*) FROM doc_vectors").fetchone()[0]
        assert n_vecs == n_docs
        assert n_vecs > 0

    def test_content_hash_correct(
        self, embed_db: Path, fake_client: FakeEmbedClient
    ) -> None:
        with connect(embed_db, load_vec=True) as conn:
            run_embed_pipeline(conn, fake_client)
            rows = conn.execute("SELECT content, content_hash FROM chunks").fetchall()
        for content, chash in rows:
            expected = hashlib.sha256(content.encode("utf-8")).hexdigest()
            assert chash == expected

    def test_metadata_json_correct(
        self, embed_db: Path, fake_client: FakeEmbedClient
    ) -> None:
        with connect(embed_db, load_vec=True) as conn:
            run_embed_pipeline(conn, fake_client)
            rows = conn.execute("SELECT metadata_json FROM chunks").fetchall()
        for (raw,) in rows:
            meta = json.loads(raw)
            assert "sender" in meta
            assert "date_sent" in meta

    def test_upsert_on_re_embed(
        self, embed_db: Path, fake_client: FakeEmbedClient
    ) -> None:
        """Re-running on same messages updates rather than duplicates."""
        with connect(embed_db, load_vec=True) as conn:
            run_embed_pipeline(conn, fake_client)
            conn.execute("UPDATE chunks SET embedded_at = NULL")
            conn.commit()
            run_embed_pipeline(conn, fake_client)
            n = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
            n_unique = conn.execute(
                "SELECT count(DISTINCT source_id || '-' || chunk_index) FROM chunks"
            ).fetchone()[0]
        assert n == n_unique

    def test_progress_callback_called(
        self, tmp_path: Path, fake_client: FakeEmbedClient
    ) -> None:
        db_path = tmp_path / "test.db"
        with connect(db_path, create=True, load_vec=True) as conn:
            MigrationRunner(conn).apply_pending()
            ensure_vec_table(conn)
            _seed_messages(conn, count=200, body_len=60)

            progress_calls: list[EmbedProgress] = []
            run_embed_pipeline(
                conn, fake_client, progress_cb=progress_calls.append
            )
        assert len(progress_calls) >= 1
        assert progress_calls[0].messages_done == 100

    def test_resumable_after_partial_run(
        self, embed_db: Path, fake_client: FakeEmbedClient
    ) -> None:
        with connect(embed_db, load_vec=True) as conn:
            run_embed_pipeline(conn, fake_client, limit=1)
            result2 = run_embed_pipeline(conn, fake_client)
        assert result2.messages_processed == 2
