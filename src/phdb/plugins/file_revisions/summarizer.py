"""file_revisions summarizer — Phase 4 of the Git for Ideas plan.

Orchestration helpers for filling the ``summary`` / ``summary_model`` /
``summary_generated_at`` columns on ``file_revisions`` rows. The actual
LLM work is dispatched to Claude Code subagents (Haiku / Sonnet) by an
orchestrator session, *not* a direct Anthropic-API call — Rob's
preference is to run locally via the Claude Code Agent tool and avoid
managing an API key inside phdb.

Public surface:

  - ``prepare_batch(conn, *, repo, limit) -> list[Item]``
        Select the next ``limit`` unsummarized rows; materialize each
        row's old + new body; build the diff-aware prompt; classify
        which model tier the row should be sent to. Returns one
        ``Item`` per row, ready for the orchestrator to dispatch.

  - ``record_summary(conn, *, rev_id, summary, model)``
        Persist a returned summary onto a file_revisions row. Stamps
        ``summary_generated_at`` server-side.

  - ``pick_model(change_type, combined_bytes) -> str``
        Public copy of the routing rule (Phase 0 Q5).

Model routing (Phase 0 Q5):
  - Haiku for routine ``add`` / ``modify`` where combined inserted +
    deleted bytes are ≤ 5 KB.
  - Sonnet for everything else (larger diffs, ``rename``, ``delete``).

The orchestrator (any Claude Code session, typically) is responsible
for actually calling subagents in parallel via the ``Agent`` tool with
``model: "haiku"`` or ``"sonnet"`` and persisting the returned text via
``record_summary``. This module owns the deterministic work — prompt
shape, body materialization, routing, persistence — and leaves the LLM
call to the orchestrator.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from phdb.file_revisions import (  # noqa: PLC2701 — same subsystem
    _git_cat_file,
    _resolve_repo_root,
)
from phdb.log import get_logger

log = get_logger("phdb.file_revisions.summarizer")


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HAIKU_TIER = "haiku"
SONNET_TIER = "sonnet"

# Combined (inserted + deleted) byte threshold above which we escalate
# from Haiku to Sonnet. 5 KB matches Phase 0 decision Q5.
ROUTING_BYTE_THRESHOLD = 5 * 1024

# Per-body truncation cap — keeps prompt size bounded on the long-tail
# of multi-thousand-line vault notes. The Phase 4 spec is a summary of
# producer intent, not line-by-line narration, so a window over the
# head of each body is sufficient.
MAX_BODY_BYTES = 32 * 1024

DEFAULT_BATCH_SIZE = 100

# Sentinels for orchestrator-side "we intentionally didn't call an LLM"
# decisions — written into summary_model so the row exits the
# unsummarized queue.
SKIP_MODEL_UNREADABLE = "skip:blob-unreadable"
SKIP_MODEL_OUT_OF_SCOPE = "skip:out-of-scope"
SKIP_MODEL_DELETE = "skip:delete-event"


# ---------------------------------------------------------------------------
# Public dataclass — one queue entry
# ---------------------------------------------------------------------------


@dataclass
class Item:
    """One row prepared for orchestrator dispatch."""

    rev_id: int
    repo: str
    commit_sha: str
    file_path: str
    change_type: str
    model_tier: str       # 'haiku' | 'sonnet'
    combined_bytes: int   # informational — bytes of old + new body
    prompt: str           # full user-turn prompt
    system_prompt: str    # shared system prompt
    # Bodies are surfaced for callers that prefer to assemble their own
    # prompts (e.g. richer markdown rendering); the prompt field is the
    # ready-to-go version.
    old_body: str = field(repr=False, default="")
    new_body: str = field(repr=False, default="")
    skip: bool = False    # set when both bodies are empty/unreadable


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


def pick_model(change_type: str, combined_bytes: int) -> str:
    """Phase 0 Q5 — Haiku for routine, Sonnet for larger / rename / delete."""
    if change_type in {"rename", "delete"}:
        return SONNET_TIER
    if combined_bytes > ROUTING_BYTE_THRESHOLD:
        return SONNET_TIER
    return HAIKU_TIER


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------


SYSTEM_PROMPT = (
    "You write 2-4 sentence change summaries for a markdown vault's git "
    "history. Each call shows you the prior body and the current body of "
    "one note. Describe what changed and why — architectural intent, "
    "semantic shift, structural reshape, governance move. Focus on "
    "producer intent. Do not narrate the diff line by line. Do not "
    "include preamble; start the summary directly. Output ONLY the "
    "summary text — no headers, no quotes, no explanation."
)


def _truncate(body: str) -> tuple[str, bool]:
    """Trim body to MAX_BODY_BYTES; return (text, truncated_flag)."""
    encoded = body.encode("utf-8")
    if len(encoded) <= MAX_BODY_BYTES:
        return body, False
    return encoded[:MAX_BODY_BYTES].decode("utf-8", errors="ignore"), True


def _build_prompt(
    *,
    file_path: str,
    change_type: str,
    old_body: str,
    new_body: str,
) -> str:
    """Construct the user-turn prompt for one revision."""
    old_trim, old_trunc = _truncate(old_body)
    new_trim, new_trunc = _truncate(new_body)

    def fence(label: str, body: str, truncated: bool) -> str:
        if not body:
            return f"### {label}\n(empty)"
        trunc_note = f" (truncated to first {MAX_BODY_BYTES} bytes)" if truncated else ""
        return f"### {label}{trunc_note}\n```markdown\n{body}\n```"

    if change_type == "add":
        prior_section = "### Prior body\n(no prior — this is the file's first revision)"
        current_section = fence("Current body", new_trim, new_trunc)
    elif change_type == "delete":
        prior_section = fence("Prior body", old_trim, old_trunc)
        current_section = "### Current body\n(deleted — no current body)"
    else:
        prior_section = fence("Prior body", old_trim, old_trunc)
        current_section = fence("Current body", new_trim, new_trunc)

    return (
        f"File: `{file_path}`\n"
        f"Change type: `{change_type}`\n\n"
        f"{prior_section}\n\n"
        f"{current_section}\n\n"
        "Write the 2-4 sentence summary now. Output only the summary text."
    )


# ---------------------------------------------------------------------------
# Materialization
# ---------------------------------------------------------------------------


def _materialize_pair(
    *,
    repo_root: Path,
    git_blob_sha: str,
    parent_blob_sha: str | None,
    change_type: str,
) -> tuple[str, str]:
    """Return (old_body, new_body) for one revision row.

    For ``add`` rows old_body is empty; for ``delete`` rows new_body is
    empty. For ``modify`` / ``rename`` both bodies are read.
    """
    def _read(sha: str | None) -> str:
        if not sha or set(sha) == {"0"}:
            return ""
        try:
            return _git_cat_file(repo_root, sha)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "[summarizer] git cat-file failed sha=%s: %s",
                sha[:8] if sha else "?", exc,
            )
            return ""

    old_body, new_body = "", ""
    if change_type in {"modify", "rename"}:
        old_body = _read(parent_blob_sha)
        new_body = _read(git_blob_sha)
    elif change_type == "add":
        new_body = _read(git_blob_sha)
    elif change_type == "delete":
        old_body = _read(parent_blob_sha)
    return old_body, new_body


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------


def _select_unsummarized(
    conn: sqlite3.Connection,
    *,
    repo: str,
    limit: int,
) -> list[tuple]:
    return conn.execute(
        "SELECT id, repo, commit_sha, file_path, git_blob_sha, parent_blob_sha,"
        "       change_type"
        " FROM file_revisions"
        " WHERE repo = ? AND summary IS NULL"
        " ORDER BY captured_at ASC"
        " LIMIT ?",
        (repo, limit),
    ).fetchall()


# ---------------------------------------------------------------------------
# Public entry — prepare_batch
# ---------------------------------------------------------------------------


def prepare_batch(
    conn: sqlite3.Connection,
    *,
    repo: str = "vault",
    limit: int = DEFAULT_BATCH_SIZE,
    repo_root: str | None = None,
) -> list[Item]:
    """Materialize prompts for the next ``limit`` unsummarized rows.

    Each Item is ready for the orchestrator to dispatch to a Claude Code
    subagent with ``subagent_type='claude', model=item.model_tier``. The
    subagent returns the summary text; the orchestrator persists it via
    ``record_summary``.

    Items with ``skip=True`` should be persisted with the
    ``SKIP_MODEL_UNREADABLE`` sentinel rather than dispatched — both
    blobs were unreadable / empty, so there's nothing to summarize.
    """
    rows = _select_unsummarized(conn, repo=repo, limit=limit)
    if not rows:
        return []

    root = _resolve_repo_root(conn, repo, override=repo_root)

    items: list[Item] = []
    for r in rows:
        rev_id, r_repo, sha, path, blob_new, blob_old, ctype = r
        old_body, new_body = _materialize_pair(
            repo_root=root,
            git_blob_sha=blob_new,
            parent_blob_sha=blob_old,
            change_type=ctype,
        )
        combined_bytes = len(old_body.encode("utf-8")) + len(new_body.encode("utf-8"))
        skip = combined_bytes == 0
        tier = pick_model(ctype, combined_bytes)
        prompt = "" if skip else _build_prompt(
            file_path=path, change_type=ctype,
            old_body=old_body, new_body=new_body,
        )
        items.append(Item(
            rev_id=rev_id,
            repo=r_repo,
            commit_sha=sha,
            file_path=path,
            change_type=ctype,
            model_tier=tier,
            combined_bytes=combined_bytes,
            prompt=prompt,
            system_prompt=SYSTEM_PROMPT,
            old_body=old_body,
            new_body=new_body,
            skip=skip,
        ))
    return items


# ---------------------------------------------------------------------------
# Public entry — record_summary
# ---------------------------------------------------------------------------


def record_summary(
    conn: sqlite3.Connection,
    *,
    rev_id: int,
    summary: str,
    model: str,
) -> None:
    """Persist a summary onto a file_revisions row.

    ``model`` should be the tier string returned in ``Item.model_tier``
    (``'haiku'`` / ``'sonnet'``) or one of the ``SKIP_MODEL_*``
    sentinels. ``summary_generated_at`` is stamped server-side so all
    rows share the same UTC-millisecond clock as ``captured_at``.
    """
    if not summary or not summary.strip():
        raise ValueError(
            f"refusing to persist empty summary for rev_id={rev_id} "
            "(orchestrator should skip the row or pass SKIP_MODEL_UNREADABLE)"
        )
    conn.execute(
        "UPDATE file_revisions"
        " SET summary = ?,"
        "     summary_model = ?,"
        "     summary_generated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        " WHERE id = ?",
        (summary.strip(), model, rev_id),
    )
    conn.commit()


def record_skip(
    conn: sqlite3.Connection,
    *,
    rev_id: int,
    reason: str = "blob unreadable or empty — no content available to summarize",
) -> None:
    """Persist a sentinel summary for rows we don't dispatch.

    Distinct from ``record_summary`` so callers can tell sentinel writes
    apart from real ones at the call site.
    """
    conn.execute(
        "UPDATE file_revisions"
        " SET summary = ?,"
        "     summary_model = ?,"
        "     summary_generated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
        " WHERE id = ?",
        (reason, SKIP_MODEL_UNREADABLE, rev_id),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Bundle dispatch (Refactor A — 5× throughput per orchestrator turn)
# ---------------------------------------------------------------------------


BUNDLE_SYSTEM_PROMPT = (
    "You write 2-4 sentence change summaries for a markdown vault's git "
    "history. You will see MULTIPLE revisions below, each in its own "
    "section marked with `### REV <id> ###`. Each section shows a file "
    "path, change type, prior body, and current body. For each revision, "
    "describe what changed and why — architectural intent, semantic shift, "
    "structural reshape, governance move. Focus on producer intent. Do not "
    "narrate the diff line by line. Two to four sentences per revision."
    "\n\n"
    "Output ONLY a single JSON object mapping the rev_id (as a string key) "
    "to the summary string. Example: "
    '{"42": "Two-sentence summary here.", "43": "Another summary."} '
    "No prose around the JSON, no code fences, no preamble."
)


@dataclass
class Bundle:
    """A group of revisions packaged for a single subagent dispatch."""

    bundle_id: int
    model_tier: str          # 'haiku' | 'sonnet' — homogeneous within a bundle
    rev_ids: list[int]
    file_path: str           # path to the staged prompt file on disk
    total_bytes: int         # combined materialized body size in the bundle


def _build_bundle_prompt(items: list[Item]) -> str:
    """Build the user-turn prompt that packs N revisions into one call.

    Sections are delimited by `### REV <id> ###` markers so the agent can
    output a JSON object keyed by rev_id. Each section reuses the same
    per-revision shape as `_build_prompt`.
    """
    parts: list[str] = []
    for it in items:
        old_trim, old_trunc = _truncate(it.old_body)
        new_trim, new_trunc = _truncate(it.new_body)

        def fence(label: str, body: str, truncated: bool) -> str:
            if not body:
                return f"  {label}: (empty)"
            trunc_note = f" (truncated to first {MAX_BODY_BYTES} bytes)" if truncated else ""
            return f"  {label}{trunc_note}:\n```markdown\n{body}\n```"

        if it.change_type == "add":
            prior = "  Prior body: (no prior — first revision)"
            current = fence("Current body", new_trim, new_trunc)
        elif it.change_type == "delete":
            prior = fence("Prior body", old_trim, old_trunc)
            current = "  Current body: (deleted)"
        else:
            prior = fence("Prior body", old_trim, old_trunc)
            current = fence("Current body", new_trim, new_trunc)

        parts.append(
            f"### REV {it.rev_id} ###\n"
            f"  File: `{it.file_path}`\n"
            f"  Change type: `{it.change_type}`\n"
            f"{prior}\n"
            f"{current}\n"
        )

    rev_ids = [it.rev_id for it in items]
    example_key = str(rev_ids[0])
    return (
        f"Summarize each of the {len(items)} revisions below. "
        f"Output a single JSON object keyed by rev_id (as string).\n\n"
        + "\n".join(parts)
        + "\n### END ###\n\n"
        f"Output ONLY the JSON object now. Keys must be the string rev_ids: "
        f"{[str(r) for r in rev_ids]}. Example shape: "
        f'{{"{example_key}": "..."}}.'
    )


def prepare_bundles(
    conn: sqlite3.Connection,
    *,
    repo: str = "vault",
    bundle_size: int = 5,
    bundle_count: int = 4,
    bundle_size_sonnet: int | None = None,
    repo_root: str | None = None,
    staging_dir: Path | None = None,
    skip_patterns: list[str] | None = None,
    skip_deletes: bool = False,
) -> list[Bundle]:
    """Stage N bundles of M unsummarized rows for parallel agent dispatch.

    Pulls ``bundle_size * bundle_count`` rows from the unsummarized queue
    (oldest first), filters out any matching ``skip_patterns`` (SQL LIKE
    patterns) or — when ``skip_deletes`` is True — any ``change_type='delete'``
    rows, groups by model tier (haiku/sonnet) for homogeneous-model bundles,
    then packs into ``bundle_count`` bundle files. Each bundle is written
    to ``staging_dir / bundle-<id>.txt`` with a system-prompt header + per-
    revision sections delimited by ``### REV <id> ###`` markers.

    ``bundle_size_sonnet`` overrides ``bundle_size`` for the sonnet tier.
    Large-body revisions (rename/delete or oversize add/modify) route to
    sonnet and quickly blow past sonnet's 200K-token context when packed
    30-per-bundle; setting ``bundle_size_sonnet=5`` keeps sonnet bundles
    in the safe ~150-500KB range while still packing haiku densely.

    Returns a list of ``Bundle`` records that the orchestrator dispatches
    in parallel — one ``Agent(model=bundle.model_tier)`` call per bundle,
    each reading its bundle file and outputting a JSON
    ``{rev_id: summary}`` object that ``record_summaries`` parses.

    Rows materializing to empty bodies are auto-skipped with the
    ``SKIP_MODEL_UNREADABLE`` sentinel (matches ``prepare_batch``); rows
    matching ``skip_patterns`` are NOT auto-skipped here — use
    ``bulk_skip`` for that path.
    """
    if staging_dir is None:
        staging_dir = Path.home() / "Forge" / "Obsidian" / "System" / "Trash" / ".summary-tmp"
    staging_dir.mkdir(parents=True, exist_ok=True)

    target_count = bundle_size * bundle_count
    # Over-fetch in case the LIKE filter trims rows mid-page.
    fetch_count = target_count * 4 if (skip_patterns or skip_deletes) else target_count

    sql = (
        "SELECT id, repo, commit_sha, file_path, git_blob_sha, parent_blob_sha, change_type"
        " FROM file_revisions"
        " WHERE repo = ? AND summary IS NULL"
    )
    params: list[Any] = [repo]
    if skip_patterns:
        for pat in skip_patterns:
            sql += " AND file_path NOT LIKE ?"
            params.append(pat)
    if skip_deletes:
        sql += " AND change_type != 'delete'"
    sql += " ORDER BY captured_at ASC LIMIT ?"
    params.append(fetch_count)
    rows = conn.execute(sql, tuple(params)).fetchall()

    root = _resolve_repo_root(conn, repo, override=repo_root)

    # Materialize prompts up to target_count usable items, splitting auto-
    # skipped (empty) rows along the way via record_skip.
    items: list[Item] = []
    for r in rows:
        if len(items) >= target_count:
            break
        rev_id, r_repo, sha, path, blob_new, blob_old, ctype = r
        old_body, new_body = _materialize_pair(
            repo_root=root,
            git_blob_sha=blob_new,
            parent_blob_sha=blob_old,
            change_type=ctype,
        )
        combined_bytes = len(old_body.encode("utf-8")) + len(new_body.encode("utf-8"))
        if combined_bytes == 0:
            record_skip(conn, rev_id=rev_id)
            continue
        tier = pick_model(ctype, combined_bytes)
        items.append(Item(
            rev_id=rev_id, repo=r_repo, commit_sha=sha, file_path=path,
            change_type=ctype, model_tier=tier, combined_bytes=combined_bytes,
            prompt="", system_prompt=BUNDLE_SYSTEM_PROMPT,
            old_body=old_body, new_body=new_body, skip=False,
        ))

    # Group by tier so each bundle hits one model; pack greedily.
    haiku_items = [i for i in items if i.model_tier == HAIKU_TIER]
    sonnet_items = [i for i in items if i.model_tier == SONNET_TIER]

    bundles: list[Bundle] = []
    next_id = 1

    def pack_tier(tier: str, src: list[Item]) -> None:
        nonlocal next_id
        size = bundle_size_sonnet if (tier == SONNET_TIER and bundle_size_sonnet is not None) else bundle_size
        for chunk_start in range(0, len(src), size):
            chunk = src[chunk_start:chunk_start + size]
            if not chunk:
                continue
            body = BUNDLE_SYSTEM_PROMPT + "\n\n" + _build_bundle_prompt(chunk)
            fp = staging_dir / f"bundle-{next_id}.txt"
            fp.write_text(body, encoding="utf-8")
            bundles.append(Bundle(
                bundle_id=next_id,
                model_tier=tier,
                rev_ids=[i.rev_id for i in chunk],
                file_path=str(fp),
                total_bytes=sum(i.combined_bytes for i in chunk),
            ))
            next_id += 1

    pack_tier(HAIKU_TIER, haiku_items)
    pack_tier(SONNET_TIER, sonnet_items)
    return bundles


def record_summaries(
    conn: sqlite3.Connection,
    *,
    summaries: dict[int, str],
    model: str,
) -> int:
    """Bulk-persist a dict of {rev_id: summary} in one transaction.

    Used by the orchestrator after a bundle subagent returns its JSON
    object. Empty / whitespace-only summaries are skipped (with a log)
    so the row stays in the queue for a retry pass.
    """
    written = 0
    for rev_id, summary in summaries.items():
        if not summary or not summary.strip():
            log.warning(
                "[summarizer] skipping empty bundle-summary for rev_id=%d", rev_id,
            )
            continue
        conn.execute(
            "UPDATE file_revisions"
            " SET summary = ?,"
            "     summary_model = ?,"
            "     summary_generated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            " WHERE id = ?",
            (summary.strip(), model, int(rev_id)),
        )
        written += 1
    conn.commit()
    return written


# ---------------------------------------------------------------------------
# Bulk skip (Refactor B — drain out-of-scope rows without LLM dispatch)
# ---------------------------------------------------------------------------


def bulk_skip(
    conn: sqlite3.Connection,
    *,
    repo: str = "vault",
    patterns: list[str] | None = None,
    skip_deletes: bool = False,
    reason: str = "out of clone-substrate scope (legacy / archive / deletion event)",
    apply: bool = False,
) -> dict[str, int]:
    """Mark unsummarized rows that match the filter as SKIP_MODEL_OUT_OF_SCOPE.

    Drains the queue without going through subagent dispatch. Path
    matching is SQL LIKE (use ``%`` wildcards). ``skip_deletes=True``
    additionally matches all ``change_type='delete'`` rows regardless of
    path. ``apply=False`` returns the count without writing.

    Delete-event rows use a distinct sentinel (``SKIP_MODEL_DELETE``) so
    they're separable from path-filtered skips in later analysis.

    Returns ``{patterns: N, deletes: M, total: N+M}``.
    """
    pattern_count = 0
    delete_count = 0

    if patterns:
        like_clauses = " OR ".join(["file_path LIKE ?"] * len(patterns))
        check_sql = (
            f"SELECT COUNT(*) FROM file_revisions"
            f" WHERE repo = ? AND summary IS NULL AND ({like_clauses})"
        )
        if skip_deletes:
            check_sql += " AND change_type != 'delete'"  # counted separately
        pattern_count = conn.execute(
            check_sql, (repo, *patterns),
        ).fetchone()[0]

    if skip_deletes:
        delete_count = conn.execute(
            "SELECT COUNT(*) FROM file_revisions"
            " WHERE repo = ? AND summary IS NULL AND change_type = 'delete'",
            (repo,),
        ).fetchone()[0]

    result = {
        "patterns": pattern_count,
        "deletes": delete_count,
        "total": pattern_count + delete_count,
    }

    if not apply or result["total"] == 0:
        return result

    if patterns and pattern_count > 0:
        like_clauses = " OR ".join(["file_path LIKE ?"] * len(patterns))
        update_sql = (
            "UPDATE file_revisions"
            " SET summary = ?, summary_model = ?,"
            "     summary_generated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            f" WHERE repo = ? AND summary IS NULL AND ({like_clauses})"
        )
        if skip_deletes:
            update_sql += " AND change_type != 'delete'"
        conn.execute(
            update_sql,
            (f"[skipped] {reason}", SKIP_MODEL_OUT_OF_SCOPE, repo, *patterns),
        )

    if skip_deletes and delete_count > 0:
        conn.execute(
            "UPDATE file_revisions"
            " SET summary = ?, summary_model = ?,"
            "     summary_generated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            " WHERE repo = ? AND summary IS NULL AND change_type = 'delete'",
            (f"[skipped] {reason} (deletion event)", SKIP_MODEL_DELETE, repo),
        )

    conn.commit()
    return result
