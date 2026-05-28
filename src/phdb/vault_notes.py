"""vault_notes — current-state note index over the vault.

Phase 8 of the Git for Ideas plan. One row per vault markdown file that
has ever existed, holding the latest body text, frontmatter metadata,
and lifecycle status (live / dissolved / deleted).

Public functions:

  - ``upsert(conn, ...) -> int``
        Insert or update a vault_notes row. Returns the row ID.
  - ``mark_deleted(conn, file_path, commit_sha) -> bool``
        Flip a note to 'deleted' (or 'dissolved' if dissolution match).
  - ``mark_renamed(conn, old_path, new_path, commit_sha) -> bool``
        Update file_path for a rename.
  - ``lookup(conn, query) -> dict | None``
        Exact match on name or file_path.
  - ``search(conn, query, limit) -> list[dict]``
        FTS5 search over name + description + body.
  - ``list_notes(conn, status, at_type, limit) -> list[dict]``
        Filtered browse.
  - ``read_note(conn, name_or_path) -> str | None``
        Return body text for one note.
  - ``backfill(conn, repo_root) -> dict``
        Populate vault_notes from file_revisions + live disk state.
  - ``stats(conn) -> dict``
        Aggregate counters.

All functions take ``sqlite3.Connection`` as first arg — stateless;
callers own connection lifecycle.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from phdb.log import get_logger

log = get_logger("phdb.vault_notes")

_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


# ---------------------------------------------------------------------------
# Frontmatter extraction (minimal — same approach as file_revisions plugin)
# ---------------------------------------------------------------------------


def _extract_fm_value(fm_text: str, key: str) -> str | None:
    for line in fm_text.splitlines():
        stripped = line.strip()
        if stripped.startswith(f'"{key}"') or stripped.startswith(f"'{key}'") or stripped.startswith(f"{key}"):
            if ":" not in stripped:
                continue
            _, _, value = stripped.partition(":")
            value = value.strip().strip('"').strip("'")
            return value if value else None
    return None


def _parse_frontmatter(body: str) -> dict[str, str | None]:
    m = _FRONTMATTER_RE.match(body)
    if m is None:
        return {}
    fm_text = m.group(1)
    return {
        "name": _extract_fm_value(fm_text, "name"),
        "description": _extract_fm_value(fm_text, "description"),
        "at_type": _extract_fm_value(fm_text, "@type"),
    }


def _name_from_path(file_path: str) -> str:
    return Path(file_path).stem


# ---------------------------------------------------------------------------
# Row mapping
# ---------------------------------------------------------------------------

_ROW_COLUMNS = (
    "id, schema_type, file_path, name, description, at_type, status,"
    " body, latest_blob_sha, latest_commit_sha, first_seen_commit,"
    " authorship, captured_at, updated_at"
)

_ROW_KEYS = [
    "id", "schema_type", "file_path", "name", "description", "at_type",
    "status", "body", "latest_blob_sha", "latest_commit_sha",
    "first_seen_commit", "authorship", "captured_at", "updated_at",
]

_LIST_COLUMNS = (
    "id, file_path, name, description, at_type, status,"
    " authorship, updated_at"
)

_LIST_KEYS = [
    "id", "file_path", "name", "description", "at_type", "status",
    "authorship", "updated_at",
]


def _row_to_dict(row: tuple[Any, ...], keys: list[str] | None = None) -> dict[str, Any]:
    return dict(zip(keys or _ROW_KEYS, row, strict=False))


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


def upsert(
    conn: sqlite3.Connection,
    *,
    file_path: str,
    body: str,
    blob_sha: str,
    commit_sha: str,
    authorship: str | None = None,
    is_first: bool = False,
) -> int:
    """Insert or update a vault_notes row. Returns the row ID."""
    fm = _parse_frontmatter(body)
    name = fm.get("name") or _name_from_path(file_path)
    description = fm.get("description")
    at_type = fm.get("at_type")

    existing = conn.execute(
        "SELECT id, first_seen_commit FROM vault_notes WHERE file_path = ?",
        (file_path,),
    ).fetchone()

    if existing is not None:
        row_id = existing[0]
        first_seen = existing[1]
        conn.execute(
            "UPDATE vault_notes SET"
            " name = ?, description = ?, at_type = ?, body = ?,"
            " latest_blob_sha = ?, latest_commit_sha = ?,"
            " first_seen_commit = COALESCE(?, first_seen_commit),"
            " authorship = COALESCE(?, authorship),"
            " status = 'live',"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            " WHERE id = ?",
            (name, description, at_type, body, blob_sha, commit_sha,
             commit_sha if is_first else None,
             authorship, row_id),
        )
        return row_id

    cur = conn.execute(
        "INSERT INTO vault_notes"
        " (file_path, name, description, at_type, body,"
        "  latest_blob_sha, latest_commit_sha, first_seen_commit,"
        "  authorship, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'live')",
        (file_path, name, description, at_type, body,
         blob_sha, commit_sha, commit_sha, authorship),
    )
    return cur.lastrowid  # type: ignore[return-value]


def mark_deleted(
    conn: sqlite3.Connection,
    file_path: str,
    commit_sha: str,
) -> bool:
    """Flip a note to 'deleted' or 'dissolved'. Returns True if row existed."""
    existing = conn.execute(
        "SELECT id FROM vault_notes WHERE file_path = ?",
        (file_path,),
    ).fetchone()
    if existing is None:
        return False

    status = _check_dissolution(conn, file_path)

    conn.execute(
        "UPDATE vault_notes SET"
        " status = ?, latest_commit_sha = ?,"
        " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        " WHERE file_path = ?",
        (status, commit_sha, file_path),
    )
    return True


def mark_renamed(
    conn: sqlite3.Connection,
    old_path: str,
    new_path: str,
    commit_sha: str,
) -> bool:
    """Update file_path for a rename. Returns True if row existed."""
    existing = conn.execute(
        "SELECT id FROM vault_notes WHERE file_path = ?",
        (old_path,),
    ).fetchone()
    if existing is None:
        return False

    conn.execute(
        "UPDATE vault_notes SET"
        " file_path = ?, latest_commit_sha = ?,"
        " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        " WHERE file_path = ?",
        (new_path, commit_sha, old_path),
    )
    return True


def _check_dissolution(conn: sqlite3.Connection, file_path: str) -> str:
    """Return 'dissolved' if a dissolution registry match exists, else 'deleted'."""
    try:
        row = conn.execute(
            "SELECT 1 FROM file_revision_dissolutions frd"
            " JOIN file_revisions fr ON fr.id = frd.file_revision_pk"
            " WHERE fr.file_path = ? LIMIT 1",
            (file_path,),
        ).fetchone()
        return "dissolved" if row else "deleted"
    except sqlite3.OperationalError:
        return "deleted"


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------


def lookup(
    conn: sqlite3.Connection,
    query: str,
) -> dict[str, Any] | None:
    """Exact match on name or file_path."""
    row = conn.execute(
        f"SELECT {_ROW_COLUMNS} FROM vault_notes"
        " WHERE file_path = ? OR name = ? LIMIT 1",
        (query, query),
    ).fetchone()
    return _row_to_dict(row) if row else None


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """FTS5 search over name + description + body."""
    rows = conn.execute(
        "SELECT vn.id, vn.file_path, vn.name, vn.description,"
        "  vn.at_type, vn.status, vn.authorship, vn.updated_at,"
        "  snippet(vault_notes_fts, 2, '»', '«', '…', 40) AS snippet"
        " FROM vault_notes_fts fts"
        " JOIN vault_notes vn ON vn.id = fts.rowid"
        " WHERE vault_notes_fts MATCH ?"
        " ORDER BY rank"
        " LIMIT ?",
        (query, limit),
    ).fetchall()
    keys = _LIST_KEYS + ["snippet"]
    return [_row_to_dict(r, keys) for r in rows]


def list_notes(
    conn: sqlite3.Connection,
    *,
    status: str | None = None,
    at_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Filtered browse."""
    clauses: list[str] = []
    params: list[Any] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if at_type is not None:
        clauses.append("at_type = ?")
        params.append(at_type)
    where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
    params.append(limit)
    rows = conn.execute(
        f"SELECT {_LIST_COLUMNS} FROM vault_notes{where}"
        " ORDER BY updated_at DESC LIMIT ?",
        params,
    ).fetchall()
    return [_row_to_dict(r, _LIST_KEYS) for r in rows]


