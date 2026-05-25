"""dissolutions — Python API over the vault-DB lifecycle event registry.

Implements declare/link/query primitives for the ``dissolutions``,
``file_revision_dissolutions``, and ``materialization_events`` typed
tables (migration 0041). Phase 0 of the Dissolution Tracking plan
locked the schema; this module wraps the SQL in a stable Python
surface that the CLI (``phdb dissolution …``) and vault-mcp tools wrap.

Public functions (all take an open sqlite3.Connection as first arg):

  - ``declare(...) -> int``
        Insert a dissolution wave row. Enforces Q11 validation:
        when migration_id is None, target_tables must be non-empty
        AND rationale must be present.
  - ``link_file_revisions(...) -> int``
        Bulk insert link rows. Idempotent on (file_revision_pk, dissolution_pk).
  - ``record_materialization(...) -> int``
        Phase 8 / Q13 hook for materializer tools.
  - ``list_for_plan(plan_slug, *, repo='vault') -> list[dict]``
  - ``list_for_migration(migration_id, *, repo='vault') -> list[dict]``
  - ``list_waves(*, repo='vault') -> list[dict]``
        Q8 verbose introspection.
  - ``lookup_vault_path(file_path, *, repo='vault') -> dict``
        Returns the full lifecycle (dissolution + materialization
        events) ordered chronologically.
  - ``audit_invariants(*, repo='vault') -> dict``
        Q8 verbose audit — FK integrity, migration_id existence in
        schema_migrations (when present), orphan link rows, Q11 rule
        satisfied.
  - ``validate_all(*, repo='vault') -> dict``
        Wraps audit_invariants; returns pass/fail summary.

All functions are stateless; callers own connection lifecycle (mirrors
``phdb.file_revisions`` convention).
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from phdb.log import get_logger

log = get_logger("phdb.dissolutions")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    """ISO 8601 timestamp with millisecond precision and Z suffix."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.") + (
        datetime.now(UTC).strftime("%f")[:3] + "Z"
    )


def _resolve_plan_pk(
    conn: sqlite3.Connection,
    plan_slug: str,
) -> int | None:
    """Best-effort resolve plan_slug to plans.id.

    The plans table uses ``identifier`` as the slug-equivalent column.
    Slug formats vary (``articles-dissolution-pilot`` vs
    ``articles-dissolution-pilot-plan``); we try exact + suffix matches.
    Returns None when no row matches — plan_pk is nullable per schema.
    """
    row = conn.execute(
        "SELECT id FROM plans WHERE identifier = ?",
        (plan_slug,),
    ).fetchone()
    if row is not None:
        return int(row[0])

    # Suffix-tolerant — the plans table sometimes carries '-plan' suffix
    row = conn.execute(
        "SELECT id FROM plans WHERE identifier = ? OR identifier = ?"
        " OR identifier LIKE ?",
        (f"{plan_slug}-plan", f"{plan_slug}-spec", f"{plan_slug}%"),
    ).fetchone()
    return int(row[0]) if row is not None else None


# ---------------------------------------------------------------------------
# declare — insert a dissolution wave row
# ---------------------------------------------------------------------------


