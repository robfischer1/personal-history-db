"""MaterializationLogger — Phase 8 helper for materializer tools.

Wraps the ``record_materialization`` API in an ergonomic interface that
vault-side materializer tools (articles_materialize.py, tasks_materialize.py,
gen_todo_md.py) compose into their write paths. Best-effort — DB
unavailable doesn't fail the materialization.

Usage:

    from phdb.dissolutions.materializer_log import MaterializationLogger

    logger = MaterializationLogger(materializer="articles_materialize")
    logger.log_stub(file_path="References/foo.md",
                    source_table="articles", source_row_id=42)
    logger.close()

Or as a context manager:

    with MaterializationLogger(materializer="tasks_materialize") as ml:
        for task in tasks:
            ml.log_stub(file_path=..., source_table="tasks", source_row_id=task.id)
"""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _resolve_db_path(override: str | None = None) -> str | None:
    """Resolve the live phdb DB path. Tries override, env var, default location."""
    if override:
        return override
    env = os.environ.get("PHDB_DB_PATH")
    if env and Path(env).exists():
        return env
    default = Path.home() / "Forge" / "personal-history-data" / "personal-history.db"
    if default.exists():
        return str(default)
    return None


class MaterializationLogger:
    """Thin wrapper around phdb.dissolutions.record_materialization.

    Best-effort: when the DB is not reachable, log_stub becomes a no-op
    (with a single WARNING) — materializers must not fail because the
    registry is unavailable.
    """

    def __init__(
        self,
        *,
        materializer: str,
        db_path: str | None = None,
        repo: str = "vault",
        source_dissolution_pk: int | None = None,
    ) -> None:
        self.materializer = materializer
        self.repo = repo
        self.source_dissolution_pk = source_dissolution_pk
        self.db_path = _resolve_db_path(db_path)
        self._conn: sqlite3.Connection | None = None
        self._disabled = False
        if self.db_path is None:
            log.warning(
                "MaterializationLogger: no DB available; events will not be logged"
            )
            self._disabled = True

    def _ensure_conn(self) -> sqlite3.Connection | None:
        if self._disabled:
            return None
        if self._conn is None and self.db_path is not None:
            try:
                self._conn = sqlite3.connect(
                    f"file:{self.db_path}?mode=rw", uri=True
                )
            except sqlite3.OperationalError as e:
                log.warning(
                    "MaterializationLogger: cannot open DB %s: %s",
                    self.db_path, e,
                )
                self._disabled = True
                return None
        return self._conn

    def log(
        self,
        *,
        file_path: str,
        source_table: str,
        source_row_id: int | None = None,
        materialization_kind: str = "stub",
        source_dissolution_pk: int | None = None,
    ) -> int | None:
        """Record one materialization event. Returns event id or None."""
        conn = self._ensure_conn()
        if conn is None:
            return None
        # Defer the import so a missing phdb install doesn't crash the materializer.
        try:
            from phdb.dissolutions import record_materialization
        except ImportError as e:
            log.warning("MaterializationLogger: phdb.dissolutions unimportable: %s", e)
            self._disabled = True
            return None
        return record_materialization(
            conn,
            file_path=file_path,
            source_table=source_table,
            source_row_id=source_row_id,
            materializer=self.materializer,
            materialization_kind=materialization_kind,
            source_dissolution_pk=source_dissolution_pk or self.source_dissolution_pk,
            repo=self.repo,
        )

    def log_stub(
        self,
        *,
        file_path: str,
        source_table: str,
        source_row_id: int | None = None,
    ) -> int | None:
        """Shorthand for log() with materialization_kind='stub'."""
        return self.log(
            file_path=file_path,
            source_table=source_table,
            source_row_id=source_row_id,
            materialization_kind="stub",
        )

    def log_aggregate(
        self,
        *,
        file_path: str,
        source_table: str,
    ) -> int | None:
        """Shorthand for aggregate (TODO.md regen) materializations."""
        return self.log(
            file_path=file_path,
            source_table=source_table,
            materialization_kind="aggregate",
        )

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "MaterializationLogger":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
