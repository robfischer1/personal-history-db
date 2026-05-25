"""MaterializationEvent — one row of the materialization_events table (migration 0041).

Per Dissolution Tracking Phase 8 (Q13 override): every materializer
(articles_materialize.py, tasks_materialize.py, gen_todo_md.py, future)
logs a row when it surfaces DB-canonical content back into the vault as
a stub. Bidirectional vault-DB lifecycle visibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

MaterializationKind = Literal["stub", "aggregate", "full"]


@dataclass(frozen=True)
class MaterializationEvent:
    """One per-file materialization event row."""

    file_path: str                                # vault-relative POSIX
    source_table: str                             # phdb table the content was materialized from
    materializer: str                             # 'articles_materialize' / 'tasks_materialize' / etc.
    materialized_at: str                          # ISO 8601
    repo: str = "vault"
    source_dissolution_pk: int | None = None
    source_row_id: int | None = None              # nullable for aggregate materializations (TODO.md)
    materialization_kind: MaterializationKind = "stub"
