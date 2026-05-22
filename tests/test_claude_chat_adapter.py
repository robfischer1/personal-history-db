"""Tests for the claude_chat adapter."""

from __future__ import annotations

from pathlib import Path

from phdb.adapters.claude_chat import ClaudeChatAdapter
from phdb.db import connect

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "claude_chat"
CONVOS = FIXTURE_DIR / "conversations.json"
MEMORIES = FIXTURE_DIR / "memories.json"
USERS = FIXTURE_DIR / "users.json"
PROJECT = FIXTURE_DIR / "projects" / "00000000-0000-4000-8000-fixtureproj0.json"

# Expected counts (recompute when the fixture is edited)
EXPECTED_CONVO_ROWS = 50          # 50 content blocks across 7 conversations
EXPECTED_CONVO_THREADS = 7
EXPECTED_TEXT_ROWS = 46
EXPECTED_TOOL_USE_ROWS = 2
EXPECTED_TOOL_RESULT_ROWS = 2


class TestConversationsIngest:
    def test_row_count(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            report = adapter.run(CONVOS, conn, test_settings)
        assert report.rows_yielded == EXPECTED_CONVO_ROWS
        assert report.rows_inserted == EXPECTED_CONVO_ROWS
        assert report.rows_skipped == 0

    def test_threads_created(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            report = adapter.run(CONVOS, conn, test_settings)
        assert report.threads_created == EXPECTED_CONVO_THREADS

    def test_kind_breakdown(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            adapter.run(CONVOS, conn, test_settings)
            kinds = dict(conn.execute("SELECT kind, COUNT(*) FROM conversations_messages GROUP BY kind").fetchall())
        assert kinds.get("message") == EXPECTED_TEXT_ROWS
        assert kinds.get("tool_use") == EXPECTED_TOOL_USE_ROWS
        assert kinds.get("tool_result") == EXPECTED_TOOL_RESULT_ROWS

    def test_tool_rows_marked_bulk(self, migrated_db: Path, test_settings) -> None:
        """tool_use and tool_result rows are flagged is_bulk=1 to keep default search clean."""
        test_settings.db_path = migrated_db
        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            adapter.run(CONVOS, conn, test_settings)
            bulk_kinds = conn.execute(
                "SELECT DISTINCT kind FROM conversations_messages WHERE is_bulk = 1"
            ).fetchall()
        bulk_kinds = {row[0] for row in bulk_kinds}
        assert "tool_use" in bulk_kinds
        assert "tool_result" in bulk_kinds
        assert "message" not in bulk_kinds

    def test_role_assignment(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            adapter.run(CONVOS, conn, test_settings)
            roles = dict(conn.execute(
                "SELECT role, COUNT(*) FROM conversations_messages WHERE kind = 'message' GROUP BY role"
            ).fetchall())
        # Both user (human) and assistant rows must be present
        assert "user" in roles
        assert "assistant" in roles

    def test_thread_nodes_created(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            adapter.run(CONVOS, conn, test_settings)
            thread_nodes = conn.execute(
                "SELECT label FROM nodes WHERE kind = 'thread' AND label LIKE '%claude-chat-%'"
            ).fetchall()
        assert len(thread_nodes) == EXPECTED_CONVO_THREADS

    def test_dedup_on_rerun(self, migrated_db: Path, test_settings) -> None:
        """Re-ingesting the same export must INSERT OR IGNORE everything."""
        test_settings.db_path = migrated_db
        with connect(migrated_db) as conn:
            r1 = ClaudeChatAdapter().run(CONVOS, conn, test_settings)
        with connect(migrated_db) as conn:
            r2 = ClaudeChatAdapter().run(CONVOS, conn, test_settings)
        assert r1.rows_inserted == EXPECTED_CONVO_ROWS
        assert r2.rows_inserted == 0
        assert r2.rows_skipped == r2.rows_yielded == EXPECTED_CONVO_ROWS

    def test_attachments_extracted(self, migrated_db: Path, test_settings) -> None:
        """The synthetic fixture conv has 1 attachment + 1 file -> 2 attachment rows on its first text msg."""
        test_settings.db_path = migrated_db
        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            adapter.run(CONVOS, conn, test_settings)
            n_attach = conn.execute("SELECT COUNT(*) FROM attachments").fetchone()[0]
        assert n_attach >= 2

    def test_sender_addresses(self, migrated_db: Path, test_settings) -> None:
        """Assistant rows use a stable claude-chat:claude sender; human rows use the owner."""
        test_settings.db_path = migrated_db
        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            adapter.run(CONVOS, conn, test_settings)
            senders = {
                row[0]
                for row in conn.execute(
                    "SELECT DISTINCT sender_address FROM conversations_messages WHERE kind = 'message'"
                ).fetchall()
            }
        assert "claude-chat:claude" in senders


class TestMemoriesIngest:
    def test_basic(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            report = adapter.run(MEMORIES, conn, test_settings)
            count = conn.execute(
                "SELECT COUNT(*) FROM things WHERE schema_type = 'Thing'"
            ).fetchone()[0]
        assert report.rows_inserted >= 1
        assert count >= 1


class TestUsersIngest:
    def test_basic(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            report = adapter.run(USERS, conn, test_settings)
            row = conn.execute(
                "SELECT schema_type FROM persons WHERE schema_type = 'Person'"
            ).fetchone()
        assert report.rows_inserted == 1
        assert row is not None
        assert row[0] == "Person"


class TestProjectIngest:
    def test_definition_plus_docs(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            report = adapter.run(PROJECT, conn, test_settings)
            cw_count = conn.execute(
                "SELECT COUNT(*) FROM creative_works WHERE schema_type = 'CreativeWork'"
            ).fetchone()[0]
            dd_count = conn.execute(
                "SELECT COUNT(*) FROM digital_documents WHERE schema_type = 'DigitalDocument'"
            ).fetchone()[0]
        assert report.rows_inserted == 2
        assert cw_count == 1
        assert dd_count == 1
        assert report.threads_created == 1

    def test_project_thread_keyed(self, migrated_db: Path, test_settings) -> None:
        test_settings.db_path = migrated_db
        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            adapter.run(PROJECT, conn, test_settings)
            row = conn.execute(
                "SELECT label FROM nodes WHERE kind = 'thread' AND label LIKE '%claude-chat-project-%'"
            ).fetchone()
        assert row is not None


class TestDedupAcrossOverlappingExports:
    """Simulates the takeout-overlap problem: second export contains a superset of first.

    The same conversation UUIDs reappear -> rows must be deduped via raw_hash.
    """

    def test_overlap_dedups(self, migrated_db: Path, test_settings, tmp_path: Path) -> None:
        import json
        test_settings.db_path = migrated_db

        # First batch: first 3 conversations
        first = json.loads(CONVOS.read_text())[:3]
        first_path = tmp_path / "export1.json"
        # Use a name the adapter recognizes by parent_path/filename dispatch.
        first_file = tmp_path / "conversations.json"
        first_file.write_text(json.dumps(first))

        # Second batch: ALL conversations (overlaps + new ones)
        second_file = tmp_path / "batch2"
        second_file.mkdir()
        (second_file / "conversations.json").write_text(CONVOS.read_text())

        adapter = ClaudeChatAdapter()
        with connect(migrated_db) as conn:
            r1 = adapter.run(first_file, conn, test_settings)
        with connect(migrated_db) as conn:
            r2 = adapter.run(second_file / "conversations.json", conn, test_settings)

        assert r1.rows_inserted > 0
        # Second batch yields the full count.  Dedup is per-source-file
        # (unique index on source_file_id + raw_hash), so rows from a
        # *different* source file re-insert even if content hashes match.
        assert r2.rows_yielded == EXPECTED_CONVO_ROWS
        assert r2.rows_inserted == EXPECTED_CONVO_ROWS