def read_note(
    conn: sqlite3.Connection,
    name_or_path: str,
) -> str | None:
    """Return body text for one note."""
    row = conn.execute(
        "SELECT body FROM vault_notes"
        " WHERE file_path = ? OR name = ? LIMIT 1",
        (name_or_path, name_or_path),
    ).fetchone()
    return row[0] if row else None


def stats(
    conn: sqlite3.Connection,
) -> dict[str, Any]:
    """Aggregate counters."""
    total = conn.execute("SELECT COUNT(*) FROM vault_notes").fetchone()[0]
    if total == 0:
        return {"total": 0, "by_status": {}, "by_at_type": [], "with_body": 0}

    by_status = dict(conn.execute(
        "SELECT status, COUNT(*) FROM vault_notes GROUP BY status",
    ).fetchall())

    by_at_type = conn.execute(
        "SELECT at_type, COUNT(*) AS cnt FROM vault_notes"
        " GROUP BY at_type ORDER BY cnt DESC LIMIT 15",
    ).fetchall()

    with_body = conn.execute(
        "SELECT COUNT(*) FROM vault_notes WHERE body IS NOT NULL",
    ).fetchone()[0]

    return {
        "total": total,
        "by_status": by_status,
        "by_at_type": [{"at_type": r[0], "count": r[1]} for r in by_at_type],
        "with_body": with_body,
    }


# ---------------------------------------------------------------------------
# Backfill — populate from file_revisions + live disk
# ---------------------------------------------------------------------------