def declare(
    conn: sqlite3.Connection,
    *,
    plan_slug: str,
    target_schemas: list[str],
    target_tables: list[str],
    dissolved_at: str | None = None,
    migration_id: str | None = None,
    commit_sha: str | None = None,
    rationale: str | None = None,
    declared_by: str = "code",
    repo: str = "vault",
    plan_pk: int | None = None,
) -> int:
    """Declare one dissolution wave.

    Returns the new dissolutions.id. Q11 validation:
      - When migration_id is None: rationale required AND target_tables non-empty.
      - When migration_id is provided: must exist in schema_migrations.

    Idempotent on (plan_pk, migration_id) per Q10 — re-runs with the
    same key return the existing id without inserting. NULL-tolerant
    per SQLite semantics (multiple (plan_pk, NULL) rows allowed).

    Args:
        conn: Open sqlite3.Connection.
        plan_slug: Driving plan identifier (e.g. "consumed-media-dissolution").
        target_schemas: Schema.org @types now owning the content.
        target_tables: phdb table names now owning the content.
        dissolved_at: ISO 8601 timestamp; defaults to now.
        migration_id: Optional schema migration that drove the dissolution.
        commit_sha: Optional git commit; NULL when wave spans multiple.
        rationale: Required when migration_id is None.
        declared_by: 'cowork' / 'code' / 'backfill'.
        repo: Defaults to 'vault'.
        plan_pk: Override plan FK; auto-resolved from plan_slug if omitted.
    """
    # Q11 validation
    if migration_id is None:
        if not target_tables:
            raise ValueError(
                "target_tables must be non-empty when migration_id is None"
            )
        if not rationale:
            raise ValueError(
                "rationale required when migration_id is None"
            )
    else:
        # Q11 — when --migration provided, it must exist in schema_migrations
        row = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id = ?",
            (migration_id,),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"migration_id {migration_id!r} not found in schema_migrations"
            )

    if dissolved_at is None:
        dissolved_at = _now_iso()

    if plan_pk is None:
        plan_pk = _resolve_plan_pk(conn, plan_slug)

    # Idempotency: lookup existing row on (plan_pk, migration_id).
    # SQLite's UNIQUE treats NULLs as distinct, so we have to handle
    # the NULL case explicitly to get true idempotency on backfill re-runs.
    if plan_pk is not None and migration_id is not None:
        existing = conn.execute(
            "SELECT id FROM dissolutions WHERE plan_pk = ? AND migration_id = ?",
            (plan_pk, migration_id),
        ).fetchone()
        if existing is not None:
            log.debug("dissolution already declared", extra={
                "plan_slug": plan_slug, "id": existing[0],
            })
            return int(existing[0])
    elif plan_pk is not None and migration_id is None:
        # For null migrations, dedup on (plan_slug, dissolved_at::date) to
        # avoid duplicate backfill rows for the same wave.
        existing = conn.execute(
            "SELECT id FROM dissolutions"
            " WHERE plan_slug = ? AND migration_id IS NULL"
            " AND substr(dissolved_at, 1, 10) = substr(?, 1, 10)",
            (plan_slug, dissolved_at),
        ).fetchone()
        if existing is not None:
            return int(existing[0])

    cur = conn.execute(
        "INSERT INTO dissolutions ("
        "  repo, plan_pk, plan_slug, migration_id, commit_sha,"
        "  target_schemas, target_tables, rationale,"
        "  dissolved_at, declared_by"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            repo, plan_pk, plan_slug, migration_id, commit_sha,
            json.dumps(target_schemas), json.dumps(target_tables), rationale,
            dissolved_at, declared_by,
        ),
    )
    conn.commit()
    new_id = int(cur.lastrowid or 0)
    log.info("dissolution declared", extra={
        "id": new_id, "plan_slug": plan_slug, "migration_id": migration_id,
    })
    return new_id


# ---------------------------------------------------------------------------
# link_file_revisions — bulk insert link rows
# ---------------------------------------------------------------------------


def link_file_revisions(
    conn: sqlite3.Connection,
    dissolution_pk: int,
    file_revision_pks: list[int],
) -> int:
    """Bulk insert link rows; idempotent on (file_revision_pk, dissolution_pk).

    Returns the number of rows actually inserted (existing pairs skipped).
    """
    if not file_revision_pks:
        return 0
    inserted = 0
    for rev_pk in file_revision_pks:
        cur = conn.execute(
            "INSERT OR IGNORE INTO file_revision_dissolutions"
            " (file_revision_pk, dissolution_pk) VALUES (?, ?)",
            (rev_pk, dissolution_pk),
        )
        inserted += cur.rowcount
    conn.commit()
    return inserted


