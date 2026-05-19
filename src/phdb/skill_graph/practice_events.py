"""Practice-event extraction with D7 provenance filtering.

For each in-scope repo:

1. Run ``git log`` to enumerate commits since some cutoff.
2. Batch-query ``phdb.authorship.get_authorship_batch`` to classify each.
3. Keep only ``rob-authored`` commits — AI-co-authored commits do **not**
   refresh *unaided* readiness (per Skill Graph D7).
4. Map each commit to one or more disciplines via an injectable mapper.

Pure mapping/filtering logic lives in ``filter_and_map`` (testable without
I/O); the orchestrator ``extract_practice_events`` wires in git + the
provenance lookup.
"""

from __future__ import annotations

import sqlite3
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from phdb.authorship import get_authorship_batch


@dataclass(frozen=True)
class CommitInfo:
    """Minimal commit metadata used by the discipline mapper."""

    sha: str
    timestamp: str  # ISO 8601 author date
    subject: str
    repo: str


@dataclass(frozen=True)
class PracticeEvent:
    """A single unaided practice event — one rob-authored commit."""

    timestamp: str  # ISO 8601 author date
    discipline: str
    repo: str
    sha: str


DisciplineMapper = Callable[[CommitInfo], list[str]]


def default_discipline_mapper(_commit: CommitInfo) -> list[str]:
    """V1 default: every rob-authored commit counts as Programming practice.

    Phase 5+ refines this with file-extension heuristics (`.py` → Python,
    `.js`/`.tsx` → JavaScript / React, etc.) — captured as a deferred item.
    """
    return ["Programming"]


def filter_and_map(
    commits: list[CommitInfo],
    authorship: dict[str, str],
    discipline_mapper: DisciplineMapper,
) -> dict[str, list[PracticeEvent]]:
    """Pure function — filter ``commits`` to rob-authored and map to disciplines.

    Args:
        commits: All extracted commits across repos.
        authorship: Map ``sha → authorship_class`` (use
            ``get_authorship_batch`` per repo, then merge).
        discipline_mapper: Maps a commit → list of discipline labels.

    Returns:
        ``{discipline_label: [PracticeEvent, ...]}``.
    """
    by_discipline: dict[str, list[PracticeEvent]] = {}
    for commit in commits:
        if authorship.get(commit.sha) != "rob-authored":
            continue
        for discipline in discipline_mapper(commit):
            by_discipline.setdefault(discipline, []).append(
                PracticeEvent(
                    timestamp=commit.timestamp,
                    discipline=discipline,
                    repo=commit.repo,
                    sha=commit.sha,
                )
            )
    return by_discipline


def git_log(repo_path: Path, repo_name: str, *, since_iso: str | None = None) -> list[CommitInfo]:
    """Run ``git log`` and return parsed commit metadata.

    Format: tab-delimited ``%H\\t%aI\\t%s`` — full sha, author ISO date,
    subject. Empty list on git failure (uninitialized repo, git missing).
    """
    cmd = ["git", "log", "--format=%H%x09%aI%x09%s"]
    if since_iso:
        cmd.extend(["--since", since_iso])

    try:
        result = subprocess.run(
            cmd,
            cwd=str(repo_path),
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, FileNotFoundError):
        return []

    if result.returncode != 0:
        return []

    commits: list[CommitInfo] = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("\t", 2)
        if len(parts) < 3:
            continue
        commits.append(
            CommitInfo(sha=parts[0], timestamp=parts[1], subject=parts[2], repo=repo_name)
        )
    return commits


def extract_practice_events(
    repos: list[tuple[str, Path]],
    *,
    conn: sqlite3.Connection,
    since_iso: str | None = None,
    discipline_mapper: DisciplineMapper = default_discipline_mapper,
) -> dict[str, list[PracticeEvent]]:
    """Pull rob-authored commits across ``repos``, mapped to disciplines.

    Args:
        repos: ``[(repo_name, repo_path), ...]``. ``repo_name`` must match the
            key in the ``commit_authorship`` table.
        conn: A phdb sqlite connection — used for provenance lookups.
        since_iso: Only commits after this date (ISO 8601). ``None`` = full
            history.
        discipline_mapper: How to assign disciplines to a commit.

    Returns:
        ``{discipline_label: [PracticeEvent, ...]}``.
    """
    all_commits: list[CommitInfo] = []
    authorship_by_sha: dict[str, str] = {}

    for repo_name, repo_path in repos:
        commits = git_log(repo_path, repo_name, since_iso=since_iso)
        if not commits:
            continue
        all_commits.extend(commits)
        shas = [c.sha for c in commits]
        authorship_by_sha.update(get_authorship_batch(conn, repo_name, shas))

    return filter_and_map(all_commits, authorship_by_sha, discipline_mapper)
