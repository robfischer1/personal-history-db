"""Schema primitives — ``FieldSpec``, ``EntityFK``, ``Sidecar``, base ABCs.

Designed around the WebPage Entity Factoring precedent (2026-05-22):
identity-bearing schemas are ``EntitySchema`` (one row per dedup key;
metadata last-write-wins); event-shaped schemas are ``ActionSchema``
(FK to entities; never duplicate entity fields). Both ultimately derive
from ``Schema`` which holds the table-name + DDL primitives.

The dataclass-vs-classvar split: ``FieldSpec`` rows are data; the
schema's own fields are class attributes describing the table. A
schema is a singleton describing one typed table; the schemas
themselves are not instantiated per row.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar


@dataclass(frozen=True)
class FieldSpec:
    """One column in a typed table.

    The ``sql_type`` is the literal SQLite type keyword (``INTEGER``,
    ``TEXT``, ``REAL``, ``BLOB``). The ``default`` is the literal SQL
    default expression (e.g. ``"strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"``);
    callers wrap strings as needed.
    """

    name: str
    sql_type: str = "TEXT"
    nullable: bool = True
    default: str | None = None  # literal SQL expression, not a Python value
    primary_key: bool = False
    references: str | None = None  # "source_files(id)" -> emits REFERENCES clause
    on_delete: str | None = None
    description: str | None = None

    def column_ddl(self) -> str:
        parts: list[str] = [self.name, self.sql_type]
        if self.primary_key:
            parts.append("PRIMARY KEY")
        if not self.nullable and not self.primary_key:
            parts.append("NOT NULL")
        if self.default is not None:
            parts.append(f"DEFAULT {self.default}")
        if self.references:
            parts.append(f"REFERENCES {self.references}")
            if self.on_delete:
                parts.append(f"ON DELETE {self.on_delete}")
        return " ".join(parts)


@dataclass(frozen=True)
class EntityFK:
    """Foreign-key field type pointing at an EntitySchema.

    Used by ActionSchema declarations to wire FK columns to canonical
    entity tables (per WebPage Entity Factoring precedent). At DDL
    generation time, this expands to a column with REFERENCES clause.
    """

    entity_table: str  # e.g. "web_pages"
    column_name: str | None = None  # defaults to "<entity>_id"
    nullable: bool = True

    def field_spec(self) -> FieldSpec:
        name = self.column_name or f"{self.entity_table.rstrip('s')}_id"
        return FieldSpec(
            name=name,
            sql_type="INTEGER",
            nullable=self.nullable,
            references=f"{self.entity_table}(id)",
        )


@dataclass(frozen=True)
class IndexSpec:
    """One index declaration on a typed table."""

    name: str
    columns: list[str]
    unique: bool = False
    where_clause: str | None = None
    if_not_exists: bool = True

    def index_ddl(self, table_name: str) -> str:
        unique = "UNIQUE " if self.unique else ""
        ine = "IF NOT EXISTS " if self.if_not_exists else ""
        cols = ", ".join(self.columns)
        sql = f"CREATE {unique}INDEX {ine}{self.name} ON {table_name}({cols})"
        if self.where_clause:
            sql += f" WHERE {self.where_clause}"
        return sql


@dataclass(frozen=True)
class Sidecar:
    """Sidecar table — source-specific extras joined to a parent typed row.

    Per Phase 0 Q5b default: when a plugin needs columns that don't fit
    the canonical schema, register them as a sidecar table keyed on the
    parent's ``id``. Phase 5+ wires sidecars into ingest at write time.
    """

    table_name: str
    parent_table: str
    fields: list[FieldSpec] = field(default_factory=list)
    indexes: list[IndexSpec] = field(default_factory=list)
    parent_fk_column: str = "parent_id"

    def field_specs(self) -> list[FieldSpec]:
        parent_fk = FieldSpec(
            name=self.parent_fk_column,
            sql_type="INTEGER",
            nullable=False,
            references=f"{self.parent_table}(id)",
            on_delete="CASCADE",
        )
        return [
            FieldSpec(name="id", sql_type="INTEGER", primary_key=True),
            parent_fk,
            *self.fields,
        ]


class Schema:
    """Base class for any typed-table schema declaration.

    Subclasses set the class-level constants ``table_name``,
    ``schema_type``, ``fields``, ``indexes``, ``sidecars``. The
    classmethods on this base produce the DDL.
    """

    table_name: ClassVar[str]
    schema_type: ClassVar[str]
    fields: ClassVar[list[FieldSpec]] = []
    indexes: ClassVar[list[IndexSpec]] = []
    sidecars: ClassVar[list[Sidecar]] = []
    date_column: ClassVar[str | None] = None
    description: ClassVar[str] = ""

    @classmethod
    def all_fields(cls) -> list[FieldSpec]:
        return list(cls.fields)

    @classmethod
    def all_indexes(cls) -> list[IndexSpec]:
        return list(cls.indexes)


class EntitySchema(Schema):
    """Identity-bearing schema — one row per ``dedup_key`` value.

    Per WebPage Entity Factoring precedent: entity rows are upserted
    with COALESCE last-write-wins on metadata fields, identified by
    the ``dedup_key`` field (e.g., ``normalized_url`` for WebPage).
    Action rows FK to entity rows via the auto-generated
    ``<entity>_id`` FK column.
    """

    dedup_key: ClassVar[str] = "id"
    # Fields that should be COALESCE'd (last-write-wins) on upsert.
    # Defaults to "all fields except id/dedup_key/created_at/source_file_id".
    coalesce_fields: ClassVar[list[str]] = []


class ActionSchema(Schema):
    """Event-shaped schema — one row per discrete action/observation.

    Per WebPage Entity Factoring precedent: action rows carry FK
    columns pointing at entities; they never duplicate entity fields.
    Multiple plugins may write to the same action schema (gmail + mbox
    + apple_dbs all emit ``EmailMessage``).
    """

    entity_refs: ClassVar[list[EntityFK]] = []


# ---------------------------------------------------------------------------
# Common shared field bundles — the messages-decomposition shape
# ---------------------------------------------------------------------------

def _provenance_fields() -> list[FieldSpec]:
    """The provenance + ingestion-timestamp tail every typed table carries."""
    return [
        FieldSpec("raw_hash", "TEXT"),
        FieldSpec("source_file_id", "INTEGER", references="source_files(id)"),
        FieldSpec(
            "created_at",
            "TEXT",
            nullable=False,
            default="(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
        ),
    ]


def _body_text_fields() -> list[FieldSpec]:
    """Chunkable body + provenance hash."""
    return [
        FieldSpec("body_text", "TEXT"),
        FieldSpec("body_text_source", "TEXT"),
        FieldSpec("body_text_hash", "TEXT"),
    ]


def _bulk_fields() -> list[FieldSpec]:
    """The bulk-signal pair shared by the messages-decomposition tables."""
    return [
        FieldSpec("is_bulk", "INTEGER", nullable=False, default="0"),
        FieldSpec("bulk_signal", "TEXT"),
    ]


def _byte_offset_fields() -> list[FieldSpec]:
    """Mbox-style byte offsets for re-fetch of original."""
    return [
        FieldSpec("source_byte_offset", "INTEGER"),
        FieldSpec("source_byte_length", "INTEGER"),
    ]


# Re-exported helpers — schema authors import these to compose tables.
__all__ = [
    "ActionSchema",
    "EntityFK",
    "EntitySchema",
    "FieldSpec",
    "IndexSpec",
    "Schema",
    "Sidecar",
    "_body_text_fields",
    "_bulk_fields",
    "_byte_offset_fields",
    "_provenance_fields",
]
