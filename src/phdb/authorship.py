"""Commit-authorship annotation layer.

Maps (repo, commit_sha) → authorship_class so the Skill Graph readiness
engine can filter AI-co-authored commits out of Rob-unaided programming
practice events.

Two-level lookup: per-commit rows in ``commit_authorship`` override the
per-repo default in ``commit_authorship_repos``.  Repos without an
explicit default fall back to ``ai-coauthored``.
"""

from __future__ import annotations

import sqlite3
from typing import Literal

AuthorshipClass = Literal["rob-authored", "ai-coauthored", "external"]

_FALLBACK_DEFAULT: AuthorshipClass = "ai-coauthored"


def get_authorship(
    conn: sqlite3.Connection,
    repo: str,
    sha: str,
) -> AuthorshipClass:
    """Return the authorship class for a single commit.

    Resolution order:
    1. Explicit row in ``commit_authorship`` (exact sha match).
    2. Per-repo ``default_class`` from ``commit_authorship_repos``.
    3. Module-level fallback (``ai-coauthored``).
    """
    row = conn.execute(
        "SELECT authorship_class FROM commit_authorship WHERE repo = ? AND sha = ?",
        (repo, sha),
    ).fetchone()
    if row:
        return row[0]

    repo_row = conn.execute(
        "SELECT default_class FROM commit_authorship_repos WHERE repo = ?",
        (repo,),
    ).fetchone()
    if repo_row:
        return repo_row[0]

    return _FALLBACK_DEFAULT


def get_authorship_batch(
    conn: sqlite3.Connection,
    repo: str,
    shas: list[str],
) -> dict[str, AuthorshipClass]:
    """Return authorship classes for multiple commits in one repo."""
    if not shas:
        return {}

    placeholders = ",".join("?" for _ in shas)
    rows = conn.execute(
        f"SELECT sha, authorship_class FROM commit_authorship "
        f"WHERE repo = ? AND sha IN ({placeholders})",
        [repo, *shas],
    ).fetchall()
    explicit = {r[0]: r[1] for r in rows}

    repo_row = conn.execute(
        "SELECT default_class FROM commit_authorship_repos WHERE repo = ?",
        (repo,),
    ).fetchone()
    default: AuthorshipClass = repo_row[0] if repo_row else _FALLBACK_DEFAULT

    return {sha: explicit.get(sha, default) for sha in shas}


def register_repo(
    conn: sqlite3.Connection,
    repo: str,
    *,
    repo_path: str | None = None,
    default_class: AuthorshipClass = "ai-coauthored",
    first_commit_date: str | None = None,
    notes: str | None = None,
) -> None:
    """Insert or update a repo's default authorship class."""
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
        (repo, repo_path, default_class, first_commit_date, notes),
    )
    conn.commit()


def classify_commit(
    conn: sqlite3.Connection,
    repo: str,
    sha: str,
    authorship_class: AuthorshipClass,
    *,
    source: str = "manual",
    commit_date: str | None = None,
    subject: str | None = None,
) -> None:
    """Insert or update a single commit's authorship classification."""
    conn.execute(
        """INSERT INTO commit_authorship
               (repo, sha, authorship_class, source, commit_date, subject)
           VALUES (?, ?, ?, ?, ?, ?)
           ON CONFLICT(repo, sha) DO UPDATE SET
               authorship_class = excluded.authorship_class,
               source = excluded.source
        """,
        (repo, sha, authorship_class, source, commit_date, subject),
    )
    conn.commit()
