"""Adapter base class and AdapterRow.

Every ingester subclasses Adapter and implements iter_rows(). The base class
provides the run() method that handles source_files registration, batched
INSERT OR IGNORE, commit cadence, and progress logging — the ~50 lines of
boilerplate that every legacy ingester repeats.

Sidecar-table API (Phase 8): adapters declare sidecar tables via the
sidecar_tables class attribute. The base class auto-creates them and
auto-inserts child rows from AdapterRow.sidecar_rows after parent insert.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from phdb.log import get_logger
from phdb.triples import resolve_node, get_predicate

if TYPE_CHECKING:
    from phdb.settings import IdentitySettings, Settings

log = get_logger("phdb.adapters")

# Predicate IDs are looked up once per connection and cached here.
_predicate_cache: dict[str, int] = {}


class DedupStrategy(Enum):
    """How the adapter produces dedup keys for INSERT OR IGNORE."""

    RFC822_MESSAGE_ID = "rfc822"
    PLATFORM_SYNTHETIC = "synthetic"
    SOURCE_POSITION = "position"
    CONTENT_HASH = "hash"


@dataclass(frozen=True)
class SidecarColumn:
    """A single column in a sidecar table."""

    name: str
    sql_type: str
    nullable: bool = True
    default: str | None = None


@dataclass(frozen=True)
class SidecarTableDef:
    """Declares a sidecar table owned by an adapter.

    The base class uses this to auto-create the table (CREATE TABLE IF NOT
    EXISTS) and auto-insert child rows from AdapterRow.sidecar_rows keyed
    by table_name.
    """

    table_name: str
    columns: tuple[SidecarColumn, ...]
    parent_fk_column: str = "parent_message_id"
    parent_table: str = "messages"
    on_delete: str = "CASCADE"

    def create_table_sql(self) -> str:
        """Generate idempotent CREATE TABLE DDL."""
        lines = [f"CREATE TABLE IF NOT EXISTS {self.table_name} ("]
        lines.append("    id INTEGER PRIMARY KEY,")
        lines.append(
            f"    {self.parent_fk_column} INTEGER NOT NULL"
            f" REFERENCES {self.parent_table}(id) ON DELETE {self.on_delete},"
        )
        for i, col in enumerate(self.columns):
            null = "" if col.nullable else " NOT NULL"
            default = f" DEFAULT {col.default}" if col.default else ""
            comma = "," if i < len(self.columns) - 1 else ""
            lines.append(f"    {col.name} {col.sql_type}{null}{default}{comma}")
        lines.append(")")
        return "\n".join(lines)

    def insert_sql(self) -> str:
        """Generate parameterized INSERT statement."""
        cols = [self.parent_fk_column] + [c.name for c in self.columns]
        placeholders = ", ".join("?" for _ in cols)
        col_list = ", ".join(cols)
        return f"INSERT INTO {self.table_name} ({col_list}) VALUES ({placeholders})"


@dataclass
class AdapterRow:
    """A single row to insert into the messages table (or a domain table)."""

    schema_type: str = "Message"
    rfc822_message_id: str | None = None
    in_reply_to: str | None = None
    references_chain: str | None = None
    gmail_thread_id: str | None = None
    gmail_labels: str | None = None
    subject: str | None = None
    sender_address: str | None = None
    sender_name: str | None = None
    sender_domain: str | None = None
    direction: str = "unknown"
    date_sent: str | None = None
    date_received: str | None = None
    body_text: str | None = None
    body_html: str | None = None
    body_text_source: str | None = None
    is_multipart: int = 0
    has_attachments: int = 0
    attachment_count: int = 0
    is_bulk: int = 0
    bulk_signal: str | None = None
    source_byte_offset: int | None = None
    source_byte_length: int | None = None
    raw_hash: str | None = None
    body_text_hash: str | None = None

    # AI session message fields (all default None — ignored by existing adapters)
    kind: str | None = None
    role: str | None = None
    parent_uuid: str | None = None
    tool_name: str | None = None
    tool_use_id: str | None = None
    model: str | None = None
    payload: str | None = None  # JSON

    # AI session thread fields
    thread_metadata: str | None = None  # JSON
    thread_cwd: str | None = None

    # Document-specific fields (used when target_table='documents')
    file_path: str | None = None
    file_size: int | None = None
    ctime: str | None = None
    bucket: str | None = None

    recipients: list[dict[str, str]] = field(default_factory=list)
    attachments: list[dict[str, str | int | None]] = field(default_factory=list)
    thread_key: str | None = None
    extra: dict[str, object] = field(default_factory=dict)

    # Sidecar-table rows: key = table_name, value = list of row dicts
    # Each dict maps column_name -> value (parent FK auto-filled by base)
    sidecar_rows: dict[str, list[dict[str, object]]] = field(default_factory=dict)


@dataclass
class IngestReport:
    """Summary returned by Adapter.run()."""

    adapter_name: str
    source_path: str
    source_file_id: int
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    threads_created: int = 0
    errors: list[str] = field(default_factory=list)


_SFID = "_sfid"

_TYPED_TABLE_MAP: dict[str, tuple[str, tuple[tuple[str, str], ...]]] = {
    "Observation": ("observations", (
        ("schema_type", "schema_type"),
        ("observation_key", "rfc822_message_id"),
        ("type_identifier", "sender_name"),
        ("subject", "subject"),
        ("source_device", "sender_address"),
        ("direction", "direction"),
        ("date_observed", "date_sent"),
        ("date_end", "date_received"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "Message": ("chat_messages", (
        ("schema_type", "schema_type"),
        ("message_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("sender_domain", "sender_domain"),
        ("direction", "direction"),
        ("date_sent", "date_sent"),
        ("date_received", "date_received"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_multipart", "is_multipart"),
        ("has_attachments", "has_attachments"),
        ("attachment_count", "attachment_count"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "SearchAction": ("search_actions", (
        ("schema_type", "schema_type"),
        ("action_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("source_device", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_performed", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "EmailMessage": ("emails", (
        ("schema_type", "schema_type"),
        ("rfc822_message_id", "rfc822_message_id"),
        ("in_reply_to", "in_reply_to"),
        ("references_chain", "references_chain"),
        ("gmail_thread_id", "gmail_thread_id"),
        ("gmail_labels", "gmail_labels"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("sender_domain", "sender_domain"),
        ("direction", "direction"),
        ("date_sent", "date_sent"),
        ("date_received", "date_received"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_multipart", "is_multipart"),
        ("has_attachments", "has_attachments"),
        ("attachment_count", "attachment_count"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "Conversation": ("conversations_messages", (
        ("schema_type", "schema_type"),
        ("conversation_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("sender_domain", "sender_domain"),
        ("direction", "direction"),
        ("date_sent", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("kind", "kind"),
        ("role", "role"),
        ("parent_uuid", "parent_uuid"),
        ("tool_name", "tool_name"),
        ("tool_use_id", "tool_use_id"),
        ("model", "model"),
        ("payload", "payload"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "ExerciseAction": ("exercise_actions", (
        ("schema_type", "schema_type"),
        ("exercise_key", "rfc822_message_id"),
        ("type_identifier", "sender_name"),
        ("subject", "subject"),
        ("source_device", "sender_address"),
        ("sender_domain", "sender_domain"),
        ("direction", "direction"),
        ("date_performed", "date_sent"),
        ("date_end", "date_received"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "ListenAction": ("listen_actions", (
        ("schema_type", "schema_type"),
        ("listen_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("artist_name", "sender_name"),
        ("source_device", "sender_address"),
        ("direction", "direction"),
        ("date_listened", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "WatchAction": ("watch_actions", (
        ("schema_type", "schema_type"),
        ("watch_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("platform_name", "sender_name"),
        ("source_device", "sender_address"),
        ("direction", "direction"),
        ("date_watched", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "Action": ("actions", (
        ("schema_type", "schema_type"),
        ("action_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_performed", "date_sent"),
        ("date_received", "date_received"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "Event": ("events", (
        ("schema_type", "schema_type"),
        ("event_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_occurred", "date_sent"),
        ("date_received", "date_received"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "Product": ("products", (
        ("schema_type", "schema_type"),
        ("product_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_recorded", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "OrderAction": ("order_actions", (
        ("schema_type", "schema_type"),
        ("order_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_ordered", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "LikeAction": ("like_actions", (
        ("schema_type", "schema_type"),
        ("like_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_liked", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "Person": ("persons", (
        ("schema_type", "schema_type"),
        ("person_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_recorded", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "SocialMediaPosting": ("social_postings", (
        ("schema_type", "schema_type"),
        ("posting_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("sender_domain", "sender_domain"),
        ("direction", "direction"),
        ("date_posted", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "Comment": ("comments", (
        ("schema_type", "schema_type"),
        ("comment_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_posted", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "Place": ("places", (
        ("schema_type", "schema_type"),
        ("place_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_recorded", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "TravelAction": ("travel_actions", (
        ("schema_type", "schema_type"),
        ("travel_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_traveled", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("source_byte_offset", "source_byte_offset"),
        ("source_byte_length", "source_byte_length"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "GeoShape": ("geo_shapes", (
        ("schema_type", "schema_type"),
        ("geo_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_recorded", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "Book": ("books", (
        ("schema_type", "schema_type"),
        ("book_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_recorded", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "MedicalRecord": ("medical_records", (
        ("schema_type", "schema_type"),
        ("record_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_recorded", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "Review": ("reviews", (
        ("schema_type", "schema_type"),
        ("review_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_reviewed", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "InviteAction": ("invite_actions", (
        ("schema_type", "schema_type"),
        ("invite_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_invited", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "CreativeWork": ("creative_works", (
        ("schema_type", "schema_type"),
        ("work_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_created", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "WebPage": ("web_pages", (
        ("schema_type", "schema_type"),
        ("page_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_recorded", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "JoinAction": ("join_actions", (
        ("schema_type", "schema_type"),
        ("join_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_joined", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "DigitalDocument": ("digital_documents", (
        ("schema_type", "schema_type"),
        ("doc_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_created", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
    "Thing": ("things", (
        ("schema_type", "schema_type"),
        ("thing_key", "rfc822_message_id"),
        ("subject", "subject"),
        ("sender_address", "sender_address"),
        ("sender_name", "sender_name"),
        ("direction", "direction"),
        ("date_recorded", "date_sent"),
        ("body_text", "body_text"),
        ("body_text_source", "body_text_source"),
        ("body_text_hash", "body_text_hash"),
        ("is_bulk", "is_bulk"),
        ("bulk_signal", "bulk_signal"),
        ("raw_hash", "raw_hash"),
        ("source_file_id", _SFID),
    )),
}

_typed_sql_cache: dict[str, str] = {}


def _get_typed_insert_sql(schema_type: str) -> str:
    """Generate and cache INSERT OR IGNORE SQL for a typed table."""
    if schema_type in _typed_sql_cache:
        return _typed_sql_cache[schema_type]
    table, columns = _TYPED_TABLE_MAP[schema_type]
    col_names = ", ".join(c[0] for c in columns)
    placeholders = ", ".join("?" for _ in columns)
    sql = f"INSERT OR IGNORE INTO [{table}] ({col_names}) VALUES ({placeholders})"
    _typed_sql_cache[schema_type] = sql
    return sql


def _get_typed_insert_params(
    row: AdapterRow, schema_type: str, source_file_id: int
) -> tuple[object, ...]:
    """Extract parameter tuple from AdapterRow for a typed table INSERT."""
    _, columns = _TYPED_TABLE_MAP[schema_type]
    params: list[object] = []
    for _, attr in columns:
        if attr == _SFID:
            params.append(source_file_id)
        else:
            params.append(getattr(row, attr))
    return tuple(params)


_INSERT_ATTACHMENT_SQL = """\
INSERT INTO attachments (schema_type, message_id, filename, content_type,
    content_disposition, size_bytes, on_disk_path, content_hash)
