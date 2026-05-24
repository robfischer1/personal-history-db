"""Plugin ABC contract — PhdbPlugin / PhdbSourcePlugin / PhdbFacetPlugin.

Per Phase 0 Q4 override: plugins must inherit from an ABC, not a
typing.Protocol. Missing required methods raise TypeError at
instantiation — runtime validation over duck-typed structural
conformance.

Source plugins implement:
  - ``discover(root)``: scan filesystem for source files this plugin
    can ingest; yield (path, kind) tuples
  - ``parse(path)``: parse a single source file; yield typed records
  - ``ingest_row(conn, record)``: persist one record to its typed
    table; emit triples; project facets
  - ``register_cli(parser)``: add the plugin's CLI subcommands
  - ``register_tools(server)``: add the plugin's MCP tools
  - ``project_facets(emission_bus, record)``: optional; default no-op

Facet plugins implement:
  - ``consume(emission)``: receive a facet emission from a source
    plugin's project_facets call
  - ``coalesce()``: run identity coalescence over accumulated emissions
  - ``register_cli(parser)``: add CLI subcommands
  - ``register_tools(server)``: add MCP tools
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import sqlite3

    from phdb.core.plugin.manifest import PluginManifest
    from phdb.core.plugin.summary import IngestSummary


class PhdbPlugin(ABC):
    """Common base for all plugins — holds the manifest + lifecycle hooks."""

    manifest: PluginManifest

    def __init__(self, manifest: PluginManifest) -> None:
        self.manifest = manifest

    @property
    def name(self) -> str:
        return self.manifest.name

    @property
    def kind(self) -> str:
        return self.manifest.kind

    @abstractmethod
    def register_cli(self, parser: Any) -> None:
        """Register the plugin's CLI subcommands on a click/argparse group."""
        ...

    @abstractmethod
    def register_tools(self, server: Any) -> None:
        """Register the plugin's MCP tools on a FastMCP server."""
        ...


class PhdbSourcePlugin(PhdbPlugin):
    """Source plugin — ingests one data source into typed action rows."""

    @abstractmethod
    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a filesystem root; yield (path, source_kind) tuples
        for every file this plugin can ingest."""
        ...

    @abstractmethod
    def parse(self, path: Path) -> Iterator[Any]:
        """Parse a single source file; yield typed records consumable
        by ``ingest_row``."""
        ...

    @abstractmethod
    def ingest_row(
        self, conn: sqlite3.Connection, record: Any, **kwargs: Any
    ) -> int | None:
        """Persist a single record to its typed table.

        Returns the inserted row id on success, or ``None`` when the row was
        intentionally skipped (e.g., dedup hit, idempotent re-ingest). Plugins
        that never skip can return only ``int``; the wider return type covers
        plugins that perform conflict resolution at the row level.

        Plugins may declare additional keyword-only parameters (e.g.,
        ``source_file_id``, ``settings``); the ``**kwargs`` here keeps the
        Liskov contract permissive without forcing every override to enumerate
        the base signature.
        """
        ...

    def project_facets(self, emission_bus: Any, record: Any) -> None:
        """Optional — emit FacetEmission events to the bus.

        Default: no-op. Plugins that produce facet projections override
        this to dispatch to people/places/time/threads/topics. The bus
        ABC + EmissionBus implementation ship in Phase 4.
        """
        return None

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Any = None,
    ) -> IngestSummary:
        """Standard ingest runner — register source, parse, ingest, batch-commit.

        Plugins with custom run logic (resume, sidecar tables, identity
        resolution, channel iteration, etc.) should override this method.
        Requires ``SOURCE_KIND``, ``FILE_KIND``, and ``BATCH_SIZE`` class
        attributes.
        """
        from phdb.core.plugin.summary import IngestSummary
        from phdb.core.source_files import register_source_file

        log = logging.getLogger(f"phdb.plugins.{self.name}")

        report = IngestSummary(source_path=str(source_path))
        source_file_id = register_source_file(
            conn, source_path,
            source_kind=self.SOURCE_KIND,  # type: ignore[attr-defined]
            file_kind=self.FILE_KIND,  # type: ignore[attr-defined]
        )
        report.source_file_id = source_file_id

        batch_size: int = getattr(self, "BATCH_SIZE", 500)
        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            row_id = self.ingest_row(conn, record, source_file_id=source_file_id)
            if row_id is None:
                report.rows_skipped += 1
            else:
                report.rows_inserted += 1

            batch_count += 1
            if batch_count >= batch_size:
                conn.commit()
                batch_count = 0

        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.name, report.rows_yielded, report.rows_inserted, report.rows_skipped,
        )
        return report


class PhdbFacetPlugin(PhdbPlugin):
    """Facet plugin — consumes emissions and runs identity coalescence."""

    @abstractmethod
    def consume(self, emission: Any) -> None:
        """Receive a FacetEmission from a source plugin's project_facets call."""
        ...

    @abstractmethod
    def coalesce(self) -> dict[str, Any]:
        """Run identity coalescence over accumulated emissions; return summary."""
        ...


__all__ = [
    "PhdbFacetPlugin",
    "PhdbPlugin",
    "PhdbSourcePlugin",
]