# ---------------------------------------------------------------------------
# record_materialization — Phase 8 hook
# ---------------------------------------------------------------------------


def record_materialization(
    conn: sqlite3.Connection,
    *,
    file_path: str,
    source_table: str,
    materializer: str,
    materialization_kind: str = "stub",
    materialized_at: str | None = None,
    source_dissolution_pk: int | None = None,
    source_row_id: int | None = None,
    repo: str = "vault",
) -> int:
    """Log a materialization event.

    Returns the new materialization_events.id. Not idempotent —
    repeated materialization of the same path *is* a fresh event
    (the stub was rewritten).
    """
    if materialized_at is None:
        materialized_at = _now_iso()

    cur = conn.execute(
        "INSERT INTO materialization_events ("
        "  repo, file_path, source_dissolution_pk, source_table,"
        "  source_row_id, materializer, materialized_at, materialization_kind"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            repo, file_path, source_dissolution_pk, source_table,
            source_row_id, materializer, materialized_at, materialization_kind,
        ),
    )
    conn.commit()
    return int(cur.lastrowid or 0)


# ---------------------------------------------------------------------------
# Listing / introspection
# ---------------------------------------------------------------------------


_DISSOLUTION_COLUMNS = (
    "id, schema_type, repo, plan_pk, plan_slug, migration_id, commit_sha,"
    " target_schemas, target_tables, rationale, dissolved_at, declared_at,"
    " declared_by"
)


def _row_to_dissolution(row: tuple[Any, ...]) -> dict[str, Any]:
    keys = [
        "id", "schema_type", "repo", "plan_pk", "plan_slug",
        "migration_id", "commit_sha", "target_schemas", "target_tables",
        "rationale", "dissolved_at", "declared_at", "declared_by",
    ]
    d = dict(zip(keys, row, strict=False))
    # Parse JSON arrays for friendlier downstream consumption.
    for k in ("target_schemas", "target_tables"):
        if d.get(k):
            try:
                d[k] = json.loads(d[k])
            except (TypeError, json.JSONDecodeError):
                pass
    return d


def get(
    conn: sqlite3.Connection,
    dissolution_pk: int,
) -> dict[str, Any] | None:
    """Return one dissolution row by primary key, or None."""
    row = conn.execute(
        f"SELECT {_DISSOLUTION_COLUMNS} FROM dissolutions WHERE id = ?",
        (dissolution_pk,),
    ).fetchone()
    return _row_to_dissolution(row) if row is not None else None


def list_for_plan(
    conn: sqlite3.Connection,
    plan_slug: str,
    *,
    repo: str = "vault",
) -> list[dict[str, Any]]:
    """List all dissolution waves for a plan slug, newest-first."""
    rows = conn.execute(
        f"SELECT {_DISSOLUTION_COLUMNS} FROM dissolutions"
        " WHERE plan_slug = ? AND repo = ?"
        " ORDER BY dissolved_at DESC",
        (plan_slug, repo),
    ).fetchall()
    return [_row_to_dissolution(r) for r in rows]


def list_for_migration(
    conn: sqlite3.Connection,
    migration_id: str,
    *,
    repo: str = "vault",
) -> list[dict[str, Any]]:
    """List all dissolution waves tied to a migration_id."""
    rows = conn.execute(
        f"SELECT {_DISSOLUTION_COLUMNS} FROM dissolutions"
        " WHERE migration_id = ? AND repo = ?"
        " ORDER BY dissolved_at DESC",
        (migration_id, repo),
    ).fetchall()
    return [_row_to_dissolution(r) for r in rows]