VALUES ('DigitalDocument', ?, ?, ?, ?, ?, ?, ?)"""

_INSERT_DOCUMENT_SQL = """\
INSERT OR IGNORE INTO documents (
    schema_type, rfc822_message_id, subject,
    file_path, file_size, mtime, ctime,
    body_text, body_text_source, body_text_hash,
    raw_hash, is_bulk, source_file_id, bucket
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

_INSERT_ARTICLE_SQL = """\
INSERT OR IGNORE INTO articles (
    schema_type, subject, url, publisher, creator, description, image_url,
    categories, tags, aliases, note_type, author_type,
    file_path, file_size, ctime, mtime,
    body_text, body_text_source, body_text_hash,
    raw_hash, bucket, source_file_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

_INSERT_CLIPPING_SQL = """\
INSERT OR IGNORE INTO clippings (
    schema_type, subject, url, publisher, creator, description, image_url,
    categories, tags, aliases, note_type, author_type,
    file_path, file_size, ctime, mtime,
    body_text, body_text_source, body_text_hash,
    raw_hash, bucket, source_file_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

_INSERT_PHOTOGRAPH_SQL = """\
INSERT OR IGNORE INTO photographs (
    schema_type, source_path, album_root, content_hash,
    captured_at, digitized_at, width, height, format, file_size,
    camera_make, camera_model, lens,
    focal_length, aperture, exposure_time, iso,
    latitude, longitude, altitude, rating,
    source_org, source_kind, provenance,
    raw_hash, source_file_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

_UPSERT_PHOTOGRAPH_SQL = """\
INSERT INTO photographs (
    schema_type, source_path, album_root, content_hash,
    captured_at, digitized_at, width, height, format, file_size,
    camera_make, camera_model, lens,
    focal_length, aperture, exposure_time, iso,
    latitude, longitude, altitude, rating,
    source_org, source_kind, provenance,
    raw_hash, source_file_id
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(source_file_id, raw_hash) DO UPDATE SET
    content_hash=excluded.content_hash, captured_at=excluded.captured_at,
    digitized_at=excluded.digitized_at, width=excluded.width, height=excluded.height,
    format=excluded.format, file_size=excluded.file_size,
    camera_make=excluded.camera_make, camera_model=excluded.camera_model,
    lens=excluded.lens, focal_length=excluded.focal_length,
    aperture=excluded.aperture, exposure_time=excluded.exposure_time,
    iso=excluded.iso, latitude=excluded.latitude, longitude=excluded.longitude,
    altitude=excluded.altitude, rating=excluded.rating"""


class Adapter(ABC):
    """Base class for personal-history-db ingesters."""

    name: str
    source_kind: str
    file_kind: str
    schema_type: str = "Message"
    target_table: str = "messages"
    dedup_strategy: DedupStrategy = DedupStrategy.CONTENT_HASH
    batch_size: int = 500
    sidecar_tables: ClassVar[list[SidecarTableDef]] = []
    _settings: Settings | None = None
    _sidecar_tables_ensured: bool = False

    def owner_sender(self, platform: str) -> tuple[str, str]:
        """Return (sender_address, sender_name) for the database owner.

        Uses the first entry in IdentitySettings.owner_names when available,
        falls back to "owner" when no identity is configured.
        """
        if self._settings and self._settings.identity.owner_names:
            name = next(iter(self._settings.identity.owner_names))
            return f"{platform}:{name.lower()}", name
        return f"{platform}:owner", "owner"

    @abstractmethod
    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        """Yield structured rows from the source file."""

    def parse_date(self, raw: str) -> str | None:
        """Parse a raw date string to ISO-8601. Override for source-specific formats."""
        return raw

    def compute_raw_hash(self, row: AdapterRow) -> str:
        """Compute the dedup hash for a row."""
        seed = f"{self.source_kind}|{row.rfc822_message_id or ''}|{row.date_sent or ''}|{(row.body_text or '')[:200]}"
        return hashlib.sha256(seed.encode("utf-8")).hexdigest()

    def detect_bulk(self, row: AdapterRow) -> tuple[bool, str | None]:
        """Detect if a row is bulk/automated. Override for source-specific rules."""
        return False, None

    def infer_direction(self, row: AdapterRow, identity: IdentitySettings) -> str:
        """Infer message direction from sender address and identity config."""
        if not row.sender_address:
            return "unknown"
        if identity.is_me(row.sender_address):
            if row.recipients and any(identity.is_me(r.get("address", "")) for r in row.recipients):
                return "self"
            return "outbound"
        return "inbound"

    def compute_session_uuid(self, source_path: Path) -> str | None:
        """Compute a stable session UUID for this source, if available.

        Override in adapters that ingest one-file-per-session formats (e.g.
        Claude Code's `<session-uuid>.jsonl`). Returning a non-None value
        opts the source into UUID-based dedup at the source_files level —
        the same session ingested under a renamed/moved path will update
        the existing row rather than register a new one.

        Default: None (path-based dedup, current behavior).
        """
        return None

    def validate_source_path(self, source_path: Path) -> None:
        """Raise to refuse ingest of a path that violates an adapter rule.

        Default: no-op. Adapters that have a canonical source location can
        override to reject other locations (see ClaudeCodeAdapter).
        """
        return None

    def ensure_sidecar_tables(self, conn: sqlite3.Connection) -> None:
        """Create declared sidecar tables if they don't exist.

        Called automatically by run() on first invocation. Adapters with
        custom run() that use sidecar_tables should call this explicitly.
        """
        if self._sidecar_tables_ensured:
            return
        for tdef in self.sidecar_tables:
            conn.execute(tdef.create_table_sql())
        self._sidecar_tables_ensured = True

    def insert_sidecar_rows(
        self, conn: sqlite3.Connection, parent_id: int, row: AdapterRow
    ) -> None:
        """Insert sidecar rows declared on an AdapterRow.

        Each key in row.sidecar_rows must match a table_name in sidecar_tables.
        The parent FK column is auto-filled with parent_id.
        """
        if not row.sidecar_rows:
            return
        table_map = {t.table_name: t for t in self.sidecar_tables}
        for table_name, rows in row.sidecar_rows.items():
            tdef = table_map.get(table_name)
            if tdef is None:
                log.warning(
                    "Sidecar rows for undeclared table %r (adapter=%s)",
                    table_name, self.name,
                )
                continue
            sql = tdef.insert_sql()
            for srow in rows:
                values = [parent_id] + [srow.get(c.name) for c in tdef.columns]
                conn.execute(sql, values)

    def pre_insert(
        self, conn: sqlite3.Connection, row: AdapterRow, source_file_id: int
    ) -> AdapterRow | None:
        """Hook called before inserting a row into the database.

        Override to write to sidecar tables, transform the row, or skip it.
        Return the (possibly modified) row to proceed with insert, or None to
        skip this row entirely. Default: pass-through.
        """
        return row

    def post_insert(
        self, conn: sqlite3.Connection, row: AdapterRow, inserted_id: int
    ) -> None:
        """Hook called after a row is successfully inserted.

        Override to write to sidecar tables that need the parent row's ID.
        Default: no-op.
        """

    def _register_source(
        self, conn: sqlite3.Connection, source_path: Path
    ) -> int:
        """Register the source file and return its ID.

        Uses a dual-conflict-target UPSERT so that adapters which provide a
        session_uuid via compute_session_uuid() get UUID-based dedup, while
        adapters that don't keep the original path-based behavior.
        """
        session_uuid = self.compute_session_uuid(source_path)
        cur = conn.execute(
            """INSERT INTO source_files (source_path, source_org, file_kind, source_kind, session_uuid, ingested_at)
               VALUES (?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
               ON CONFLICT(source_path) DO UPDATE
                 SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                     session_uuid = COALESCE(source_files.session_uuid, excluded.session_uuid)
               ON CONFLICT(source_kind, session_uuid) WHERE session_uuid IS NOT NULL
                 DO UPDATE SET source_path = excluded.source_path,
                               ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
               RETURNING id""",
            (str(source_path), None, self.file_kind, self.source_kind, session_uuid),
        )
        row = cur.fetchone()
        assert row is not None
        return int(row[0])

    def _insert_row(
        self, conn: sqlite3.Connection, row: AdapterRow, source_file_id: int
    ) -> int | None:
        """Insert a single row. Routes to typed tables by schema_type or target_table."""
        if self.target_table == "documents":
            return self._insert_document(conn, row, source_file_id)
        if self.target_table == "articles":
            return self._insert_article(conn, row, source_file_id)
        if self.target_table == "clippings":
            return self._insert_clipping(conn, row, source_file_id)
        if self.target_table == "photographs":
            return self._insert_photograph(conn, row, source_file_id)
        if row.schema_type not in _TYPED_TABLE_MAP:
            raise ValueError(
                f"No typed table mapping for schema_type={row.schema_type!r}"
            )
        sql = _get_typed_insert_sql(row.schema_type)
        params = _get_typed_insert_params(row, row.schema_type, source_file_id)
        cur = conn.execute(sql, params)
        if cur.rowcount == 0:
            return None
        return cur.lastrowid

    def _insert_document(
        self, conn: sqlite3.Connection, row: AdapterRow, source_file_id: int
    ) -> int | None:
        """Insert a single row into the documents typed table. Returns doc ID or None if skipped."""
        cur = conn.execute(
            _INSERT_DOCUMENT_SQL,
            (
                row.schema_type, row.rfc822_message_id, row.subject,
                row.file_path, row.file_size, row.date_sent, row.ctime,
                row.body_text, row.body_text_source, row.body_text_hash,
                row.raw_hash, row.is_bulk, source_file_id, row.bucket,
            ),
        )
        if cur.rowcount == 0:
            return None
        return cur.lastrowid

    def _insert_article(
        self, conn: sqlite3.Connection, row: AdapterRow, source_file_id: int
    ) -> int | None:
        """Insert a single row into the articles typed table. Returns article ID or None if skipped.

        Article-specific fields travel on AdapterRow.extra (keyed by column name);
        shared document-shaped fields use the standard AdapterRow attributes.
        """
        def _s(key: str) -> str | None:
            v = row.extra.get(key)
            return None if v is None else str(v)

        cur = conn.execute(
            _INSERT_ARTICLE_SQL,
            (
                row.schema_type, row.subject,
                _s("url"), _s("publisher"), _s("creator"),
                _s("description"), _s("image_url"),
                _s("categories"), _s("tags"), _s("aliases"),
                _s("note_type"), _s("author_type"),
                row.file_path, row.file_size, row.ctime, _s("mtime"),
                row.body_text, row.body_text_source, row.body_text_hash,
                row.raw_hash, row.bucket, source_file_id,
            ),
        )
        if cur.rowcount == 0:
            return None
        return cur.lastrowid

    def _insert_clipping(
        self, conn: sqlite3.Connection, row: AdapterRow, source_file_id: int
    ) -> int | None:
        """Insert a single row into the clippings typed table."""
        def _s(key: str) -> str | None:
            v = row.extra.get(key)
            return None if v is None else str(v)

        cur = conn.execute(
            _INSERT_CLIPPING_SQL,
            (
                row.schema_type, row.subject,
                _s("url"), _s("publisher"), _s("creator"),
                _s("description"), _s("image_url"),
                _s("categories"), _s("tags"), _s("aliases"),
                _s("note_type"), _s("author_type"),
                row.file_path, row.file_size, row.ctime, _s("mtime"),
                row.body_text, row.body_text_source, row.body_text_hash,
                row.raw_hash, row.bucket, source_file_id,
            ),
        )
        if cur.rowcount == 0:
            return None
        return cur.lastrowid

    def _insert_photograph(
        self, conn: sqlite3.Connection, row: AdapterRow, source_file_id: int
    ) -> int | None:
        """Insert a single row into the photographs typed table.

        Photo-specific fields travel on AdapterRow.extra (keyed by column name);
        uses upsert SQL when the adapter's _upsert_mode flag is set.
        """
        def _s(key: str) -> str | None:
            v = row.extra.get(key)
            return None if v is None else str(v)

        def _f(key: str) -> float | None:
            v = row.extra.get(key)
            return None if v is None else float(v)

        def _i(key: str) -> int | None:
            v = row.extra.get(key)
            return None if v is None else int(v)

        sql = _UPSERT_PHOTOGRAPH_SQL if getattr(self, "_upsert_mode", False) else _INSERT_PHOTOGRAPH_SQL
        params = (
            row.schema_type, _s("source_path"), _s("album_root"), _s("content_hash"),
            _s("captured_at"), _s("digitized_at"), _i("width"), _i("height"),
            _s("format"), _i("file_size"),
            _s("camera_make"), _s("camera_model"), _s("lens"),
            _f("focal_length"), _f("aperture"), _f("exposure_time"), _i("iso"),
            _f("latitude"), _f("longitude"), _f("altitude"), _i("rating"),
            _s("source_org"), _s("source_kind"), _s("provenance"),
            row.raw_hash, source_file_id,
        )
        cur = conn.execute(sql, params)
        if cur.rowcount == 0:
            return None
        return cur.lastrowid

    @staticmethod
    def _resolve_predicate_id(conn: sqlite3.Connection, name: str) -> int:
        """Look up a predicate ID by name, caching across calls."""
        if name in _predicate_cache:
            return _predicate_cache[name]
        pred = get_predicate(conn, name)
        if pred is None:
            raise ValueError(f"Predicate {name!r} not found — run migration 0018")
        _predicate_cache[name] = pred["id"]
        return pred["id"]

    def _target_table_for_row(self, row: AdapterRow) -> str:
        """Return the DB table name that this row was inserted into."""
        if self.target_table in ("documents", "articles", "clippings", "photographs"):
            return self.target_table
        if row.schema_type in _TYPED_TABLE_MAP:
            return _TYPED_TABLE_MAP[row.schema_type][0]
        return self.target_table

    def _emit_recipient_triples(
        self,
        conn: sqlite3.Connection,
        row_id: int,
        row: AdapterRow,
    ) -> None:
        """Emit sentTo triples for a message's recipients.

        Creates a 'record' node for the message row and a 'contact' node for
        each recipient address, then links them with a sentTo triple.
        No conn.commit() — the caller's batch loop handles that.
        """
        if not row.recipients:
            return

        source_table = self._target_table_for_row(row)
        sent_to_id = self._resolve_predicate_id(conn, "sentTo")

        # Resolve the record node for this message row
        record_label = f"{source_table}:{row_id}"
        record_node_id = resolve_node(
            conn, record_label, "record",
            source_table=source_table, source_id=row_id,
        )

        for r in row.recipients:
            address = r.get("address", "")
            if not address:
                continue

            contact_node_id = resolve_node(
                conn, address.lower(), "contact",
                source_table=source_table, source_id=row_id,
            )

            conn.execute(
                "INSERT OR IGNORE INTO triples"
                " (subject_node_id, predicate_id, object_node_id,"
                "  provenance, source_ref)"
                " VALUES (?, ?, ?, 'adapter', ?)",
                (record_node_id, sent_to_id, contact_node_id, self.source_kind),
            )

    def _insert_sidecars(
        self, conn: sqlite3.Connection, message_id: int, row: AdapterRow
    ) -> None:
        """Emit recipient triples and insert attachments for a message."""
        self._emit_recipient_triples(conn, message_id, row)
        for a in row.attachments:
            conn.execute(
                _INSERT_ATTACHMENT_SQL,
                (
                    message_id,
                    a.get("filename"),
                    a.get("content_type"),
                    a.get("content_disposition"),
                    a.get("size_bytes"),
                    a.get("on_disk_path"),
                    a.get("content_hash"),
                ),
            )

    def _upsert_thread(
        self,
        conn: sqlite3.Connection,
        thread_key: str,
        participants: list[str] | None = None,
        metadata: str | None = None,
        cwd: str | None = None,
    ) -> tuple[int, bool]:
        """Find or create a thread node by (source_kind, thread_key).

        Returns (thread_node_id, created). The node uses kind='thread' and
        label='{source_kind}:{thread_key}' for global uniqueness.
        """
        label = f"{self.source_kind}:{thread_key}"
        existing = conn.execute(
            "SELECT id FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
            (label.lower(),),
        ).fetchone()
        if existing:
            return existing[0], False

        node_id = resolve_node(conn, label, "thread")
        return node_id, True  # type: ignore[return-value]

    def _link_message_thread(
        self, conn: sqlite3.Connection, message_id: int, thread_node_id: int,
        *, row: AdapterRow | None = None,
    ) -> None:
        """Emit an inThread triple from the message's record node to the thread node."""
        # Determine source table for the record node label
        if row is not None:
            source_table = self._target_table_for_row(row)
        else:
            # Fallback: use the adapter's default typed table
            if self.target_table in ("documents", "articles", "clippings", "photographs"):
                source_table = self.target_table
            elif self.schema_type in _TYPED_TABLE_MAP:
                source_table = _TYPED_TABLE_MAP[self.schema_type][0]
            else:
                source_table = self.target_table

        in_thread_id = self._resolve_predicate_id(conn, "inThread")

        record_label = f"{source_table}:{message_id}"
        record_node_id = resolve_node(
            conn, record_label, "record",
            source_table=source_table, source_id=message_id,
        )

        conn.execute(
            "INSERT OR IGNORE INTO triples"
            " (subject_node_id, predicate_id, object_node_id,"
            "  provenance, source_ref)"
            " VALUES (?, ?, ?, 'adapter', ?)",
            (record_node_id, in_thread_id, thread_node_id, self.source_kind),
        )

    def _update_thread_aggregates(
        self,
        conn: sqlite3.Connection,
        thread_id: int,
        date_lo: str | None = None,
        date_hi: str | None = None,
    ) -> None:
        """No-op — thread aggregates are now derived from triples.

        Kept as a method stub so callers don't need changes until the
        threads table is dropped (task #17).
        """

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestReport:
        """Full ingest pipeline: register source -> iter_rows -> batch insert -> commit."""
        report = IngestReport(
            adapter_name=self.name,
            source_path=str(source_path),
            source_file_id=0,
        )

        self._settings = settings
        self.validate_source_path(source_path)
        if self.sidecar_tables:
            self.ensure_sidecar_tables(conn)
        source_file_id = self._register_source(conn, source_path)
        report.source_file_id = source_file_id
        log.info("[%s] Source registered: id=%d path=%s", self.name, source_file_id, source_path)

        _touched_threads: set[int] = set()
        _thread_dates: dict[int, tuple[str, str]] = {}
        _is_document = self.target_table in ("documents", "articles", "clippings", "photographs")
        batch_count = 0
        for row in self.iter_rows(source_path):
            report.rows_yielded += 1

            if not row.raw_hash:
                row.raw_hash = self.compute_raw_hash(row)
            if row.body_text and not row.body_text_hash:
                row.body_text_hash = hashlib.sha256(row.body_text.encode("utf-8")).hexdigest()

            is_bulk, signal = self.detect_bulk(row)
            if is_bulk:
                row.is_bulk = 1
                row.bulk_signal = signal

            if not _is_document and row.direction == "unknown" and settings.identity.is_configured:
                row.direction = self.infer_direction(row, settings.identity)

            row = self.pre_insert(conn, row, source_file_id)
            if row is None:
                report.rows_skipped += 1
                continue

            row_id = self._insert_row(conn, row, source_file_id)
            if row_id is None:
                report.rows_skipped += 1
                continue

            report.rows_inserted += 1
            self.post_insert(conn, row, row_id)

            if row.sidecar_rows:
                self.insert_sidecar_rows(conn, row_id, row)

            if not _is_document:
                self._insert_sidecars(conn, row_id, row)

                if row.thread_key:
                    thread_id, created = self._upsert_thread(
                        conn, row.thread_key,
                        metadata=row.thread_metadata,
                        cwd=row.thread_cwd,
                    )
                    self._link_message_thread(conn, row_id, thread_id, row=row)
                    if created:
                        report.threads_created += 1
                    _touched_threads.add(thread_id)
                    rd = row.date_sent
                    if rd and thread_id in _thread_dates:
                        lo, hi = _thread_dates[thread_id]
                        _thread_dates[thread_id] = (min(lo, rd), max(hi, rd))
                    elif rd:
                        _thread_dates[thread_id] = (rd, rd)

            batch_count += 1
            if batch_count >= self.batch_size:
                conn.commit()
                batch_count = 0

        conn.commit()

        if _touched_threads:
            for tid in _touched_threads:
                dates = _thread_dates.get(tid)
                row_date_lo = dates[0] if dates else None
                row_date_hi = dates[1] if dates else None
                self._update_thread_aggregates(conn, tid, row_date_lo, row_date_hi)
            conn.commit()

        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, source_file_id),
        )
        conn.commit()

        log.info(
            "[%s] Done: %d yielded, %d inserted, %d skipped",
            self.name,
            report.rows_yielded,
            report.rows_inserted,
            report.rows_skipped,
        )
        return report
