#!/usr/bin/env python3
"""Backfill commit_authorship from git history.

Walks git log for each configured repo, registers the repo with its
default authorship class, and inserts explicit per-commit rows when a
trailer signal is present (Co-Authored-By or Source).

Usage:
    python scripts/backfill_commit_authorship.py [--db PATH] [--dry-run]

Requires: migration 0014_commit_authorship already applied.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

WORKSPACE = Path(os.environ.get("OBSIDIAN_WORKSPACE", Path.home() / "Obsidian"))

REPOS: list[dict] = [
    {
        "name": "vault",
        "path": str(WORKSPACE / "Obsidian"),
        "default_class": "ai-coauthored",
        "notes": "All commits since git init (2026-05-02) are Claude-era",
    },
    {
        "name": "vault-mcp",
        "path": str(WORKSPACE / "vault-mcp"),
        "default_class": "ai-coauthored",
        "notes": "All commits since git init (2026-05-09) are Claude-era",
    },
    {
        "name": "personal-history-db",
        "path": str(WORKSPACE / "personal-history-db"),
        "default_class": "ai-coauthored",
        "notes": "All commits since git init (2026-05-07) are Claude-era",
    },
]


@dataclass
class CommitInfo:
    sha: str
    date: str
    subject: str
    has_co_authored: bool
    source_trailer: str | None


def parse_git_log(repo_path: str) -> list[CommitInfo]:
    """Extract commit metadata from git log."""
    result = subprocess.run(
        [
            "git", "-C", repo_path, "log",
            "--all", "--format=%H%x00%aI%x00%s%x00%b%x00---END---",
        ],
        capture_output=True, text=True, encoding="utf-8",
    )
    if result.returncode != 0:
        print(f"  ERROR: git log failed for {repo_path}: {result.stderr.strip()}")
        return []

    commits: list[CommitInfo] = []
    for block in result.stdout.split("---END---"):
        block = block.strip()
        if not block:
            continue
        parts = block.split("\x00", 3)
        if len(parts) < 4:
            continue
        sha, date, subject, body = parts

        has_co_authored = "Co-Authored-By:" in body or "Co-authored-by:" in body
        source_trailer = None
        for line in body.splitlines():
            if line.startswith("Source:"):
                source_trailer = line.split(":", 1)[1].strip()

        commits.append(CommitInfo(
            sha=sha.strip(),
            date=date.strip(),
            subject=subject.strip(),
            has_co_authored=has_co_authored,
            source_trailer=source_trailer,
        ))
    return commits


def classify(commit: CommitInfo, default_class: str) -> tuple[str, str]:
    """Return (authorship_class, source) for a commit."""
    if commit.has_co_authored:
        return "ai-coauthored", "trailer"

    if commit.source_trailer:
        if commit.source_trailer == "Manual":
            return "rob-authored", "trailer"
        return "ai-coauthored", "trailer"

    return default_class, "heuristic"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, help="Path to phdb database")
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    args = parser.parse_args()

    db_path = args.db or Path(os.environ.get(
        "PHDB_DB_PATH",
        Path.home() / "Obsidian/personal-history-data/personal-history.db",
    ))

    if not db_path.exists():
        print(f"Database not found: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))

    applied = {r[0] for r in conn.execute("SELECT migration_id FROM schema_migrations").fetchall()}
    if "0014_commit_authorship" not in applied:
        print("Migration 0014_commit_authorship not applied. Run migrations first.")
        sys.exit(1)

    total_inserted = 0

    for repo_cfg in REPOS:
        name = repo_cfg["name"]
        path = repo_cfg["path"]
        default = repo_cfg["default_class"]
        notes = repo_cfg["notes"]

        print(f"\n{'='*60}")
        print(f"Repo: {name} ({path})")
        print(f"Default class: {default}")

        if not Path(path).exists():
            print(f"  SKIP — path not found")
            continue

        first_date_result = subprocess.run(
            ["git", "-C", path, "log", "--reverse", "--format=%aI", "--max-count=1"],
            capture_output=True, text=True,
        )
        first_date = first_date_result.stdout.strip() if first_date_result.returncode == 0 else None

        if not args.dry_run:
            conn.execute(
                """INSERT INTO commit_authorship_repos
                       (repo, repo_path, default_class, first_commit_date, notes)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(repo) DO UPDATE SET
                       repo_path = excluded.repo_path,
                       default_class = excluded.default_class,
                       first_commit_date = excluded.first_commit_date,
                       notes = excluded.notes
                """,
                (name, path, default, first_date, notes),
            )
            conn.commit()

        commits = parse_git_log(path)
        print(f"  Total commits: {len(commits)}")

        explicit_count = 0
        for commit in commits:
            cls, source = classify(commit, default)

            if source == "heuristic" and cls == default:
                continue

            explicit_count += 1
            if args.dry_run:
                print(f"  [DRY] {commit.sha[:8]} {cls} ({source}) — {commit.subject[:60]}")
            else:
                conn.execute(
                    """INSERT INTO commit_authorship
                           (repo, sha, authorship_class, source, commit_date, subject)
                       VALUES (?, ?, ?, ?, ?, ?)
                       ON CONFLICT(repo, sha) DO UPDATE SET
                           authorship_class = excluded.authorship_class,
                           source = excluded.source
                    """,
                    (name, commit.sha, cls, source, commit.date, commit.subject),
                )

        if not args.dry_run:
            conn.commit()

        print(f"  Explicit rows inserted: {explicit_count}")
        print(f"  Defaulting to '{default}': {len(commits) - explicit_count}")
        total_inserted += explicit_count

    conn.close()
    print(f"\n{'='*60}")
    print(f"Done. {total_inserted} explicit rows written.")
    if args.dry_run:
        print("(dry-run — no database changes)")


if __name__ == "__main__":
    main()