def list_waves(
    conn: sqlite3.Connection,
    *,
    repo: str = "vault",
) -> list[dict[str, Any]]:
    """List all dissolution waves with file counts (Q8 verbose introspection)."""
    rows = conn.execute(
        f"SELECT {_DISSOLUTION_COLUMNS},"
        " (SELECT COUNT(*) FROM file_revision_dissolutions"
        "  WHERE dissolution_pk = dissolutions.id) AS linked_files"
        " FROM dissolutions WHERE repo = ?"
        " ORDER BY dissolved_at DESC",
        (repo,),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for r in rows:
        d = _row_to_dissolution(r[:-1])
        d["linked_files"] = int(r[-1])
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# lookup_vault_path — full lifecycle for a path
# ---------------------------------------------------------------------------


def lookup_vault_path(
    conn: sqlite3.Connection,
    file_path: str,
    *,
    repo: str = "vault",
) -> dict[str, Any]:
    """Return full lifecycle (dissolution + materialization events) for a path.

    Output:
        {
          "file_path": str,
          "repo": str,
          "dissolutions": [<dissolution dict>, ...],
          "materializations": [<materialization event dict>, ...],
          "lifecycle": [<event>, ...],     # chronological union
        }
    """
    qualified_columns = ", ".join(f"d.{c}" for c in _DISSOLUTION_COLUMNS.split(", "))
    dissolutions = [
        _row_to_dissolution(r) for r in conn.execute(
            f"SELECT {qualified_columns} FROM dissolutions d"
            " JOIN file_revision_dissolutions frd ON frd.dissolution_pk = d.id"
            " JOIN file_revisions fr ON fr.id = frd.file_revision_pk"
            " WHERE fr.repo = ? AND fr.file_path = ?"
            " ORDER BY d.dissolved_at",
            (repo, file_path),
        ).fetchall()
    ]

    mat_rows = conn.execute(
        "SELECT id, repo, file_path, source_dissolution_pk, source_table,"
        " source_row_id, materializer, materialized_at, materialization_kind"
        " FROM materialization_events"
        " WHERE repo = ? AND file_path = ?"
        " ORDER BY materialized_at",
        (repo, file_path),
    ).fetchall()
    materializations: list[dict[str, Any]] = [
        {
            "id": r[0], "repo": r[1], "file_path": r[2],
            "source_dissolution_pk": r[3], "source_table": r[4],
            "source_row_id": r[5], "materializer": r[6],
            "materialized_at": r[7], "materialization_kind": r[8],
        }
        for r in mat_rows
    ]

    # Build chronological lifecycle view
    lifecycle: list[dict[str, Any]] = []
    for d in dissolutions:
        lifecycle.append({
            "event_type": "dissolution",
            "event_at": d["dissolved_at"],
            "plan_slug": d["plan_slug"],
            "target_tables": d["target_tables"],
            "dissolution_pk": d["id"],
        })
    for m in materializations:
        lifecycle.append({
            "event_type": "materialization",
            "event_at": m["materialized_at"],
            "materializer": m["materializer"],
            "source_table": m["source_table"],
            "materialization_kind": m["materialization_kind"],
            "materialization_pk": m["id"],
        })
    lifecycle.sort(key=lambda e: e["event_at"])

    return {
        "file_path": file_path,
        "repo": repo,
        "dissolutions": dissolutions,
        "materializations": materializations,
        "lifecycle": lifecycle,
    }


# ---------------------------------------------------------------------------
# Audit / validate — Q8 verbose
# ---------------------------------------------------------------------------


def audit_invariants(
    conn: sqlite3.Connection,
    *,
    repo: str = "vault",
) -> dict[str, Any]:
    """Audit FK integrity, Q11 rule, orphan rows, migration_id existence.

    Returns a dict with:
      - ``checks_passed``: int
      - ``findings``: list[dict] — each {check, severity, detail}
    """
    findings: list[dict[str, Any]] = []
    checks = 0

    # 1. Orphan link rows — file_revision_dissolutions pointing at deleted rows
    orphan_link_rev = conn.execute(
        "SELECT COUNT(*) FROM file_revision_dissolutions frd"
        " WHERE NOT EXISTS (SELECT 1 FROM file_revisions fr WHERE fr.id = frd.file_revision_pk)"
    ).fetchone()[0]
    checks += 1
    if orphan_link_rev:
        findings.append({
            "check": "orphan_link_to_file_revisions",
            "severity": "error",
            "detail": f"{orphan_link_rev} link row(s) point at missing file_revisions",
        })

    orphan_link_dis = conn.execute(
        "SELECT COUNT(*) FROM file_revision_dissolutions frd"
        " WHERE NOT EXISTS (SELECT 1 FROM dissolutions d WHERE d.id = frd.dissolution_pk)"
    ).fetchone()[0]
    checks += 1
    if orphan_link_dis:
        findings.append({
            "check": "orphan_link_to_dissolutions",
            "severity": "error",
            "detail": f"{orphan_link_dis} link row(s) point at missing dissolutions",
        })

    # 2. Q11 rule — when migration_id IS NULL, rationale must be present AND
    #    target_tables must be a non-empty JSON array.
    q11_violations = conn.execute(
        "SELECT id, plan_slug, target_tables FROM dissolutions"
        " WHERE migration_id IS NULL"
        " AND (rationale IS NULL OR rationale = '' OR target_tables = '[]')",
    ).fetchall()
    checks += 1
    for row in q11_violations:
        findings.append({
            "check": "q11_validation",
            "severity": "error",
            "detail": (
                f"dissolution id={row[0]} plan_slug={row[1]!r}"
                f" has NULL migration_id but missing rationale or empty target_tables"
            ),
        })

    # 3. migration_id present but absent from schema_migrations
    rows = conn.execute(
        "SELECT id, migration_id FROM dissolutions"
        " WHERE migration_id IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM schema_migrations sm"
        "                  WHERE sm.migration_id = dissolutions.migration_id)"
    ).fetchall()
    checks += 1
    for row in rows:
        findings.append({
            "check": "migration_id_absent",
            "severity": "warning",
            "detail": (
                f"dissolution id={row[0]} references migration_id={row[1]!r}"
                f" which is not in schema_migrations"
            ),
        })

    # 4. plan_pk NULL but plan_slug looks resolvable — surfaces drift only
    rows = conn.execute(
        "SELECT id, plan_slug FROM dissolutions"
        " WHERE plan_pk IS NULL"
    ).fetchall()
    checks += 1
    for row in rows:
        resolved = _resolve_plan_pk(conn, row[1])
        if resolved is not None:
            findings.append({
                "check": "plan_pk_unresolved",
                "severity": "info",
                "detail": (
                    f"dissolution id={row[0]} has plan_pk=NULL but"
                    f" plan_slug={row[1]!r} now resolves to plans.id={resolved}"
                ),
            })

    # 5. materialization_events with source_dissolution_pk pointing at missing row
    orphan_mat = conn.execute(
        "SELECT COUNT(*) FROM materialization_events me"
        " WHERE me.source_dissolution_pk IS NOT NULL"
        " AND NOT EXISTS (SELECT 1 FROM dissolutions d WHERE d.id = me.source_dissolution_pk)"
    ).fetchone()[0]
    checks += 1
    if orphan_mat:
        findings.append({
            "check": "orphan_materialization_source",
            "severity": "error",
            "detail": f"{orphan_mat} materialization event(s) point at missing dissolutions",
        })

    return {
        "repo": repo,
        "checks_passed": checks - sum(1 for f in findings if f["severity"] == "error"),
        "checks_run": checks,
        "findings": findings,
    }


def validate_all(
    conn: sqlite3.Connection,
    *,
    repo: str = "vault",
) -> dict[str, Any]:
    """Run audit_invariants and emit a pass/fail summary."""
    audit = audit_invariants(conn, repo=repo)
    errors = [f for f in audit["findings"] if f["severity"] == "error"]
    return {
        "pass": len(errors) == 0,
        "error_count": len(errors),
        "warning_count": sum(1 for f in audit["findings"] if f["severity"] == "warning"),
        "info_count": sum(1 for f in audit["findings"] if f["severity"] == "info"),
        "checks_run": audit["checks_run"],
        "findings": audit["findings"],
    }


# ---------------------------------------------------------------------------
# Status — overview for `phdb dissolution status`
# ---------------------------------------------------------------------------


def status_overview(
    conn: sqlite3.Connection,
    *,
    repo: str = "vault",
) -> dict[str, Any]:
    """Return a high-level registry health overview."""
    total = conn.execute(
        "SELECT COUNT(*) FROM dissolutions WHERE repo = ?",
        (repo,),
    ).fetchone()[0]
    total_links = conn.execute(
        "SELECT COUNT(*) FROM file_revision_dissolutions"
    ).fetchone()[0]
    total_mat = conn.execute(
        "SELECT COUNT(*) FROM materialization_events WHERE repo = ?",
        (repo,),
    ).fetchone()[0]
    null_migration = conn.execute(
        "SELECT COUNT(*) FROM dissolutions"
        " WHERE repo = ? AND migration_id IS NULL",
        (repo,),
    ).fetchone()[0]
    audit = validate_all(conn, repo=repo)
    return {
        "repo": repo,
        "total_dissolutions": total,
        "total_linked_revisions": total_links,
        "total_materialization_events": total_mat,
        "dissolutions_without_migration": null_migration,
        "audit_pass": audit["pass"],
        "audit_errors": audit["error_count"],
        "audit_warnings": audit["warning_count"],
    }


# ---------------------------------------------------------------------------
# Reclassify — Phase 4 link-rows backfill for one wave
# ---------------------------------------------------------------------------


def reclassify_wave(
    conn: sqlite3.Connection,
    dissolution_pk: int,
    *,
    file_path_patterns: list[str],
    commit_shas: list[str] | None = None,
    repo: str = "vault",
) -> dict[str, Any]:
    """Find matching file_revisions rows and insert link rows.

    For each pattern (SQL LIKE syntax), find ``file_revisions`` rows where
    either:
      - ``change_type='delete'`` and ``file_path`` matches the pattern, OR
      - ``change_type='rename'`` and ``prior_file_path`` matches the pattern
        (file renamed AWAY from the original dissolution-eligible location,
        e.g., handoffs archived to ``Archives/Handoffs/`` via
        ``archive_handoffs.py`` rather than deleted).

    Optional ``commit_shas`` narrows the match to specific commit(s).

    Returns dict with matched / inserted counts. Idempotent.
    """
    matched_pks: set[int] = set()
    for pattern in file_path_patterns:
        if commit_shas:
            placeholders = ",".join("?" * len(commit_shas))
            sql = (
                "SELECT id FROM file_revisions"
                " WHERE repo = ?"
                " AND ("
                "  (change_type = 'delete' AND file_path LIKE ?)"
                "  OR (change_type = 'rename' AND prior_file_path LIKE ?)"
                " )"
                f" AND commit_sha IN ({placeholders})"
            )
            params = (repo, pattern, pattern, *commit_shas)
        else:
            sql = (
                "SELECT id FROM file_revisions"
                " WHERE repo = ?"
                " AND ("
                "  (change_type = 'delete' AND file_path LIKE ?)"
                "  OR (change_type = 'rename' AND prior_file_path LIKE ?)"
                " )"
            )
            params = (repo, pattern, pattern)
        for row in conn.execute(sql, params).fetchall():
            matched_pks.add(int(row[0]))

    inserted = link_file_revisions(conn, dissolution_pk, list(matched_pks))
    return {
        "dissolution_pk": dissolution_pk,
        "matched": len(matched_pks),
        "inserted": inserted,
    }


__all__ = [
    "audit_invariants",
    "declare",
    "get",
    "link_file_revisions",
    "list_for_migration",
    "list_for_plan",
    "list_waves",
    "lookup_vault_path",
    "reclassify_wave",
    "record_materialization",
    "status_overview",
    "validate_all",
]
