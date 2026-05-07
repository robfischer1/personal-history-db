"""Migration runner with namespaced project/instance support.

Discovers .sql files in project (0001-0999) and instance (1000+) migration
directories, tracks applied state via the schema_migrations table, and
applies pending migrations in numeric order.

Handles backwards compatibility with the legacy 3-digit migration IDs
(001_init etc.) already present in production databases.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from phdb.log import get_logger

log = get_logger("phdb.migrations")

_MIGRATION_RE = re.compile(r"^(\d{3,4})_(.+)\.sql$")

COMPAT_MAP: dict[str, str] = {
    "0001_init": "001_init",
    "0002_conversation_generalization": "002_conversation_generalization",
    "0003_health_sidecars": "003_health_sidecars",
    "0004_bookmarks": "004_bookmarks",
    "0005_connections": "005_connections",
}


@dataclass(frozen=True, order=True)
class Migration:
    """A discovered migration file, sortable by numeric prefix."""

    number: int
    migration_id: str
    path: Path

    @classmethod
    def from_path(cls, path: Path) -> Migration | None:
        m = _MIGRATION_RE.match(path.name)
        if not m:
            return None
        return cls(number=int(m.group(1)), migration_id=path.stem, path=path)


def _default_project_dir() -> Path:
    return Path(__file__).parent / "project"


class MigrationRunner:
    """Apply SQL migrations from project and instance directories."""

    def __init__(
        self,
        conn: sqlite3.Connection,
        project_dir: Path | None = None,
        instance_dir: Path | None = None,
    ) -> None:
        self.conn = conn
        self._dirs: list[Path] = []
        if project_dir is None:
            project_dir = _default_project_dir()
        if project_dir.is_dir():
            self._dirs.append(project_dir)
        if instance_dir and instance_dir.is_dir():
            self._dirs.append(instance_dir)

    def discover(self) -> list[Migration]:
        """Find all migration files across configured directories."""
        migrations: list[Migration] = []
        for d in self._dirs:
            for f in sorted(d.glob("*.sql")):
                m = Migration.from_path(f)
                if m:
                    migrations.append(m)
        migrations.sort()
        return migrations

    def applied(self) -> set[str]:
        """Return the set of migration IDs already recorded in the database."""
        try:
            rows = self.conn.execute("SELECT migration_id FROM schema_migrations").fetchall()
            return {r[0] for r in rows}
        except sqlite3.OperationalError:
            return set()

    def _is_applied(self, migration_id: str, applied_ids: set[str]) -> bool:
        """Check if a migration is applied, including compat aliases."""
        if migration_id in applied_ids:
            return True
        legacy_id = COMPAT_MAP.get(migration_id)
        return bool(legacy_id and legacy_id in applied_ids)

    def pending(self) -> list[Migration]:
        """Return migrations that haven't been applied yet, in order."""
        applied_ids = self.applied()
        return [m for m in self.discover() if not self._is_applied(m.migration_id, applied_ids)]

    def apply_one(self, migration: Migration) -> None:
        """Apply a single migration and record it."""
        log.info("Applying migration: %s", migration.migration_id)
        sql = migration.path.read_text(encoding="utf-8")
        self.conn.executescript(sql)

    def apply_pending(self) -> list[str]:
        """Apply all pending migrations in order. Returns list of applied IDs."""
        to_apply = self.pending()
        if not to_apply:
            log.info("No pending migrations")
            return []

        applied: list[str] = []
        for migration in to_apply:
            self.apply_one(migration)
            applied.append(migration.migration_id)

        log.info("Applied %d migration(s): %s", len(applied), ", ".join(applied))
        return applied

    def status(self) -> list[tuple[str, bool]]:
        """Return (migration_id, is_applied) for all discovered migrations."""
        applied_ids = self.applied()
        return [
            (m.migration_id, self._is_applied(m.migration_id, applied_ids))
            for m in self.discover()
        ]
