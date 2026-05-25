"""Dissolution — one row of the dissolutions table (migration 0041).

Per the Dissolution Tracking plan (Outputs/Plans/Dissolution Tracking.md):
one row per dissolution wave with driving plan, migration (optional per Q3),
commit_sha (optional), target Schema.org @types, target phdb tables,
rationale (required when migration_id is NULL), and ISO 8601 timestamps.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Dissolution:
    """One per-wave dissolution event row."""

    plan_slug: str
    target_schemas: list[str]                 # Schema.org @types — durable contract
    target_tables: list[str]                  # phdb table names — operational
    dissolved_at: str                         # ISO 8601
    declared_by: str                          # 'cowork' / 'code' / 'backfill'
    repo: str = "vault"
    plan_pk: int | None = None
    migration_id: str | None = None
    commit_sha: str | None = None
    rationale: str | None = None
