"""Tests for the unified query module."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from phdb.db import connect, ensure_vec_table
from phdb.migrations.runner import MigrationRunner
from phdb.query import (
    build_fts_query,
    corpus_stats,
    find_messages_by_participant,
    find_threads_by_subject,
    get_chunk,
    get_message,
    get_thread,
    list_sources,
    rrf_fuse,
    search,
    top_correspondents,
)


@pytest.fixture
def query_db(tmp_path: Path) -> sqlite3.Connection:
    """A migrated DB seeded with synthetic data for query tests."""
    db_path = tmp_path / "query_test.db"
    with connect(db_path, load_vec=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
        ensure_vec_table(conn)
        _seed(conn)
        conn.commit()
        yield conn


def _seed(conn: sqlite3.Connection) -> None:
    """Insert synthetic messages, documents, recipients, threads."""
    conn.execute(
        "INSERT INTO source_files (id, source_path, source_org, file_kind, message_count)"
        " VALUES (1, '/test/inbox.mbox', 'gmail', 'mbox', 5)"
    )
    msgs = [
        (1, "EmailMessage", "msg-001", "thread-A", "Project kickoff",
         "alice@example.com", "Alice", "example.com", "inbound",
         "2024-03-15T10:00:00Z", "Let's start the project planning phase", 0, 1, 0),
        (2, "EmailMessage", "msg-002", "thread-A", "Re: Project kickoff",
         "bob@example.com", "Bob", "example.com", "inbound",
         "2024-03-15T11:00:00Z", "Sounds good, I'll prepare the architecture docs", 0, 1, 1),
        (3, "EmailMessage", "msg-003", "thread-B", "Weekly standup notes",
         "noreply@lists.example.com", "Mailing List", "lists.example.com", "inbound",
         "2024-06-01T09:00:00Z", "Here are the standup notes for this week", 1, 1, 0),
        (4, "EmailMessage", "msg-004", None, "Dinner plans with Erica",
         "erica@example.com", "Erica", "example.com", "inbound",
         "2023-11-20T18:30:00Z", "Hey are we still on for dinner Friday?", 0, 1, 0),
        (5, "EmailMessage", "msg-005", "thread-C", "WGU coursework feedback",
         "test@example.com", "Test User", "example.com", "outbound",
         "2024-01-10T14:00:00Z", "My WGU learning experience has been great", 0, 1, 0),
    ]
    for m in msgs:
        conn.execute(
            "INSERT INTO messages (id, schema_type, rfc822_message_id, gmail_thread_id,"
            " subject, sender_address, sender_name, sender_domain, direction,"
            " date_sent, body_text, is_bulk, source_file_id, attachment_count)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", m
        )

    recips = [
        (1, "test@example.com", "Test User", "to"),
        (2, "test@example.com", "Test User", "to"),
        (2, "carol@example.com", "Carol", "cc"),
        (4, "test@example.com", "Test User", "to"),
        (5, "erica@example.com", "Erica", "to"),
    ]
    for msg_id, addr, name, rtype in recips:
        conn.execute(
            "INSERT INTO recipients (message_id, address, name, rtype)"
            " VALUES (?,?,?,?)",
            (msg_id, addr, name, rtype),
        )

    conn.execute(
        "INSERT INTO attachments (message_id, filename, content_type, size_bytes)"
        " VALUES (2, 'arch.pdf', 'application/pdf', 204800)"
    )

    threads = [
        (1, "Conversation", "thread-A", None, None, "project kickoff", 2,
         "2024-03-15T10:00:00Z", "2024-03-15T11:00:00Z",
         '["alice@example.com", "bob@example.com", "test@example.com"]'),
        (2, "Conversation", None, "gmail", "gmail:thread-B", "weekly standup notes", 1,
         "2024-06-01T09:00:00Z", "2024-06-01T09:00:00Z",
         '["noreply@lists.example.com"]'),
        (3, "Conversation", "thread-C", None, None, "wgu coursework feedback", 1,
         "2024-01-10T14:00:00Z", "2024-01-10T14:00:00Z",
         '["test@example.com", "erica@example.com"]'),
    ]
    for t in threads:
        conn.execute(
            "INSERT INTO threads (id, schema_type, gmail_thread_id, source_kind,"
            " thread_key, subject_canonical, message_count,"
            " date_first, date_last, participants)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)", t
        )

    docs = [
        (1, "EmailMessage", "messages", 1, 0, "message_body_512tok",
         "Project kickoff", "Let's start the project planning phase"),
        (2, "EmailMessage", "messages", 2, 0, "message_body_512tok",
         "Re: Project kickoff", "Sounds good, I'll prepare the architecture docs"),
        (3, "EmailMessage", "messages", 3, 0, "message_body_512tok",
         "Weekly standup notes", "Here are the standup notes for this week"),
        (4, "EmailMessage", "messages", 4, 0, "message_body_512tok",
         "Dinner plans with Erica", "Hey are we still on for dinner Friday?"),
        (5, "EmailMessage", "messages", 5, 0, "message_body_512tok",
         "WGU coursework feedback", "My WGU learning experience has been great"),
    ]
    for d in docs:
        conn.execute(
            "INSERT INTO chunks (id, schema_type, source_table, source_id,"
            " chunk_index, chunk_strategy, title, content)"
            " VALUES (?,?,?,?,?,?,?,?)", d
        )


# ===================================================================
# build_fts_query
# ===================================================================
class TestBuildFtsQuery:
    def test_strips_stopwords(self) -> None:
        result = build_fts_query("what was I planning before the surgery")
        assert "what" not in result
        assert "was" not in result
        assert "planning" in result
        assert "surgery" in result

    def test_preserves_phrases(self) -> None:
        result = build_fts_query('"job change" anxiety')
        assert '"job change"' in result
        assert "anxiety" in result

    def test_empty_after_stopword_removal(self) -> None:
        result = build_fts_query("what is the")
        assert result == ""

    def test_and_operator(self) -> None:
        result = build_fts_query("WGU learning experience", op="AND")
        assert " AND " in result

    def test_or_operator(self) -> None:
        result = build_fts_query("WGU learning experience", op="OR")
        assert " OR " in result

    def test_strips_non_alnum(self) -> None:
        result = build_fts_query("PSE&G billing @home")
        parts = result.split()
        for p in parts:
            stripped = p.replace('"', "")
            assert stripped.isalnum() or stripped in ("AND", "OR"), f"non-alnum: {p}"


# ===================================================================
# rrf_fuse
# ===================================================================
class TestRrfFuse:
    def test_single_list(self) -> None:
        ranked = [(10, 0.5, 1), (20, 0.4, 2), (30, 0.3, 3)]
        fused = rrf_fuse(ranked)
        assert len(fused) == 3
        assert fused[0][0] == 10

    def test_two_lists_intersection_wins(self) -> None:
        sem = [(10, 0.9, 1), (20, 0.8, 2)]
        fts = [(20, -1.0, 1), (30, -2.0, 2)]
        fused = rrf_fuse(sem, fts)
        assert fused[0][0] == 20

    def test_empty_lists(self) -> None:
        assert rrf_fuse([], []) == []


# ===================================================================
# search (FTS-only mode, no Ollama)
# ===================================================================
class TestSearch:
    def test_fts_only_returns_results(self, query_db: sqlite3.Connection) -> None:
        result = search(query_db, "project planning", mode="fts")
        assert result["mode"] == "fts"
        assert result["n_fts"] > 0
        assert len(result["results"]) > 0

    def test_fts_only_includes_expected_fields(self, query_db: sqlite3.Connection) -> None:
        result = search(query_db, "project", mode="fts")
        if result["results"]:
            r = result["results"][0]
            assert "doc_id" in r
            assert "snippet" in r
            assert "score" in r
            assert "sender" in r
            assert "date" in r

    def test_fts_bulk_filtered_by_default(self, query_db: sqlite3.Connection) -> None:
        result = search(query_db, "standup notes", mode="fts")
        bulk_results = [r for r in result["results"] if r.get("is_bulk")]
        assert len(bulk_results) == 0

    def test_fts_include_bulk(self, query_db: sqlite3.Connection) -> None:
        result = search(query_db, "standup notes", mode="fts", include_bulk=True)
        assert len(result["results"]) > 0

    def test_since_filter(self, query_db: sqlite3.Connection) -> None:
        result = search(query_db, "project planning dinner WGU", mode="fts", since="2024")
        dates = [r["date"] for r in result["results"] if r["date"]]
        for d in dates:
            assert d >= "2024"

    def test_until_filter(self, query_db: sqlite3.Connection) -> None:
        result = search(query_db, "project planning dinner WGU", mode="fts", until="2023-12-31")
        dates = [r["date"] for r in result["results"] if r["date"]]
        for d in dates:
            assert d <= "2023-12-31"

    def test_hybrid_degrades_to_fts_without_embed(self, query_db: sqlite3.Connection) -> None:
        result = search(query_db, "project", mode="hybrid")
        assert result["n_semantic"] == 0
        assert result["n_fts"] > 0

    def test_semantic_without_client_returns_error(self, query_db: sqlite3.Connection) -> None:
        result = search(query_db, "test", mode="semantic")
        assert "error" in result


# ===================================================================
# get_message
# ===================================================================
class TestGetMessage:
    def test_returns_message(self, query_db: sqlite3.Connection) -> None:
        result = get_message(query_db, 1)
        assert result["id"] == 1
        assert result["subject"] == "Project kickoff"
        assert result["sender_address"] == "alice@example.com"

    def test_includes_recipients(self, query_db: sqlite3.Connection) -> None:
        result = get_message(query_db, 1, include_recipients=True)
        assert "recipients" in result
        assert len(result["recipients"]) == 1
        assert result["recipients"][0]["address"] == "test@example.com"

    def test_excludes_recipients(self, query_db: sqlite3.Connection) -> None:
        result = get_message(query_db, 1, include_recipients=False)
        assert "recipients" not in result

    def test_includes_attachments(self, query_db: sqlite3.Connection) -> None:
        result = get_message(query_db, 2, include_attachments=True)
        assert "attachments" in result
        assert result["attachments"][0]["filename"] == "arch.pdf"

    def test_not_found(self, query_db: sqlite3.Connection) -> None:
        result = get_message(query_db, 9999)
        assert "error" in result


# ===================================================================
# get_chunk
# ===================================================================
class TestGetChunk:
    def test_returns_chunk(self, query_db: sqlite3.Connection) -> None:
        result = get_chunk(query_db, 1)
        assert result["id"] == 1
        assert result["source_table"] == "messages"
        assert "content" in result

    def test_not_found(self, query_db: sqlite3.Connection) -> None:
        result = get_chunk(query_db, 9999)
        assert "error" in result


# ===================================================================
# get_thread
# ===================================================================
class TestGetThread:
    def test_by_thread_id(self, query_db: sqlite3.Connection) -> None:
        result = get_thread(query_db, thread_id="thread-A")
        assert result["thread_id"] == "thread-A"
        assert result["message_count"] == 2
        assert len(result["messages"]) == 2

    def test_by_msg_id(self, query_db: sqlite3.Connection) -> None:
        result = get_thread(query_db, msg_id=1)
        assert result["thread_id"] == "thread-A"

    def test_neither_provided(self, query_db: sqlite3.Connection) -> None:
        result = get_thread(query_db)
        assert "error" in result

    def test_msg_no_thread(self, query_db: sqlite3.Connection) -> None:
        result = get_thread(query_db, msg_id=4)
        assert "error" in result


# ===================================================================
# list_sources
# ===================================================================
class TestListSources:
    def test_returns_totals(self, query_db: sqlite3.Connection) -> None:
        result = list_sources(query_db)
        assert result["totals"]["messages"] == 5
        assert result["totals"]["chunks"] == 5
        assert result["totals"]["documents"] == 0

    def test_returns_source_files(self, query_db: sqlite3.Connection) -> None:
        result = list_sources(query_db)
        assert len(result["source_files"]) >= 1
        sf = result["source_files"][0]
        assert sf["source_org"] == "gmail"


# ===================================================================
# corpus_stats
# ===================================================================
class TestCorpusStats:
    def test_returns_year_breakdown(self, query_db: sqlite3.Connection) -> None:
        result = corpus_stats(query_db)
        years = {row["year"] for row in result["by_year"]}
        assert "2024" in years

    def test_since_filter(self, query_db: sqlite3.Connection) -> None:
        result = corpus_stats(query_db, since="2024")
        years = {row["year"] for row in result["by_year"]}
        assert "2023" not in years

    def test_direction_breakdown(self, query_db: sqlite3.Connection) -> None:
        result = corpus_stats(query_db)
        dirs = {row["direction"] for row in result["by_direction"]}
        assert "inbound" in dirs


# ===================================================================
# find_messages_by_participant
# ===================================================================
class TestFindMessagesByParticipant:
    def test_finds_by_sender(self, query_db: sqlite3.Connection) -> None:
        result = find_messages_by_participant(query_db, "alice", role="sender")
        assert result["match_count"] >= 1
        assert result["messages"][0]["sender_address"] == "alice@example.com"

    def test_finds_by_recipient(self, query_db: sqlite3.Connection) -> None:
        result = find_messages_by_participant(
            query_db, "carol", role="recipient", include_bulk=True
        )
        assert result["match_count"] >= 1

    def test_finds_by_any(self, query_db: sqlite3.Connection) -> None:
        result = find_messages_by_participant(query_db, "erica")
        assert result["match_count"] >= 1

    def test_excludes_bulk_by_default(self, query_db: sqlite3.Connection) -> None:
        result = find_messages_by_participant(query_db, "noreply")
        assert result["match_count"] == 0

    def test_since_filter(self, query_db: sqlite3.Connection) -> None:
        result = find_messages_by_participant(query_db, "alice", since="2025")
        assert result["match_count"] == 0

    def test_invalid_role(self, query_db: sqlite3.Connection) -> None:
        result = find_messages_by_participant(query_db, "alice", role="invalid")
        assert "error" in result


# ===================================================================
# find_threads_by_subject
# ===================================================================
class TestFindThreadsBySubject:
    def test_finds_thread(self, query_db: sqlite3.Connection) -> None:
        result = find_threads_by_subject(query_db, "kickoff")
        assert result["match_count"] >= 1
        assert result["threads"][0]["subject"] == "project kickoff"

    def test_since_filter(self, query_db: sqlite3.Connection) -> None:
        result = find_threads_by_subject(query_db, "kickoff", since="2025")
        assert result["match_count"] == 0

    def test_parses_participants(self, query_db: sqlite3.Connection) -> None:
        result = find_threads_by_subject(query_db, "kickoff")
        participants = result["threads"][0]["participants"]
        assert isinstance(participants, list)
        assert "alice@example.com" in participants


# ===================================================================
# top_correspondents
# ===================================================================
class TestTopCorrespondents:
    def test_top_senders(self, query_db: sqlite3.Connection) -> None:
        result = top_correspondents(query_db, exclude_bulk=True, exclude_self=False)
        assert len(result["correspondents"]) > 0
        addrs = [c["address"] for c in result["correspondents"]]
        assert "noreply@lists.example.com" not in addrs

    def test_top_recipients(self, query_db: sqlite3.Connection) -> None:
        result = top_correspondents(query_db, role="recipient", exclude_self=False)
        assert len(result["correspondents"]) > 0

    def test_both_role(self, query_db: sqlite3.Connection) -> None:
        result = top_correspondents(query_db, role="both", exclude_self=False)
        assert len(result["correspondents"]) > 0

    def test_invalid_role(self, query_db: sqlite3.Connection) -> None:
        result = top_correspondents(query_db, role="invalid")
        assert "error" in result

    def test_since_filter(self, query_db: sqlite3.Connection) -> None:
        result = top_correspondents(query_db, since="2025", exclude_self=False)
        assert len(result["correspondents"]) == 0


# ===================================================================
# kind field + include_meta filter (AI sessions)
# ===================================================================

def _seed_ai_session(conn: sqlite3.Connection) -> None:
    """Add synthetic AI session rows with different kind values."""
    conn.execute(
        "INSERT INTO source_files (id, source_path, source_org, file_kind, message_count)"
        " VALUES (2, '/test/session.jsonl', 'claude-code', 'jsonl', 3)"
    )
    ai_msgs = [
        (10, "Conversation", None, None, None,
         None, None, None, "self",
         "2026-04-16T10:00:00Z", "What is the capital of France?", 0, 2, 0,
         "message", "user"),
        (11, "Conversation", None, None, None,
         None, None, None, "self",
         "2026-04-16T10:00:01Z", "Paris is the capital of France.", 0, 2, 0,
         "message", "assistant"),
        (12, "Conversation", None, None, None,
         None, None, None, "self",
         "2026-04-16T10:00:02Z", "thinking about capital cities", 1, 2, 0,
         "sidechain", "assistant"),
    ]
    for m in ai_msgs:
        conn.execute(
            "INSERT INTO messages (id, schema_type, rfc822_message_id, gmail_thread_id,"
            " subject, sender_address, sender_name, sender_domain, direction,"
            " date_sent, body_text, is_bulk, source_file_id, attachment_count,"
            " kind, role)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", m
        )
    ai_docs = [
        (10, "Conversation", "messages", 10, 0, "message_body_512tok",
         None, "What is the capital of France?"),
        (11, "Conversation", "messages", 11, 0, "message_body_512tok",
         None, "Paris is the capital of France."),
        (12, "Conversation", "messages", 12, 0, "message_body_512tok",
         None, "thinking about capital cities"),
    ]
    for d in ai_docs:
        conn.execute(
            "INSERT INTO chunks (id, schema_type, source_table, source_id,"
            " chunk_index, chunk_strategy, title, content)"
            " VALUES (?,?,?,?,?,?,?,?)", d
        )


@pytest.fixture
def ai_session_db(tmp_path: Path) -> sqlite3.Connection:
    """DB with both email messages and AI session rows with different kinds."""
    db_path = tmp_path / "ai_session_test.db"
    with connect(db_path, load_vec=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
        ensure_vec_table(conn)
        _seed(conn)
        _seed_ai_session(conn)
        conn.commit()
        yield conn


class TestKindFilter:
    def test_kind_field_in_results(self, ai_session_db: sqlite3.Connection) -> None:
        result = search(ai_session_db, "capital France", mode="fts", include_meta=True)
        kinds = {r.get("kind") for r in result["results"]}
        assert "message" in kinds

    def test_sidechain_excluded_by_default(self, ai_session_db: sqlite3.Connection) -> None:
        result = search(ai_session_db, "thinking capital cities", mode="fts")
        kinds = [r.get("kind") for r in result["results"]]
        assert "sidechain" not in kinds

    def test_sidechain_included_with_include_meta(self, ai_session_db: sqlite3.Connection) -> None:
        result = search(
            ai_session_db, "thinking capital cities", mode="fts",
            include_bulk=True, include_meta=True,
        )
        kinds = [r.get("kind") for r in result["results"]]
        assert "sidechain" in kinds

    def test_message_kind_passes_default_filter(self, ai_session_db: sqlite3.Connection) -> None:
        result = search(ai_session_db, "capital France", mode="fts")
        assert len(result["results"]) >= 1
        for r in result["results"]:
            assert r.get("kind") in (None, "message")
