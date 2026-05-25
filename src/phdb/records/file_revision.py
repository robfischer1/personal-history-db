"""FileRevision — one row of the file_revisions table (migration 0039).

Per the Git for Ideas plan (Outputs/Plans/Git for Ideas.md):
one row per (repo, commit_sha, file_path) where a markdown file changed.
Bodies are not stored — git already holds them; this record references
``git_blob_sha`` so callers materialize via ``git cat-file -p``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

ChangeType = Literal["add", "modify", "delete", "rename"]
Authorship = Literal["rob", "ai"]


@dataclass(frozen=True)
class FileRevision:
    """One per-commit per-file revision row."""

    repo: str
    commit_sha: str
    file_path: str
    git_blob_sha: str
    change_type: ChangeType
    authorship: Authorship
    parent_blob_sha: str | None = None
    prior_file_path: str | None = None
    summary: str | None = None
    summary_model: str | None = None
    summary_generated_at: str | None = None
