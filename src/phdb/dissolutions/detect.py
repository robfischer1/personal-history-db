"""dissolution candidate auto-detect — Phase 3 heuristic.

Scans staged + recently-committed git changes for a dissolution signal:

  Signal: >= 10 deleted .md files in a single commit OR the commit-message
  subject contains 'Dissolution' / 'Dissolve' / 'Migrate to DB' (case-insensitive).

Output (JSON-friendly dict):
    {
      "detected": bool,
      "signal": str,                        # 'subject_keyword' / 'bulk_delete' / 'none'
      "plan_slug_guess": str | None,
      "migration_id_guess": str | None,
      "files": list[str],                   # deleted .md files in the candidate commit
      "suggested_target_schemas": list[str],
    }

Wired into ``end-session/orchestrate.py --pre`` under
``report['dissolution_candidate']`` (Phase 3 hook). Non-blocking — when
no signal is present, ``detected`` is False and the orchestrator
report still validates.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

_KEYWORD_RE = re.compile(
    r"(?:dissolution|dissolve|migrate to db|dissolves?\s+to\s+db)",
    re.IGNORECASE,
)
_BULK_DELETE_THRESHOLD = 10


def _git(args: list[str], *, cwd: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
        )
        return result.returncode, result.stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return -1, ""


def _resolve_repo_root(override: str | None = None) -> Path:
    if override:
        return Path(override).resolve()
    # Walk upward looking for .git
    cwd = Path.cwd().resolve()
    for parent in [cwd, *cwd.parents]:
        if (parent / ".git").exists():
            return parent
    # Fallback to vault default
    return Path.home() / "Forge" / "Obsidian"


def _staged_deleted_md(repo_root: Path) -> list[str]:
    rc, out = _git(
        ["diff", "--cached", "--diff-filter=D", "--name-only"],
        cwd=repo_root,
    )
    if rc != 0:
        return []
    return [line.strip() for line in out.splitlines()
            if line.strip().endswith(".md")]


def _last_commit_subject(repo_root: Path) -> tuple[str, str]:
    """Return (sha, subject) of HEAD commit."""
    rc, out = _git(["log", "-1", "--pretty=format:%H%n%s"], cwd=repo_root)
    if rc != 0 or "\n" not in out:
        return "", ""
    sha, _, subject = out.partition("\n")
    return sha.strip(), subject.strip()


def _last_commit_deleted_md(repo_root: Path) -> list[str]:
    rc, out = _git(
        ["log", "-1", "--diff-filter=D", "--name-only", "--pretty=format:"],
        cwd=repo_root,
    )
    if rc != 0:
        return []
    return [line.strip() for line in out.splitlines()
            if line.strip().endswith(".md")]


def _last_commit_added_sql_migration(repo_root: Path) -> str | None:
    """Find a migration_id from any SQL migration file added in the last commit."""
    rc, out = _git(
        ["log", "-1", "--diff-filter=A", "--name-only", "--pretty=format:"],
        cwd=repo_root,
    )
    if rc != 0:
        return None
    for line in out.splitlines():
        path = line.strip()
        if path.endswith(".sql") and "/migrations/" in path.replace("\\", "/"):
            stem = Path(path).stem
            return stem
    return None


def _guess_plan_slug(subject: str, files: list[str]) -> str | None:
    """Best-effort plan slug guess from commit subject + file paths."""
    subject_lower = subject.lower()
    # Try matching known wave keywords first
    if "consumed media" in subject_lower or any(
        "Entities/Books/" in f or "Entities/Games/" in f for f in files
    ):
        return "consumed-media-dissolution"
    if "tasks" in subject_lower and "dissolu" in subject_lower:
        return "tasks-projects-dissolution"
    if "handoff" in subject_lower:
        return "handoff-dissolution"
    if "articles" in subject_lower or any(
        "References/" in f or "Resources/Articles" in f for f in files
    ):
        return "articles-dissolution-pilot"

    # Generic: slugify the commit subject
    match = _KEYWORD_RE.search(subject)
    if match is not None:
        slug = re.sub(r"[^a-z0-9]+", "-", subject_lower).strip("-")
        return slug[:60] if slug else None
    return None


def _suggest_target_schemas(files: list[str]) -> list[str]:
    """Heuristic mapping from file paths to Schema.org @types."""
    schemas: set[str] = set()
    for f in files:
        if "Entities/Books/" in f:
            schemas.add("Book")
        elif "Entities/Games/" in f:
            schemas.add("VideoGame")
        elif "Entities/Movies/" in f:
            schemas.add("Movie")
        elif "Entities/Podcasts/" in f:
            schemas.add("PodcastSeries")
        elif "Entities/TV Series/" in f:
            schemas.add("TVSeries")
        elif "Entities/YouTube Channels/" in f or "Entities/Twitch Channels/" in f:
            schemas.add("WebSite")
        elif f.startswith("Outputs/Tasks/") or "/Tasks/" in f:
            schemas.add("Action")
        elif f.startswith("System/Handoffs/"):
            schemas.add("Action")
        elif (
            f.startswith("References/") or f.startswith("Resources/Articles")
            or "/Articles/" in f or "/Clippings/" in f
        ):
            schemas.add("Article")
    return sorted(schemas)


def detect_candidate(repo_root: str | None = None) -> dict[str, Any]:
    """Run the heuristic and return a candidate dict.

    Non-blocking — any git failure returns ``{"detected": False, ...}``.
    """
    root = _resolve_repo_root(repo_root)

    staged = _staged_deleted_md(root)
    last_sha, last_subject = _last_commit_subject(root)
    last_deletes = _last_commit_deleted_md(root)

    # Subject keyword check
    has_keyword_staged = False
    if staged:
        # Staged changes have no subject yet — synthesize from index
        # The signal is the bulk-delete count.
        pass
    has_keyword_last = bool(_KEYWORD_RE.search(last_subject))

    # Bulk-delete check
    staged_bulk = len(staged) >= _BULK_DELETE_THRESHOLD
    last_bulk = len(last_deletes) >= _BULK_DELETE_THRESHOLD

    # Signal precedence: staged bulk > subject keyword in last commit > last bulk.
    # Both subject_keyword and bulk_delete signals trigger detection; the
    # "carry-forward" sweep in last commit pattern triggers bulk_delete but the
    # AI can override via /end --no-dissolution-check.
    if staged_bulk:
        signal = "bulk_delete_staged"
        detected = True
    elif has_keyword_last and last_deletes:
        signal = "subject_keyword"
        detected = True
    elif last_bulk:
        signal = "bulk_delete_last_commit"
        detected = True
    else:
        signal = "none"
        detected = False

    files = staged if staged else last_deletes

    plan_slug_guess = None
    migration_id_guess = None
    suggested_schemas: list[str] = []

    if detected:
        # Use last-commit context for slug/migration guessing
        plan_slug_guess = _guess_plan_slug(last_subject, files)
        migration_id_guess = _last_commit_added_sql_migration(root)
        suggested_schemas = _suggest_target_schemas(files)

    return {
        "detected": detected,
        "signal": signal,
        "plan_slug_guess": plan_slug_guess,
        "migration_id_guess": migration_id_guess,
        "files": files,
        "suggested_target_schemas": suggested_schemas,
        "repo_root": str(root),
        "last_commit_sha": last_sha,
        "last_commit_subject": last_subject,
    }
