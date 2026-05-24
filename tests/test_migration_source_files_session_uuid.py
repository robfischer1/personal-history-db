"""Tests for migration 0010_source_files_session_uuid.

Covers:
  - session_uuid column is added to source_files
  - Partial UNIQUE index exists on (source_kind, session_uuid)
  - Backfill extracts session_uuid from canonical and legacy filename shapes
  - Backfill leaves agent sub-sessions (agent-<hex>.jsonl) with session_uuid=NULL
  - Cleanup deletes the legacy-C:\\ source_files when a relocated D:\\ counterpart exists
  - Cleanup cascades to messages under the deleted source_files
  - Cleanup is bounded — leaves legacy-C:\\ rows alone if no relocated counterpart
  - Re-inserting the same session under a renamed path triggers the UPSERT path
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def db_with_pre_0010_state(tmp_path: Path) -> Path:
    """Set up a DB at migration 0009, seeded with dup pairs (legacy + relocated)."""
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        # Apply migrations 0001..0009 by walking the discovered file list.
        runner = MigrationRunner(conn)
        pre_0010 = sorted(
            [m for m in runner.discover() if m.number < 10],
            key=lambda m: m.number,
        )
        # Ensure schema_migrations exists before INSERT (0001_init creates it).
        for m in pre_0010:
            sql = m.path.read_text(encoding="utf-8")
            conn.executescript(sql)
            conn.execute(
                "INSERT OR IGNORE INTO schema_migrations (migration_id) VALUES (?)",
                (m.migration_id,),
            )
        conn.commit()

        # Seed: 2 dup pairs + 1 unique relocated session + 1 lonely legacy + 1 agent sub-session
        pairs = [
            ("4efecc8b-d706-4667-b922-7476858b2991",
             r"C:\Users\<owner>\.claude\projects\c--Users-<owner>-Obsidian\4efecc8b-d706-4667-b922-7476858b2991.jsonl",
             r"D:\<records>\AI Sessions\Claude\claude-code__c--Users-<owner>-Obsidian__4efecc8b-d706-4667-b922-7476858b2991.jsonl"),
            ("8bab7e63-bbbc-43a4-9398-8126f2ea66ad",
             r"C:\Users\<owner>\.claude\projects\c--Users-<owner>-Obsidian-Obsidian\8bab7e63-bbbc-43a4-9398-8126f2ea66ad.jsonl",
             r"D:\<records>\AI Sessions\Claude\claude-code__c--Users-<owner>-Obsidian-Obsidian__8bab7e63-bbbc-43a4-9398-8126f2ea66ad.jsonl"),
        ]
        for _, legacy_path, reloc_path in pairs:
            for path in (legacy_path, reloc_path):
                conn.execute(
                    "INSERT INTO source_files (source_path, source_kind, file_kind) VALUES (?, 'claude-code', 'jsonl')",
                    (path,),
                )
        # Lonely legacy (no relocated counterpart) — must NOT be deleted
        conn.execute(
            "INSERT INTO source_files (source_path, source_kind, file_kind) VALUES (?, 'claude-code', 'jsonl')",
            (r"C:\Users\<owner>\.claude\projects\some-other\aaaa1111-2222-3333-4444-555566667777.jsonl",),
        )
        # Lonely relocated (no legacy counterpart)
        conn.execute(
            "INSERT INTO source_files (source_path, source_kind, file_kind) VALUES (?, 'claude-code', 'jsonl')",
            (r"D:\<records>\AI Sessions\Claude\claude-code__only__bbbb2222-3333-4444-5555-666677778888.jsonl",),
        )
        # Agent sub-session (no session UUID; legacy path)
        conn.execute(
            "INSERT INTO source_files (source_path, source_kind, file_kind) VALUES (?, 'claude-code', 'jsonl')",
            (r"D:\<records>\AI Sessions\Claude\claude-code__c--Users__agent-a1709f28e260fa9f.jsonl",),
        )

        # Seed messages under one of the dup pairs so we can verify cascade.
        # Get the legacy + relocated IDs for the first pair.
        legacy_id = conn.execute(
            "SELECT id FROM source_files WHERE source_path = ?",
            (pairs[0][1],),
        ).fetchone()[0]
        reloc_id = conn.execute(
            "SELECT id FROM source_files WHERE source_path = ?",
            (pairs[0][2],),
        ).fetchone()[0]
        for _i, sf_id in enumerate([legacy_id, reloc_id]):
            for j in range(3):
                conn.execute(
                    """INSERT INTO messages (schema_type, body_text, source_file_id, raw_hash, kind, role, direction)
                       VALUES ('Conversation', ?, ?, ?, 'message', 'user', 'unknown')""",
                    (f"msg-{j}-from-sf{sf_id}", sf_id, f"claude-code:msg-{j}-sf{sf_id}"),
                )
        conn.commit()
    return db_path


@pytest.fixture
def migrated_db(db_with_pre_0010_state: Path) -> Path:
    """Apply migration 0010 on top of the seeded pre-0010 state."""
    with connect(db_with_pre_0010_state) as conn:
        MigrationRunner(conn).apply_pending()
    return db_with_pre_0010_state


# ── Schema shape ────────────────────────────────────────────────────────────


def test_source_files_has_session_uuid_column(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(source_files)").fetchall()}
        assert "session_uuid" in cols


def test_partial_unique_index_exists(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        idx = conn.execute(
            """SELECT name, sql FROM sqlite_master
               WHERE type='index' AND name='idx_source_files_kind_session_uuid'"""
        ).fetchone()
        assert idx is not None
        # Partial-index WHERE clause is preserved in the stored SQL.
        assert "session_uuid IS NOT NULL" in idx[1]
        assert "UNIQUE" in idx[1].upper()


# ── Backfill ─────────────────────────────────────────────────────────────────


def test_backfill_extracts_uuid_from_legacy_path(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        row = conn.execute(
            """SELECT session_uuid FROM source_files
               WHERE source_path = ?""",
            (r"C:\Users\<owner>\.claude\projects\some-other\aaaa1111-2222-3333-4444-555566667777.jsonl",),
        ).fetchone()
        assert row[0] == "aaaa1111-2222-3333-4444-555566667777"


def test_backfill_extracts_uuid_from_relocated_path(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        row = conn.execute(
            """SELECT session_uuid FROM source_files
               WHERE source_path = ?""",
            (r"D:\<records>\AI Sessions\Claude\claude-code__only__bbbb2222-3333-4444-5555-666677778888.jsonl",),
        ).fetchone()
        assert row[0] == "bbbb2222-3333-4444-5555-666677778888"


def test_agent_subsession_backfilled_by_followup_migration(migrated_db: Path) -> None:
    # Migration 0010's backfill GLOB only matches the 36-char UUID shape,
    # so it leaves agent sub-sessions at NULL. Migration 0038 fills them
    # in with their `agent-<hex>` identifier; this fixture applies all
    # pending migrations, so the post-state covers 0038's contribution.
    with connect(migrated_db) as conn:
        row = conn.execute(
            """SELECT session_uuid FROM source_files
               WHERE source_path = ?""",
            (r"D:\<records>\AI Sessions\Claude\claude-code__c--Users__agent-a1709f28e260fa9f.jsonl",),
        ).fetchone()
        assert row[0] == "agent-a1709f28e260fa9f"


# ── Cleanup ─────────────────────────────────────────────────────────────────


def test_cleanup_deletes_paired_legacy_rows(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        # Both paired legacy rows should be gone.
        count = conn.execute(
            """SELECT COUNT(*) FROM source_files
               WHERE source_path = ?""",
            (r"C:\Users\<owner>\.claude\projects\c--Users-<owner>-Obsidian\4efecc8b-d706-4667-b922-7476858b2991.jsonl",),
        ).fetchone()[0]
        assert count == 0


def test_cleanup_preserves_relocated_counterpart(migrated_db: Path) -> None:
    with connect(migrated_db) as conn:
        count = conn.execute(
            """SELECT COUNT(*) FROM source_files
               WHERE source_path = ?""",
            (r"D:\<records>\AI Sessions\Claude\claude-code__c--Users-<owner>-Obsidian__4efecc8b-d706-4667-b922-7476858b2991.jsonl",),
        ).fetchone()[0]
        assert count == 1


def test_cleanup_preserves_lonely_legacy(migrated_db: Path) -> None:
    """Legacy rows with no relocated counterpart must NOT be deleted."""
    with connect(migrated_db) as conn:
        count = conn.execute(
            """SELECT COUNT(*) FROM source_files
               WHERE source_path = ?""",
            (r"C:\Users\<owner>\.claude\projects\some-other\aaaa1111-2222-3333-4444-555566667777.jsonl",),
        ).fetchone()[0]
        assert count == 1


def test_cleanup_cascades_messages(migrated_db: Path) -> None:
    """Messages under the deleted legacy source_file are removed too."""
    with connect(migrated_db) as conn:
        reloc_id = conn.execute(
            "SELECT id FROM source_files WHERE source_path = ?",
            (r"D:\<records>\AI Sessions\Claude\claude-code__c--Users-<owner>-Obsidian__4efecc8b-d706-4667-b922-7476858b2991.jsonl",),
        ).fetchone()[0]
        reloc_msgs = conn.execute(
            "SELECT COUNT(*) FROM conversations_messages WHERE source_file_id = ?",
            (reloc_id,),
        ).fetchone()[0]
        assert reloc_msgs == 3  # untouched

        orphan_msgs = conn.execute(
            """SELECT COUNT(*) FROM conversations_messages m
               LEFT JOIN source_files sf ON sf.id = m.source_file_id
               WHERE sf.id IS NULL"""
        ).fetchone()[0]
        assert orphan_msgs == 0


# ── Going-forward UPSERT behavior ────────────────────────────────────────────


def test_renamed_path_updates_existing_row_via_session_uuid_upsert(migrated_db: Path) -> None:
    """A session ingested under a renamed path updates the existing row."""
    with connect(migrated_db) as conn:
        # Take the surviving relocated source_file and re-insert under a new path
        # using the same UPSERT logic the adapter uses.
        existing_id = conn.execute(
            "SELECT id FROM source_files WHERE source_path = ?",
            (r"D:\<records>\AI Sessions\Claude\claude-code__only__bbbb2222-3333-4444-5555-666677778888.jsonl",),
        ).fetchone()[0]
        new_path = r"E:\NewArchive\bbbb2222-3333-4444-5555-666677778888.jsonl"
        cur = conn.execute(
            """INSERT INTO source_files (source_path, source_kind, file_kind, session_uuid, ingested_at)
               VALUES (?, 'claude-code', 'jsonl', 'bbbb2222-3333-4444-5555-666677778888', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
               ON CONFLICT(source_path) DO UPDATE SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               ON CONFLICT(source_kind, session_uuid) WHERE session_uuid IS NOT NULL
                 DO UPDATE SET source_path = excluded.source_path,
                               ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               RETURNING id""",
            (new_path,),
        )
        returned_id = cur.fetchone()[0]
        # Same row updated, not new row created.
        assert returned_id == existing_id

        # source_path is now the new path.
        updated_path = conn.execute(
            "SELECT source_path FROM source_files WHERE id = ?",
            (existing_id,),
        ).fetchone()[0]
        assert updated_path == new_path

        # And no row exists under the old path.
        old_count = conn.execute(
            "SELECT COUNT(*) FROM source_files WHERE source_path = ?",
            (r"D:\<records>\AI Sessions\Claude\claude-code__only__bbbb2222-3333-4444-5555-666677778888.jsonl",),
        ).fetchone()[0]
        assert old_count == 0
