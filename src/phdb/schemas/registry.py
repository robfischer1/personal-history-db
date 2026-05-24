"""Schema registry — the global @type → Schema lookup.

Phase 2 deliverable. The registry is the single source of truth for
which Schema.org ``@type`` strings phdb knows about and which DB tables
they're projected into. Plugins consult this registry at write time
(via ``upsert_entity`` and the action-schema FK validators); the Phase
6 DB_SCHEMA.md regenerator walks it to emit the docs.

Phase 3 wires plugin-manifest ``emits = [...]`` declarations through
this registry to enforce that declared emissions resolve to known
schemas at plugin load time.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phdb.schemas.base import Schema


@dataclass
class SchemaRegistry:
    """Process-wide schemas-by-name lookup.

    Tables are unique. ``schema_type`` (Schema.org @type) can map to
    multiple tables — e.g., ``DigitalDocument`` covers both the file-
    system-extracted ``documents`` table and the messages-decomposition
    ``digital_documents`` table; consumers route by table. The
    ``get_by_type`` lookup returns the first registered schema for a
    type; ``get_all_by_type`` returns the full list.
    """

    by_type: dict[str, list[type[Schema]]] = field(default_factory=dict)
    by_table: dict[str, type[Schema]] = field(default_factory=dict)

    def register(self, schema: type[Schema]) -> None:
        bucket = self.by_type.setdefault(schema.schema_type, [])
        if schema not in bucket:
            bucket.append(schema)
        self.by_table[schema.table_name] = schema

    def get_by_type(self, schema_type: str) -> type[Schema] | None:
        bucket = self.by_type.get(schema_type)
        return bucket[0] if bucket else None

    def get_all_by_type(self, schema_type: str) -> list[type[Schema]]:
        return list(self.by_type.get(schema_type, []))

    def get_by_table(self, table_name: str) -> type[Schema] | None:
        return self.by_table.get(table_name)

    def __iter__(self) -> Iterator[type[Schema]]:
        return iter(self.by_table.values())

    def __len__(self) -> int:
        return len(self.by_table)


_DEFAULT: SchemaRegistry | None = None


def default_schema_registry() -> SchemaRegistry:
    """Return the process-wide default schemas registry, building it lazily."""
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = SchemaRegistry()
        from phdb.schemas import canonical  # noqa: PLC0415 — lazy import to avoid cycle

        canonical.register_all(_DEFAULT)
    return _DEFAULT


def reset_default_schema_registry() -> None:
    """Test helper — force the next ``default_schema_registry()`` to rebuild."""
    global _DEFAULT
    _DEFAULT = None


def register_schema(schema: type[Schema]) -> None:
    """Convenience: register a schema in the process-wide default registry."""
    default_schema_registry().register(schema)


def get_schema(schema_type: str) -> type[Schema] | None:
    """Convenience: look up a schema by Schema.org @type."""
    return default_schema_registry().get_by_type(schema_type)


def all_schemas() -> list[type[Schema]]:
    """Return every registered schema (entities + actions + document-shaped)."""
    return list(default_schema_registry())


__all__ = [
    "SchemaRegistry",
    "all_schemas",
    "default_schema_registry",
    "get_schema",
    "register_schema",
    "reset_default_schema_registry",
]