def backfill(
    conn: sqlite3.Connection,
    repo_root: Path,
    *,
    repo: str = "vault",
) -> dict[str, int]:
    """Populate vault_notes from file_revisions + live disk state.

    Walks the latest file_revisions row per unique file_path, reads the
    current body from disk (if still present) or from git blob (for
    deleted/dissolved files), and upserts into vault_notes.
    """
    import subprocess

    rows = conn.execute(
        "SELECT file_path,"
        " MAX(CASE WHEN change_type = 'add' THEN commit_sha END) AS first_commit,"
        " (SELECT fr2.commit_sha FROM file_revisions fr2"
        "  WHERE fr2.repo = fr.repo AND fr2.file_path = fr.file_path"
        "  ORDER BY fr2.captured_at DESC LIMIT 1) AS latest_commit,"
        " (SELECT fr3.git_blob_sha FROM file_revisions fr3"
        "  WHERE fr3.repo = fr.repo AND fr3.file_path = fr.file_path"
        "  ORDER BY fr3.captured_at DESC LIMIT 1) AS latest_blob,"
        " (SELECT fr4.change_type FROM file_revisions fr4"
        "  WHERE fr4.repo = fr.repo AND fr4.file_path = fr.file_path"
        "  ORDER BY fr4.captured_at DESC LIMIT 1) AS latest_change,"
        " (SELECT fr5.authorship FROM file_revisions fr5"
        "  WHERE fr5.repo = fr.repo AND fr5.file_path = fr.file_path"
        "  ORDER BY fr5.captured_at DESC LIMIT 1) AS authorship,"
        " (SELECT fr6.summary FROM file_revisions fr6"
        "  WHERE fr6.repo = fr.repo AND fr6.file_path = fr.file_path"
        "  ORDER BY fr6.captured_at DESC LIMIT 1) AS latest_summary"
        " FROM file_revisions fr"
        " WHERE fr.repo = ?"
        " GROUP BY fr.file_path",
        (repo,),
    ).fetchall()

    inserted = 0
    updated = 0
    deleted = 0
    dissolved = 0
    skipped = 0

    for r in rows:
        file_path, first_commit, latest_commit, latest_blob, latest_change, authorship, summary = r

        if not first_commit:
            first_commit = latest_commit

        disk_path = repo_root / file_path
        body: str | None = None

        if latest_change == "delete":
            if latest_blob and set(latest_blob) != {"0"}:
                pass
            parent_row = conn.execute(
                "SELECT parent_blob_sha FROM file_revisions"
                " WHERE repo = ? AND file_path = ? AND change_type = 'delete'"
                " ORDER BY captured_at DESC LIMIT 1",
                (repo, file_path),
            ).fetchone()
            if parent_row and parent_row[0] and set(parent_row[0]) != {"0"}:
                try:
                    result = subprocess.run(
                        ["git", "-C", str(repo_root), "cat-file", "-p", parent_row[0]],
                        capture_output=True, check=False,
                    )
                    if result.returncode == 0:
                        body = result.stdout.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    pass
        elif disk_path.exists():
            try:
                body = disk_path.read_text(encoding="utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                pass
        else:
            if latest_blob and set(latest_blob) != {"0"}:
                try:
                    result = subprocess.run(
                        ["git", "-C", str(repo_root), "cat-file", "-p", latest_blob],
                        capture_output=True, check=False,
                    )
                    if result.returncode == 0:
                        body = result.stdout.decode("utf-8", errors="replace")
                except Exception:  # noqa: BLE001
                    pass

        if body is None:
            skipped += 1
            continue

        fm = _parse_frontmatter(body)
        name = fm.get("name") or _name_from_path(file_path)
        description = fm.get("description") or (summary if summary and not summary.startswith("[skipped]") else None)
        at_type = fm.get("at_type")

        if latest_change == "delete":
            status = _check_dissolution(conn, file_path)
            if status == "dissolved":
                dissolved += 1
            else:
                deleted += 1
        else:
            if not disk_path.exists():
                status = _check_dissolution(conn, file_path)
                if status == "dissolved":
                    dissolved += 1
                else:
                    deleted += 1
            else:
                status = "live"

        existing = conn.execute(
            "SELECT id FROM vault_notes WHERE file_path = ?",
            (file_path,),
        ).fetchone()

        blob_sha = latest_blob if latest_blob and set(latest_blob) != {"0"} else ""

        if existing:
            conn.execute(
                "UPDATE vault_notes SET"
                " name = ?, description = ?, at_type = ?, body = ?,"
                " latest_blob_sha = ?, latest_commit_sha = ?,"
                " first_seen_commit = ?, authorship = ?, status = ?,"
                " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
                " WHERE id = ?",
                (name, description, at_type, body, blob_sha,
                 latest_commit, first_commit, authorship, status,
                 existing[0]),
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO vault_notes"
                " (file_path, name, description, at_type, body,"
                "  latest_blob_sha, latest_commit_sha, first_seen_commit,"
                "  authorship, status)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (file_path, name, description, at_type, body,
                 blob_sha, latest_commit, first_commit, authorship, status),
            )
            inserted += 1

        if (inserted + updated + deleted + dissolved) % 500 == 0:
            conn.commit()

    conn.commit()

    return {
        "inserted": inserted,
        "updated": updated,
        "deleted": deleted,
        "dissolved": dissolved,
        "skipped": skipped,
        "total": inserted + updated,
    }
