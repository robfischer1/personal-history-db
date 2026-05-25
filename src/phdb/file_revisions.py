"""file_revisions — Python API over the git-for-ideas intelligence layer.

Implements the materialization + query surface for the
``file_revisions`` typed table (migration 0039). Bodies live in git;
this module shells out to ``git cat-file -p <git_blob_sha>`` against
the repo at ``commit_authorship_repos.repo_path`` to retrieve them.

Public functions:

  - ``materialize(conn, rev_id, repo_root=None) -> str``
        Return the body text of one revision by primary key.
  - ``list_for_path(conn, file_path, *, repo='vault') -> list[dict]``
        Chronological revision history for a vault path.
  - ``latest_for_path(conn, file_path, *, repo='vault') -> dict | None``
        Most recent revision row for a vault path.
  - ``diff(conn, rev_a_id, rev_b_id, *, repo_root=None) -> str``
        Unified diff between two revisions' bodies.
  - ``triple_deltas(conn, rev_id) -> list[dict]``
        Predicate-graph edges added/removed by one revision.
  - ``stats(conn, *, repo='vault') -> dict``
        Aggregate counters — total revisions, daily distribution,
        top-10 most-revised files, summary coverage, authorship split.

The CLI surface (``phdb revision …``) wraps these functions in
``phdb.cli``; the vault-mcp tools (Phase 6) wrap them in
``vault-mcp/server.py``.

All functions take an open ``sqlite3.Connection`` as first argument —
this module is stateless; callers own connection lifecycle (mirrors
``phdb.core.graph`` convention).
"""

from __future__ import annotations

import difflib
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from phdb.log import get_logger

log = get_logger("phdb.file_revisions")


# ---------------------------------------------------------------------------
# Repo path resolution
# ---------------------------------------------------------------------------


def _resolve_repo_root(
    conn: sqlite3.Connection,
    repo: str,
    *,
    override: str | None = None,
) -> Path:
    """Resolve the on-disk path for a configured repo.

    Lookup order:
      1. Explicit override argument.
      2. ``commit_authorship_repos.repo_path`` column for that repo.
      3. ``C:/Users/robfi/Forge/<repo capitalized appropriately>``
         as a last resort (the vault is at ``Forge/Obsidian/`` not
         ``Forge/vault/``, so the fallback is best-effort only).

    The legacy column value points at ``C:/Users/robfi/Obsidian/<repo>``;
    that path no longer exists. The walker updates the column to the
    live path before running, so most production calls take path (2).
    """
    if override:
        return Path(override).resolve()

    row = conn.execute(
        "SELECT repo_path FROM commit_authorship_repos WHERE repo = ?",
        (repo,),
    ).fetchone()
    if row and row[0]:
        candidate = Path(row[0])
        if candidate.exists():
            return candidate.resolve()

    # Last-resort fallback. Names are short; the vault is special-cased.
    workspace = Path.home() / "Forge"
    if repo == "vault":
        candidate = workspace / "Obsidian"
    else:
        candidate = workspace / repo
    return candidate.resolve()


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def materialize(
    conn: sqlite3.Connection,
    rev_id: int,
    *,
    repo_root: str | None = None,
) -> str:
    """Return the body text of one revision.

    For ``change_type = 'delete'`` revisions, the body is the
    pre-deletion blob (the parent's content). Raises FileNotFoundError
    if the row doesn't exist; raises CalledProcessError if git can't
    find the blob (corrupted DB row or wrong repo path).
    """
    row = conn.execute(
        "SELECT repo, git_blob_sha, change_type, parent_blob_sha"
        " FROM file_revisions WHERE id = ?",
        (rev_id,),
    ).fetchone()
    if row is None:
        raise FileNotFoundError(f"file_revisions row id={rev_id} not found")
    repo, git_blob_sha, change_type, parent_blob_sha = row

    # For delete rows the new blob is all-zeros sentinel; the meaningful
    # content is the parent blob. The walker stores '0' * 40 as git_blob_sha
    # for `delete` and the actual content sha as parent_blob_sha.
    sha = parent_blob_sha if change_type == "delete" else git_blob_sha
    if not sha or set(sha) == {"0"}:
        return ""

    root = _resolve_repo_root(conn, repo, override=repo_root)
    return _git_cat_file(root, sha)


