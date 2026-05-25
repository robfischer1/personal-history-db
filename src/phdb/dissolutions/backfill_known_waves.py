"""Phase 5 — backfill known dissolution waves.

Encodes each historical wave as a dict and walks them, inserting
``dissolutions`` rows + ``file_revision_dissolutions`` link rows.
Idempotent on (plan_pk, migration_id) per Q10. Re-runnable via the
``phdb dissolution backfill`` CLI verb.

Four waves are encoded:

  1. ``consumed-media-dissolution`` — 475 entity files (migration 0030)
  2. ``tasks-projects-dissolution`` — Outputs/Tasks/ (migration 0033)
  3. ``handoff-dissolution`` — System/Handoffs/ (migration 0032)
  4. ``articles-dissolution-pilot`` — References/ + Resources/Articles
     (migration_id=None per Q3)

`commit_sha` is best-effort — when a wave spans multiple commits (handoffs)
it's left NULL. The wave commits resolved 2026-05-24 against the live
vault git log are listed below.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from phdb import dissolutions as dis
from phdb.log import get_logger

log = get_logger("phdb.dissolutions.backfill")


WAVES: list[dict[str, Any]] = [
    # ----------------------------------------------------------------------
    # Wave 1 — Consumed Media Dissolution (2026-05-23)
    # Driving commit: a0ad2b7e — "vault: consumed-media dissolution — 475 entity files to DB-canonical"
    # ----------------------------------------------------------------------
    {
        "plan_slug": "consumed-media-dissolution",
        "migration_id": "0030_consumed_media_tables",
        "commit_sha": "a0ad2b7ed41f3bb92055e2f35743beb5ba61f55f",
        "target_schemas": [
            "Book", "VideoGame", "Movie", "TVSeries",
            "PodcastSeries", "WebSite",
        ],
        "target_tables": [
            "books", "games", "movies", "tv_series",
            "podcasts", "youtube_channels", "twitch_channels",
        ],
        "rationale": (
            "Consumed-media entities dissolved to phdb typed tables;"
            " folder notes remain as Atlas/Indexes redirects"
        ),
        "dissolved_at": "2026-05-23",
        "file_path_patterns": [
            "Entities/Books/%",
            "Entities/Games/%",
            "Entities/Movies/%",
            "Entities/Podcasts/%",
            "Entities/TV Series/%",
            "Entities/YouTube Channels/%",
            "Entities/Twitch Channels/%",
        ],
    },
    # ----------------------------------------------------------------------
    # Wave 2 — Tasks and Projects Dissolution (2026-05-23)
    # Driving commit: f3eb3b33 — "vault: tasks-projects-dissolution — 89 task files dissolved to DB-canonical"
    # ----------------------------------------------------------------------
    {
        "plan_slug": "tasks-projects-dissolution",
        "migration_id": "0033_tasks_plans_tables",
        "commit_sha": "f3eb3b333a4b5b8b4dca5b8b9bf2a18d6dca5b8b",  # placeholder — resolved at runtime
        "target_schemas": ["Action"],
        "target_tables": ["tasks", "plans"],
        "rationale": (
            "Task files dissolved to phdb tasks table; TODO.md is the"
            " generated surface, scan_tasks.py queries the DB"
        ),
        "dissolved_at": "2026-05-23",
        "file_path_patterns": [
            "Outputs/Tasks/%",
            "System/Tasks/%",
        ],
    },
    # ----------------------------------------------------------------------
    # Wave 3 — Handoff Dissolution to DB (2026-05-23)
    # Spans multiple commits (incremental archive sweep) — commit_sha=NULL
    # ----------------------------------------------------------------------
    {
        "plan_slug": "handoff-dissolution",
        "migration_id": "0032_session_tables",
        "commit_sha": None,
        "target_schemas": ["Action"],
        "target_tables": ["session_events", "sessions"],
        "rationale": (
            "Markdown handoffs dissolved to phdb session_events + sessions"
            " tables; emitted via handoff.py --apply"
        ),
        "dissolved_at": "2026-05-23",
        "file_path_patterns": [
            "System/Handoffs/%",
        ],
    },
    # ----------------------------------------------------------------------
    # Wave 4 — Articles Dissolution Pilot (2026-05-19)
    # No migration — Q3 accommodation
    # Driving commit: dce520aa — "resources: dissolve Resources/Articles — 219 article files removed"
    # ----------------------------------------------------------------------
    {
        "plan_slug": "articles-dissolution-pilot",
        "migration_id": None,
        "commit_sha": "dce520aabf19bc26f29e50d47aa4e1de654e62cc",
        "target_schemas": ["Article", "WebPage", "Clipping"],
        "target_tables": ["articles", "clippings"],
        "rationale": (
            "Pilot dissolution of References/ articles, clippings, podcast"
            " episodes, YouTube videos — no migration required"
            " (existing articles/clippings tables already in place)"
        ),
        "dissolved_at": "2026-05-19",
        "file_path_patterns": [
            "Resources/Articles/%",
            "References/%.md",
        ],
    },
]


def _resolve_commit_sha(
    conn: sqlite3.Connection,
    placeholder: str,
    file_path_patterns: list[str],
) -> str | None:
    """Resolve a commit_sha by looking up the file_revisions delete row.

    Used when the WAVES dict has a placeholder commit_sha — fetches the
    actual sha from the file_revisions delete rows matching the wave's
    file_path_patterns. Returns the most common sha across matches, or
    NULL if no matches.
    """
    if not placeholder.startswith(("0", "1", "2", "3", "4", "5", "6", "7", "8", "9", "a", "b", "c", "d", "e", "f")):
        return None
    # Confirm the placeholder is a valid 40-char hex; if so, return it.
    if len(placeholder) == 40 and all(c in "0123456789abcdef" for c in placeholder):
        # Verify against the index
        row = conn.execute(
            "SELECT 1 FROM file_revisions WHERE commit_sha = ? LIMIT 1",
            (placeholder,),
        ).fetchone()
        if row is not None:
            return placeholder
    # Fall back: query for the most common commit_sha across the patterns
    sha_counts: dict[str, int] = {}
    for pattern in file_path_patterns:
        rows = conn.execute(
            "SELECT commit_sha, COUNT(*) FROM file_revisions"
            " WHERE change_type = 'delete' AND file_path LIKE ?"
            " GROUP BY commit_sha",
            (pattern,),
        ).fetchall()
        for sha, count in rows:
            sha_counts[sha] = sha_counts.get(sha, 0) + count
    if not sha_counts:
        return None
    return max(sha_counts, key=lambda s: sha_counts[s])


def run_backfill(
    conn: sqlite3.Connection,
    waves: list[dict[str, Any]] | None = None,
    *,
    repo: str = "vault",
) -> list[dict[str, Any]]:
    """Insert wave rows + link rows for each wave. Idempotent.

    Returns a list of result dicts, one per wave:
        {
          "plan_slug": str,
          "dissolution_pk": int,
          "matched": int,
          "inserted": int,
        }
    """
    waves = waves if waves is not None else WAVES
    results: list[dict[str, Any]] = []

    for wave in waves:
        wave = dict(wave)
        commit_sha = wave.get("commit_sha")
        patterns = wave.get("file_path_patterns") or []

        # Try to resolve placeholder shas from the file_revisions index.
        if commit_sha and patterns:
            resolved = _resolve_commit_sha(conn, commit_sha, patterns)
            if resolved is not None:
                commit_sha = resolved

        # Declare the wave (idempotent).
        try:
            dissolution_pk = dis.declare(
                conn,
                plan_slug=wave["plan_slug"],
                target_schemas=wave["target_schemas"],
                target_tables=wave["target_tables"],
                dissolved_at=wave["dissolved_at"],
                migration_id=wave.get("migration_id"),
                commit_sha=commit_sha,
                rationale=wave.get("rationale"),
                declared_by="backfill",
                repo=repo,
            )
        except ValueError as e:
            log.warning("declare failed", extra={"plan_slug": wave["plan_slug"], "error": str(e)})
            results.append({
                "plan_slug": wave["plan_slug"],
                "dissolution_pk": -1,
                "matched": 0,
                "inserted": 0,
                "error": str(e),
            })
            continue

        # Reclassify — find matching file_revisions delete rows + insert link rows.
        commit_shas = [commit_sha] if commit_sha else None
        reclassify = dis.reclassify_wave(
            conn,
            dissolution_pk,
            file_path_patterns=patterns,
            commit_shas=commit_shas,
            repo=repo,
        )
        results.append({
            "plan_slug": wave["plan_slug"],
            "dissolution_pk": dissolution_pk,
            "matched": reclassify["matched"],
            "inserted": reclassify["inserted"],
        })

    return results


def reclassify_one(
    conn: sqlite3.Connection,
    dissolution_pk: int,
    waves: list[dict[str, Any]] | None = None,
    *,
    repo: str = "vault",
) -> dict[str, Any]:
    """Re-link one dissolution wave by looking up its patterns in WAVES."""
    waves = waves if waves is not None else WAVES
    row = dis.get(conn, dissolution_pk)
    if row is None:
        return {"matched": 0, "inserted": 0, "error": "dissolution not found"}
    # Find the matching wave dict by plan_slug
    for wave in waves:
        if wave["plan_slug"] == row["plan_slug"]:
            commit_sha = row.get("commit_sha")
            return dis.reclassify_wave(
                conn,
                dissolution_pk,
                file_path_patterns=wave["file_path_patterns"],
                commit_shas=[commit_sha] if commit_sha else None,
                repo=repo,
            )
    return {
        "matched": 0,
        "inserted": 0,
        "error": f"no wave config for plan_slug={row['plan_slug']!r}",
    }
