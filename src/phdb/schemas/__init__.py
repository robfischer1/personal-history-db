"""phdb.schemas — canonical Schema.org-keyed typed table definitions.

Phase 2 deliverable of the phdb Plugin Architecture plan (2026-05-22).

Each schema is a typed Python dataclass that knows how to (a) emit its
own ``CREATE TABLE`` DDL, (b) emit ``ALTER TABLE`` migrations as the
diff against ``sqlite_master``, and (c) for entity schemas, register an
``upsert_<entity>()`` helper with COALESCE last-write-wins semantics.

Phase 0 Q5 reframe: schemas are decoupled from plugins. Plugins declare
emission (``emits = ["EmailMessage", "Observation"]``); the schemas
themselves are owned by this pillar. Multiple plugins can write to the
same schema (gmail + mbox + apple_dbs all emit ``EmailMessage``).

Phase 0 lineage from WebPage Entity Factoring (2026-05-22): the
entity-vs-action split is the canonical pattern. ``EntitySchema`` rows
own identity (one row per dedup key); ``ActionSchema`` rows FK to
entities and never duplicate entity fields.
"""

from __future__ import annotations

from phdb.schemas.base import (
    ActionSchema,
    EntityFK,
    EntitySchema,
    FieldSpec,
    Schema,
    Sidecar,
)
from phdb.schemas.ddl import generate_create_table, generate_indexes
from phdb.schemas.registry import (
    SchemaRegistry,
    all_schemas,
    default_schema_registry,
    get_schema,
    register_schema,
)
from phdb.schemas.upsert import build_upsert_sql, upsert_entity

__all__ = [
    "ActionSchema",
    "EntityFK",
    "EntitySchema",
    "FieldSpec",
    "Schema",
    "SchemaRegistry",
    "Sidecar",
    "all_schemas",
    "build_upsert_sql",
    "default_schema_registry",
    "generate_create_table",
    "generate_indexes",
    "get_schema",
    "register_schema",
    "upsert_entity",
]
