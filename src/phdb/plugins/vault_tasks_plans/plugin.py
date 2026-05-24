"""VaultTasksPlansPlugin — Tasks and Projects Dissolution.

Source: Outputs/Tasks/ + System/Tasks/ (tasks) and Outputs/Plans/ +
System/Plans/ (plans). Each file with ``note_type: Task`` or
``note_type: Plan`` becomes one row in the corresponding typed table.
Multi-type plugin routing by ``note_type`` to the correct table.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.core.source_files import register_source_file as _register_source_file
from phdb.formats.vault_tasks_plans_md import (
    PlanRecord,
    TaskRecord,
    parse_plans,
    parse_tasks,
)
from phdb.log import get_logger

if TYPE_CHECKING:
    from phdb.core.plugin.manifest import PluginManifest
    from phdb.settings import Settings

log = get_logger("phdb.plugins.vault_tasks_plans")


@dataclass
class IngestSummary:
    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    errors: list[str] = field(default_factory=list)
    by_table: dict[str, int] = field(default_factory=dict)


_TASK_INSERT_SQL = """
INSERT OR IGNORE INTO tasks
    (schema_type, name, identifier, tier, status, effort, maintenance,
     project, created, updated, closure_date, closure_evidence,
     file_path, raw_hash, source_file_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_PLAN_INSERT_SQL = """
INSERT OR IGNORE INTO plans
    (schema_type, name, identifier, description, status, phase,
     effort, maintenance, created, updated,
     file_path, raw_hash, source_file_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


def _task_params(record: TaskRecord, source_file_id: int) -> list[object]:
    return [
        "Action",
        record.name,
        record.identifier,
        record.tier,
        record.status,
        record.effort,
        record.maintenance,
        record.project,
        record.created,
        record.updated,
        record.closure_date,
        record.closure_evidence,
        record.file_path,
        record.provenance.raw_hash,
        source_file_id,
    ]


def _plan_params(record: PlanRecord, source_file_id: int) -> list[object]:
    return [
        "Plan",
        record.name,
        record.identifier,
        record.description,
        record.status,
        record.phase,
        record.effort,
        record.maintenance,
        record.created,
        record.updated,
        record.file_path,
        record.provenance.raw_hash,
        source_file_id,
    ]


class VaultTasksPlansPlugin(PhdbSourcePlugin):
    """Vault task/plan dissolution plugin — multi-type."""

    SOURCE_KIND = "vault-tasks-plans"
    FILE_KIND = "md"
    TARGET_TABLE = "tasks"
    SCHEMA_TYPE = "VaultTask"
    BATCH_SIZE = 50

    def __init__(
        self,
        manifest: PluginManifest | None = None,
    ) -> None:
        super().__init__(manifest)  # type: ignore[arg-type]

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        if root.is_file():
            yield root, self.SOURCE_KIND
            return
        if root.is_dir():
            yield root, self.SOURCE_KIND

    def parse(self, path: Path) -> Iterator[TaskRecord | PlanRecord]:
        yield from parse_tasks(path)
        yield from parse_plans(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: TaskRecord | PlanRecord,
        *,
        source_file_id: int | None = None,
    ) -> int | None:
        sf_id = source_file_id if source_file_id is not None else 0

        if isinstance(record, TaskRecord):
            sql = _TASK_INSERT_SQL
            params = _task_params(record, sf_id)
        else:
            sql = _PLAN_INSERT_SQL
            params = _plan_params(record, sf_id)

        cur = conn.execute(sql, params)
        if cur.rowcount == 0:
            return None
        return int(cur.lastrowid)  # type: ignore[arg-type]

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings | None = None,
    ) -> IngestSummary:
        report = IngestSummary(source_path=str(source_path))
        source_file_id = _register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND, file_kind=self.FILE_KIND,
        )
        report.source_file_id = source_file_id

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            row_id = self.ingest_row(
                conn, record, source_file_id=source_file_id,
            )
            table = "tasks" if isinstance(record, TaskRecord) else "plans"
            if row_id is None:
                report.rows_skipped += 1
            else:
                report.rows_inserted += 1
                report.by_table[table] = (
                    report.by_table.get(table, 0) + 1
                )

            batch_count += 1
            if batch_count >= self.BATCH_SIZE:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[vault_tasks_plans] Done: %d yielded, %d inserted, %d skipped",
            report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        for tbl, cnt in sorted(report.by_table.items()):
            log.info("  %s: %d rows", tbl, cnt)
        return report
