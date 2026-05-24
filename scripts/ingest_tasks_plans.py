#!/usr/bin/env python3
"""One-shot ingestion script — reads all task/plan files and inserts into phdb.

Walks Outputs/Tasks/ + System/Tasks/ (tasks) and Outputs/Plans/ + System/Plans/
(plans). Idempotent via INSERT OR IGNORE + raw_hash dedup.

Usage:
  python scripts/ingest_tasks_plans.py [--vault-root PATH] [--db PATH]
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PHDB_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PHDB_ROOT / "src"))

from phdb.formats.vault_tasks_plans_md import (  # noqa: E402
    parse_plans,
    parse_tasks,
)


def _register_source_file(
    conn: sqlite3.Connection,
    source_path: Path,
    source_kind: str,
) -> int:
    cur = conn.execute(
        """INSERT INTO source_files
           (source_path, source_org, file_kind, source_kind, session_uuid, ingested_at)
           VALUES (?, ?, ?, ?, NULL,
                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
           ON CONFLICT(source_path) DO UPDATE
             SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           RETURNING id""",
        (str(source_path), None, "md", source_kind),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])


_TASK_INSERT = """
INSERT OR IGNORE INTO tasks
    (schema_type, name, identifier, tier, status, effort, maintenance,
     project, created, updated, closure_date, closure_evidence,
     file_path, raw_hash, source_file_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_PLAN_INSERT = """
INSERT OR IGNORE INTO plans
    (schema_type, name, identifier, description, status, phase,
     effort, maintenance, created, updated,
     file_path, raw_hash, source_file_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def ingest_tasks(conn: sqlite3.Connection, dirs: list[Path]) -> dict[str, int]:
    counts = {"yielded": 0, "inserted": 0, "skipped": 0}
    for task_dir in dirs:
        if not task_dir.is_dir():
            print(f"  SKIP (not found): {task_dir}")
            continue
        sf_id = _register_source_file(conn, task_dir, "vault-tasks")
        for record in parse_tasks(task_dir):
            counts["yielded"] += 1
            cur = conn.execute(_TASK_INSERT, [
                "Action",
                record.name,
                record.identifier,
                record.tier,
                record.status,
                record.effort,
                record.maintenance,
                record.project,
                record.created,
                record.updated,
                record.closure_date,
                record.closure_evidence,
                record.file_path,
                record.provenance.raw_hash,
                sf_id,
            ])
            if cur.rowcount > 0:
                counts["inserted"] += 1
            else:
                counts["skipped"] += 1
    conn.commit()
    return counts


def ingest_plans(conn: sqlite3.Connection, dirs: list[Path]) -> dict[str, int]:
    counts = {"yielded": 0, "inserted": 0, "skipped": 0}
    for plan_dir in dirs:
        if not plan_dir.is_dir():
            print(f"  SKIP (not found): {plan_dir}")
            continue
        sf_id = _register_source_file(conn, plan_dir, "vault-plans")
        for record in parse_plans(plan_dir):
            counts["yielded"] += 1
            cur = conn.execute(_PLAN_INSERT, [
                "Plan",
                record.name,
                record.identifier,
                record.description,
                record.status,
                record.phase,
                record.effort,
                record.maintenance,
                record.created,
                record.updated,
                record.file_path,
                record.provenance.raw_hash,
                sf_id,
            ])
            if cur.rowcount > 0:
                counts["inserted"] += 1
            else:
                counts["skipped"] += 1
    conn.commit()
    return counts


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest vault tasks and plans into phdb.")
    ap.add_argument("--vault-root", type=Path,
                    default=Path(os.path.expanduser("~/Forge/Obsidian")))
    ap.add_argument("--db", type=Path,
                    default=Path(os.path.expanduser("~/Forge/personal-history-data/personal-history.db")))
    args = ap.parse_args()

    vault = args.vault_root
    task_dirs = [vault / "Outputs" / "Tasks", vault / "System" / "Tasks"]
    plan_dirs = [vault / "Outputs" / "Plans", vault / "System" / "Plans"]

    conn = sqlite3.connect(str(args.db))
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")

    print("=== Ingesting tasks ===")
    tc = ingest_tasks(conn, task_dirs)
    print(f"  Tasks: {tc['yielded']} yielded, {tc['inserted']} inserted, {tc['skipped']} skipped")

    print("\n=== Ingesting plans ===")
    pc = ingest_plans(conn, plan_dirs)
    print(f"  Plans: {pc['yielded']} yielded, {pc['inserted']} inserted, {pc['skipped']} skipped")

    # Verification
    task_count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
    plan_count = conn.execute("SELECT COUNT(*) FROM plans").fetchone()[0]
    print("\n=== Verification ===")
    print(f"  tasks table: {task_count} rows")
    print(f"  plans table: {plan_count} rows")

    # Spot-check
    print("\n=== Spot check (5 tasks) ===")
    for row in conn.execute("SELECT name, status, tier FROM tasks ORDER BY name LIMIT 5").fetchall():
        print(f"  {row[0]} | {row[1]} | {row[2]}")

    print("\n=== Spot check (5 plans) ===")
    for row in conn.execute("SELECT name, status, phase FROM plans ORDER BY name LIMIT 5").fetchall():
        print(f"  {row[0]} | {row[1]} | {row[2]}")

    conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