def _git_cat_file(repo_root: Path, blob_sha: str) -> str:
    """Run ``git cat-file -p <sha>`` and return stdout decoded as UTF-8."""
    result = subprocess.run(
        ["git", "-C", str(repo_root), "cat-file", "-p", blob_sha],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0:
        raise subprocess.CalledProcessError(
            result.returncode,
            result.args,
            output=result.stdout,
            stderr=result.stderr,
        )
    # git stores blobs as bytes; decode with replacement so the function
    # never throws on unusual encodings.
    return result.stdout.decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Listing / fetch
# ---------------------------------------------------------------------------


_ROW_COLUMNS = (
    "id, repo, commit_sha, file_path, git_blob_sha, parent_blob_sha,"
    " change_type, authorship, prior_file_path, summary, summary_model,"
    " summary_generated_at, captured_at"
)


def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
    keys = [
        "id", "repo", "commit_sha", "file_path", "git_blob_sha",
        "parent_blob_sha", "change_type", "authorship",
        "prior_file_path", "summary", "summary_model",
        "summary_generated_at", "captured_at",
    ]
    return dict(zip(keys, row, strict=False))


def list_for_path(
    conn: sqlite3.Connection,
    file_path: str,
    *,
    repo: str = "vault",
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Return the chronological revision history for a vault path.

    Ordered newest-first by ``captured_at`` so callers see the most
    recent change first. Pass ``limit=None`` (default) for the full
    history.
    """
    sql = (
        f"SELECT {_ROW_COLUMNS} FROM file_revisions"
        " WHERE repo = ? AND file_path = ?"
        " ORDER BY captured_at DESC"
    )
    params: tuple[Any, ...] = (repo, file_path)
    if limit is not None:
        sql += " LIMIT ?"
        params = (*params, limit)
    rows = conn.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def latest_for_path(
    conn: sqlite3.Connection,
    file_path: str,
    *,
    repo: str = "vault",
) -> dict[str, Any] | None:
    """Return the most recent revision row for a vault path, or None."""
    rows = list_for_path(conn, file_path, repo=repo, limit=1)
    return rows[0] if rows else None


def get_revision(
    conn: sqlite3.Connection,
    rev_id: int,
) -> dict[str, Any] | None:
    """Return one revision row by primary key, or None."""
    row = conn.execute(
        f"SELECT {_ROW_COLUMNS} FROM file_revisions WHERE id = ?",
        (rev_id,),
    ).fetchone()
    return _row_to_dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------


def diff(
    conn: sqlite3.Connection,
    rev_a_id: int,
    rev_b_id: int,
    *,
    repo_root: str | None = None,
    context: int = 3,
) -> str:
    """Return a unified diff between two revisions' bodies.

    Both revisions are materialized via ``git cat-file -p`` then piped
    through ``difflib.unified_diff`` — no shell-out to ``diff``,
    because GNU diff is not always on PATH on Windows.

    Header lines name the revision by id + commit_sha[:7] + file_path so
    the diff is self-describing.
    """
    rev_a = get_revision(conn, rev_a_id)
    rev_b = get_revision(conn, rev_b_id)
    if rev_a is None:
        raise FileNotFoundError(f"rev_a id={rev_a_id} not found")
    if rev_b is None:
        raise FileNotFoundError(f"rev_b id={rev_b_id} not found")

    body_a = materialize(conn, rev_a_id, repo_root=repo_root).splitlines(keepends=True)
    body_b = materialize(conn, rev_b_id, repo_root=repo_root).splitlines(keepends=True)

    fromfile = f"rev_{rev_a_id} @{rev_a['commit_sha'][:7]} {rev_a['file_path']}"
    tofile = f"rev_{rev_b_id} @{rev_b['commit_sha'][:7]} {rev_b['file_path']}"

    return "".join(
        difflib.unified_diff(
            body_a, body_b,
            fromfile=fromfile, tofile=tofile,
            n=context,
        )
    )


# ---------------------------------------------------------------------------
# Triple deltas
# ---------------------------------------------------------------------------


def triple_deltas(
    conn: sqlite3.Connection,
    rev_id: int,
) -> list[dict[str, Any]]:
    """Return the predicate-graph deltas attached to one revision.

    Each row is a dict with ``op`` ('add' | 'remove'), the three node
    IDs, and (where resolvable) the human-readable labels.
    """
    rows = conn.execute(
        "SELECT rtd.id, rtd.op,"
        "       rtd.subject_node_pk, n_subj.label,"
        "       rtd.predicate_pk, p.name,"
        "       rtd.object_node_pk, n_obj.label"
        " FROM revision_triple_deltas rtd"
        " LEFT JOIN nodes n_subj ON n_subj.id = rtd.subject_node_pk"
        " LEFT JOIN predicates p ON p.id = rtd.predicate_pk"
        " LEFT JOIN nodes n_obj  ON n_obj.id  = rtd.object_node_pk"
        " WHERE rtd.revision_pk = ?"
        " ORDER BY rtd.op, rtd.id",
        (rev_id,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        out.append({
            "id": r[0],
            "op": r[1],
            "subject_node_pk": r[2],
            "subject_label": r[3],
            "predicate_pk": r[4],
            "predicate_name": r[5],
            "object_node_pk": r[6],
            "object_label": r[7],
        })
    return out


# ---------------------------------------------------------------------------
# Stats — Phase 7
# ---------------------------------------------------------------------------


def stats(
    conn: sqlite3.Connection,
    *,
    repo: str = "vault",
) -> dict[str, Any]:
    """Aggregate stats for ``phdb revision stats``.

    Returns:
        total rows, summary coverage %, authorship split, top-10 most
        revised files, revisions-per-day for the trailing 14 days.
    """
    total = conn.execute(
        "SELECT COUNT(*) FROM file_revisions WHERE repo = ?",
        (repo,),
    ).fetchone()[0]

    if total == 0:
        return {
            "repo": repo,
            "total": 0,
            "by_authorship": {},
            "summary_coverage": 0.0,
            "summary_filled": 0,
            "top_files": [],
            "by_day": [],
        }

    by_authorship = dict(conn.execute(
        "SELECT authorship, COUNT(*) FROM file_revisions"
        " WHERE repo = ? GROUP BY authorship",
        (repo,),
    ).fetchall())

    summary_filled = conn.execute(
        "SELECT COUNT(*) FROM file_revisions"
        " WHERE repo = ? AND summary IS NOT NULL",
        (repo,),
    ).fetchone()[0]
    coverage_pct = (summary_filled / total * 100.0) if total else 0.0

    top_files = conn.execute(
        "SELECT file_path, COUNT(*) AS cnt FROM file_revisions"
        " WHERE repo = ? GROUP BY file_path"
        " ORDER BY cnt DESC LIMIT 10",
        (repo,),
    ).fetchall()

    by_day = conn.execute(
        "SELECT substr(captured_at, 1, 10) AS day, COUNT(*)"
        " FROM file_revisions WHERE repo = ?"
        " GROUP BY day ORDER BY day DESC LIMIT 14",
        (repo,),
    ).fetchall()

    by_change_type = dict(conn.execute(
        "SELECT change_type, COUNT(*) FROM file_revisions"
        " WHERE repo = ? GROUP BY change_type",
        (repo,),
    ).fetchall())

    return {
        "repo": repo,
        "total": total,
        "by_authorship": by_authorship,
        "by_change_type": by_change_type,
        "summary_filled": summary_filled,
        "summary_coverage": round(coverage_pct, 2),
        "top_files": [{"file_path": r[0], "revisions": r[1]} for r in top_files],
        "by_day": [{"day": r[0], "revisions": r[1]} for r in by_day],
    }


# ---------------------------------------------------------------------------
# Rerun helper — Phase 7
# ---------------------------------------------------------------------------


def rerun_commit(
    conn: sqlite3.Connection,
    commit_sha: str,
    *,
    repo: str = "vault",
) -> int:
    """Delete all file_revisions rows for one commit so the walker re-derives.

    Used after a manual authorship reclassification or a parser fix —
    the walker's idempotent upsert won't update existing rows because
    of the UNIQUE INDEX, so the path is delete-and-redrive.

    Cascade on ``revision_triple_deltas`` is declared in the migration,
    so the SQL DELETE here also clears delta rows.

    Returns the number of file_revisions rows deleted.
    """
    cur = conn.execute(
        "DELETE FROM file_revisions WHERE repo = ? AND commit_sha = ?",
        (repo, commit_sha),
    )
    conn.commit()
    return cur.rowcount
