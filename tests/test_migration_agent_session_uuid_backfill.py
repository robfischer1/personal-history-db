"""Tests for migration 0038_agent_session_uuid_backfill.

Covers:
  - Agent sub-session rows (`agent-<hex>.jsonl`) get session_uuid populated
  - Top-level UUID sessions are untouched (already backfilled by 0010)
  - Non-claude-code rows are untouched
  - Re-running 0038 is a no-op (idempotent on the bounded UPDATE)
  - The partial UNIQUE index now blocks a duplicate agent insert
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner

_AGENT_LEGACY_PATH = (
    r"D:\<records>\AI Sessions\Claude\claude-code__c--Users__"
    r"agent-a1709f28e260fa9f.jsonl"
)
_AGENT_OTHER_PATH = (
    r"D:\<records>\AI Sessions\Claude\claude-code__c--Users-Obsidian__"
    r"agent-b88e4d1b28dbc1e2f.jsonl"
)
_TOPLEVEL_PATH = (
    r"D:\<records>\AI Sessions\Claude\claude-code__c--Users-Obsidian__"
    r"4efecc8b-d706-4667-b922-7476858b2991.jsonl"
)
_NON_CLAUDE_PATH = (
    r"D:\<records>\Other\claude.ai-export.json"
)


@pytest.fixture
def db_pre_0038(tmp_path: Path) -> Path:
    """Seed a DB with migrations 0001..0037 applied + representative rows."""
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        pre_0038 = sorted(
            [m for m in runner.discover() if m.number < 38],
            key=lambda m: m.number,
        )
        for m in pre_0038:
            sql = m.path.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_id) VALUES (?)",
                (m.migration_id,),
            )

        # Top-level row — 0010 should have backfilled it.
        conn.execute(
            """INSERT INTO source_files
               (source_path, source_kind, file_kind, session_uuid)
               VALUES (?, 'claude-code', 'jsonl',
                       '4efecc8b-d706-4667-b922-7476858b2991')""",
            (_TOPLEVEL_PATH,),
        )
        # Two agent rows — session_uuid NULL pre-0038
        conn.execute(
            """INSERT INTO source_files
               (source_path, source_kind, file_kind, session_uuid)
               VALUES (?, 'claude-code', 'jsonl', NULL)""",
            (_AGENT_LEGACY_PATH,),
        )
        conn.execute(
            """INSERT INTO source_files
               (source_path, source_kind, file_kind, session_uuid)
               VALUES (?, 'claude-code', 'jsonl', NULL)""",
            (_AGENT_OTHER_PATH,),
        )
        # Non-claude-code row — must stay untouched
        conn.execute(
            """INSERT INTO source_files
               (source_path, source_kind, file_kind, session_uuid)
               VALUES (?, 'claude-chat', 'json', NULL)""",
            (_NON_CLAUDE_PATH,),
        )
        conn.commit()
    return db_path


@pytest.fixture
def migrated_db(db_pre_0038: Path) -> Path:
    with connect(db_pre_0038) as conn:
        MigrationRunner(conn).apply_pending()
    return db_pre_0038


def test_agent_rows_get_session_uuid(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        rows = conn.execute(
            "SELECT source_path, session_uuid FROM source_files "
            "WHERE source_kind='claude-code' AND source_path LIKE '%agent-%' "
            "ORDER BY source_path"
        ).fetchall()
        assert len(rows) == 2
        path_to_uuid = {r[0]: r[1] for r in rows}
        assert path_to_uuid[_AGENT_LEGACY_PATH] == "agent-a1709f28e260fa9f"
        assert path_to_uuid[_AGENT_OTHER_PATH] == "agent-b88e4d1b28dbc1e2f"


def test_toplevel_row_untouched(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        uuid = conn.execute(
            "SELECT session_uuid FROM source_files WHERE source_path = ?",
            (_TOPLEVEL_PATH,),
        ).fetchone()[0]
        assert uuid == "4efecc8b-d706-4667-b922-7476858b2991"


def test_non_claude_code_untouched(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        uuid = conn.execute(
            "SELECT session_uuid FROM source_files WHERE source_path = ?",
            (_NON_CLAUDE_PATH,),
        ).fetchone()[0]
        assert uuid is None


def test_partial_index_blocks_duplicate_agent(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        with pytest.raises(Exception) as exc:
            conn.execute(
                """INSERT INTO source_files
                   (source_path, source_kind, file_kind, session_uuid)
                   VALUES (?, 'claude-code', 'jsonl', 'agent-a1709f28e260fa9f')""",
                (r"E:\Different\Path\copy.jsonl",),
            )
        assert "UNIQUE constraint failed" in str(exc.value)


def test_rerun_is_noop(migrated_db: Path) -> None:
    sql = (
        Path(__file__).parent.parent
        / "src" / "phdb" / "migrations" / "project"
        / "0038_agent_session_uuid_backfill.sql"
    ).read_text(encoding="utf-8")
    with connect(migrated_db) as conn:
        before = conn.execute(
            "SELECT source_path, session_uuid FROM source_files "
            "ORDER BY id"
        ).fetchall()
        conn.executescript(sql)
        conn.commit()
        after = conn.execute(
            "SELECT source_path, session_uuid FROM source_files "
            "ORDER BY id"
        ).fetchall()
        assert before == after
