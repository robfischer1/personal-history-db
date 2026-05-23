"""Claude Chat plugin — port of the legacy claude_chat adapter.

Ingests Claude.ai data exports: conversations, memories, users, projects.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from phdb.core.plugin import PhdbSourcePlugin
from phdb.formats.claude_chat_json import parse as parse_claude_json
from phdb.log import get_logger
from phdb.records import AISessionMessage
from phdb.triples import resolve_node, get_predicate

if TYPE_CHECKING:
    from phdb.settings import Settings
    from phdb.core.plugin.manifest import PluginManifest

log = get_logger("phdb.plugins.claude_chat")

_PLATFORM = "claude-chat"

@dataclass
class IngestSummary:
    """Result of one ``run()`` call — mirrors the legacy IngestReport surface."""
    source_path: str
    source_file_id: int = 0
    rows_yielded: int = 0
    rows_inserted: int = 0
    rows_skipped: int = 0
    threads_created: int = 0
    errors: list[str] = field(default_factory=list)

def _register_source_file(
    conn: sqlite3.Connection,
    source_path: Path,
    *,
    source_kind: str = "claude_chat",
    file_kind: str = "json",
) -> int:
    """Insert (or refresh) a source_files row for the given path."""
    cur = conn.execute(
        """INSERT INTO source_files
           (source_path, source_org, file_kind, source_kind, session_uuid, ingested_at)
           VALUES (?, ?, ?, ?, NULL,
                   strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
           ON CONFLICT(source_path) DO UPDATE
             SET ingested_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
           RETURNING id""",
        (str(source_path), None, file_kind, source_kind),
    )
    row = cur.fetchone()
    assert row is not None
    return int(row[0])

class ClaudeChatPlugin(PhdbSourcePlugin):
    """Claude.ai data export plugin."""

    def __init__(self, manifest: PluginManifest) -> None:
        super().__init__(manifest)
        self._predicate_cache: dict[str, int] = {}

    # ----------------------- PhdbSourcePlugin contract ---------------------

    def discover(self, root: Path) -> Iterator[tuple[Path, str]]:
        """Walk a directory; yield (path, source_kind) for every Claude export file."""
        if root.is_file():
            parent = root.parent.name
            fname = root.name
            if fname in ("conversations.json", "memories.json", "users.json") or \
               (parent == "projects" and root.suffix == ".json"):
                yield root, self.name
            return
            
        for fname in ("conversations.json", "memories.json", "users.json"):
            p = root / fname
            if p.exists():
                yield p, self.name
        
        projects_dir = root / "projects"
        if projects_dir.exists() and projects_dir.is_dir():
            for p in projects_dir.glob("*.json"):
                yield p, self.name

    def parse(self, path: Path) -> Iterator[AISessionMessage]:
        """Yield AISessionMessage records from one Claude export file."""
        yield from parse_claude_json(path)

    def ingest_row(
        self,
        conn: sqlite3.Connection,
        record: AISessionMessage,
        *,
        source_file_id: int | None = None,
        owner_addr: str = "claude-chat:owner",
        owner_name: str = "owner",
    ) -> int | None:
        """Persist a single AISessionMessage to its typed table."""
        sf_id = source_file_id if source_file_id is not None else 0
        payload: dict[str, Any] = json.loads(record.payload) if record.payload else {}
        schema_type = payload.get("schema_type", "Conversation")
        kind = record.kind
        role = record.role

        if kind in ("message", "tool_use", "tool_result"):
            return self._ingest_conversation_message(conn, record, payload, sf_id, owner_addr, owner_name)
        elif kind == "conversation_memory":
            return self._ingest_thing(conn, record, payload, sf_id, owner_addr, owner_name)
        elif kind == "account_identity":
            return self._ingest_person(conn, record, payload, sf_id)
        elif kind == "project_definition":
            return self._ingest_creative_work(conn, record, payload, sf_id, owner_addr, owner_name)
        elif kind == "project_doc":
            return self._ingest_digital_document(conn, record, payload, sf_id, owner_addr, owner_name)
        
        log.warning("[%s] unrecognized kind: %s", self.name, kind)
        return None

    def register_cli(self, parser: Any) -> None:
        return None

    def register_tools(self, server: Any) -> None:
        return None

    # ------------------------- Convenience runner --------------------------

    def run(
        self,
        source_path: Path,
        conn: sqlite3.Connection,
        settings: Settings,
    ) -> IngestSummary:
        """End-to-end ingest of one Claude export file."""
        report = IngestSummary(source_path=str(source_path))
        sf_id = _register_source_file(conn, source_path, source_kind=self.name)
        report.source_file_id = sf_id

        # Resolve owner identity
        owner_addr = f"{_PLATFORM}:owner"
        owner_name = "owner"
        if settings.identity.owner_names:
            oname = next(iter(settings.identity.owner_names))
            owner_addr = f"{_PLATFORM}:{oname.lower()}"
            owner_name = oname

        batch_count = 0
        for record in self.parse(source_path):
            report.rows_yielded += 1
            
            row_id = self.ingest_row(
                conn, record, source_file_id=sf_id,
                owner_addr=owner_addr, owner_name=owner_name
            )
            
            if row_id is None:
                report.rows_skipped += 1
                continue

            report.rows_inserted += 1

            # Handle Threading (only for Conversation, CreativeWork, DigitalDocument)
            if record.thread_key:
                tid, created = self._upsert_thread(conn, record.thread_key, record.thread_metadata)
                
                # Determine source table
                payload = json.loads(record.payload) if record.payload else {}
                schema_type = payload.get("schema_type", "Conversation")
                source_table = self._source_table_for_schema(schema_type)
                
                self._link_message_thread(conn, source_table, row_id, tid)
                if created:
                    report.threads_created += 1

            batch_count += 1
            if batch_count >= 500: # BATCH_SIZE
                conn.commit()
                batch_count = 0

        conn.commit()
        
        # Finalize message count
        conn.execute(
            "UPDATE source_files SET message_count = ? WHERE id = ?",
            (report.rows_inserted, sf_id),
        )
        conn.commit()
        
        return report

    # --------------------------- Ingest Helpers ---------------------------

    def _source_table_for_schema(self, schema_type: str) -> str:
        mapping = {
            "Conversation": "conversations_messages",
            "Thing": "things",
            "Person": "persons",
            "CreativeWork": "creative_works",
            "DigitalDocument": "digital_documents",
        }
        return mapping.get(schema_type, "conversations_messages")

    def _ingest_conversation_message(
        self, conn: sqlite3.Connection, record: AISessionMessage,
        payload: dict[str, Any], sf_id: int, owner_addr: str, owner_name: str
    ) -> int | None:
        sender = payload.get("sender", "")
        direction = payload.get("direction", "self")
        kind = record.kind
        
        sender_address = owner_addr if sender == "human" else f"{_PLATFORM}:claude"
        sender_name = owner_name if sender == "human" else "Claude"
        rfc822_id = payload.get("rfc822_id_suffix")
        
        # Attachments extracted logic
        attachments = payload.get("attachments") or []

        # Payload for non-message
        adapter_payload: str | None = None
        if kind != "message":
            raw_block = payload.get("raw_block")
            if raw_block:
                adapter_payload = json.dumps(raw_block)

        cur = conn.execute(
            """INSERT OR IGNORE INTO conversations_messages (
                schema_type, conversation_key, sender_address, sender_name,
                direction, date_sent, body_text, body_text_source, body_text_hash,
                is_bulk, bulk_signal, kind, role, parent_uuid,
                tool_name, tool_use_id, model, payload, raw_hash, source_file_id
            ) VALUES ('Conversation', ?, ?, ?, ?, ?, ?, 'claude-chat-json', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id""",
            (
                rfc822_id, sender_address, sender_name, direction, record.date_sent or None,
                record.body_text, hashlib.sha256(record.body_text.encode()).hexdigest() if record.body_text else None,
                0 if kind == "message" else 1, None if kind == "message" else f"non_text:{kind}",
                kind, record.role, record.parent_uuid,
                record.tool_name, record.tool_use_id, record.model,
                adapter_payload, record.provenance.raw_hash, sf_id
            )
        )
        row = cur.fetchone()
        if not row:
            return None
        
        message_id = int(row[0])
        self._insert_attachments(conn, message_id, attachments)
        return message_id

    def _ingest_thing(
        self, conn: sqlite3.Connection, record: AISessionMessage,
        payload: dict[str, Any], sf_id: int, owner_addr: str, owner_name: str
    ) -> int | None:
        cur = conn.execute(
            """INSERT OR IGNORE INTO things (
                schema_type, thing_key, sender_address, sender_name,
                direction, date_recorded, body_text, body_text_source, body_text_hash,
                is_bulk, bulk_signal, raw_hash, source_file_id
            ) VALUES ('Thing', ?, ?, ?, 'outbound', NULL, ?, 'claude-chat-memory-json', ?, 1, 'account_setting', ?, ?)
            RETURNING id""",
            (
                f"{_PLATFORM}:memory:{payload.get('account_uuid', '?')}",
                owner_addr, owner_name, record.body_text,
                hashlib.sha256(record.body_text.encode()).hexdigest() if record.body_text else None,
                record.provenance.raw_hash, sf_id
            )
        )
        row = cur.fetchone()
        return int(row[0]) if row else None

    def _ingest_person(
        self, conn: sqlite3.Connection, record: AISessionMessage,
        payload: dict[str, Any], sf_id: int
    ) -> int | None:
        uuid = payload.get("uuid", "")
        full_name = payload.get("full_name", "")
        cur = conn.execute(
            """INSERT OR IGNORE INTO persons (
                schema_type, person_key, sender_address, sender_name,
                direction, date_recorded, body_text, body_text_source, body_text_hash,
                is_bulk, bulk_signal, raw_hash, source_file_id
            ) VALUES ('Person', ?, ?, ?, 'self', NULL, ?, 'claude-chat-user-json', ?, 1, 'account_identity', ?, ?)
            RETURNING id""",
            (
                f"{_PLATFORM}:user:{uuid}", f"{_PLATFORM}:user:{uuid}", full_name,
                record.body_text, hashlib.sha256(record.body_text.encode()).hexdigest() if record.body_text else None,
                record.provenance.raw_hash, sf_id
            )
        )
        row = cur.fetchone()
        return int(row[0]) if row else None

    def _ingest_creative_work(
        self, conn: sqlite3.Connection, record: AISessionMessage,
        payload: dict[str, Any], sf_id: int, owner_addr: str, owner_name: str
    ) -> int | None:
        subject = payload.get("subject")
        proj_uuid = record.thread_key.replace(f"{_PLATFORM}-project-", "")
        cur = conn.execute(
            """INSERT OR IGNORE INTO creative_works (
                schema_type, work_key, subject, sender_address, sender_name,
                direction, date_created, body_text, body_text_source, body_text_hash,
                is_bulk, bulk_signal, raw_hash, source_file_id
            ) VALUES ('CreativeWork', ?, ?, ?, ?, 'self', ?, ?, 'claude-chat-project-json', ?, 1, 'project_definition', ?, ?)
            RETURNING id""",
            (
                f"{_PLATFORM}:project:{proj_uuid}:def", subject, owner_addr, owner_name,
                record.date_sent or None, record.body_text,
                hashlib.sha256(record.body_text.encode()).hexdigest() if record.body_text else None,
                record.provenance.raw_hash, sf_id
            )
        )
        row = cur.fetchone()
        return int(row[0]) if row else None

    def _ingest_digital_document(
        self, conn: sqlite3.Connection, record: AISessionMessage,
        payload: dict[str, Any], sf_id: int, owner_addr: str, owner_name: str
    ) -> int | None:
        subject = payload.get("subject")
        proj_uuid = record.thread_key.replace(f"{_PLATFORM}-project-", "")
        doc_uuid = payload.get("uuid", "")
        cur = conn.execute(
            """INSERT OR IGNORE INTO digital_documents (
                schema_type, doc_key, subject, sender_address, sender_name,
                direction, date_created, body_text, body_text_source, body_text_hash,
                is_bulk, bulk_signal, raw_hash, source_file_id
            ) VALUES ('DigitalDocument', ?, ?, ?, ?, 'self', ?, ?, 'claude-chat-project-doc', ?, 1, 'project_doc', ?, ?)
            RETURNING id""",
            (
                f"{_PLATFORM}:project:{proj_uuid}:doc:{doc_uuid}", subject, owner_addr, owner_name,
                record.date_sent or None, record.body_text,
                hashlib.sha256(record.body_text.encode()).hexdigest() if record.body_text else None,
                record.provenance.raw_hash, sf_id
            )
        )
        row = cur.fetchone()
        return int(row[0]) if row else None

    def _insert_attachments(
        self, conn: sqlite3.Connection, message_id: int, attachments: list[dict[str, Any]]
    ) -> None:
        for a in attachments:
            conn.execute(
                """INSERT INTO attachments (schema_type, message_id, filename, content_type,
                   content_disposition, size_bytes, on_disk_path, content_hash)
                   VALUES ('DigitalDocument', ?, ?, ?, ?, ?, ?, ?)""",
                (
                    message_id, a.get("filename"), a.get("content_type"), a.get("content_disposition"),
                    a.get("size_bytes"), a.get("on_disk_path"), a.get("content_hash"),
                ),
            )

    def _resolve_predicate_id(self, conn: sqlite3.Connection, name: str) -> int:
        if name in self._predicate_cache:
            return self._predicate_cache[name]
        pred = get_predicate(conn, name)
        if pred is None:
            raise ValueError(f"Predicate {name!r} not found")
        self._predicate_cache[name] = pred["id"]
        return pred["id"]

    def _upsert_thread(
        self, conn: sqlite3.Connection, thread_key: str, metadata: dict[str, Any] | None = None
    ) -> tuple[int, bool]:
        label = f"{_PLATFORM}:{thread_key}" if not thread_key.startswith(_PLATFORM) else thread_key
        # Actually the legacy code used source_kind:thread_key
        # ClaudeChatAdapter.source_kind was "claude-chat"
        # AISessionMessage already has thread_key like "claude-chat-..."
        
        existing = conn.execute(
            "SELECT id FROM nodes WHERE kind = 'thread' AND normalized_label = ?",
            (label.lower(),),
        ).fetchone()
        if existing:
            return int(existing[0]), False

        node_id = resolve_node(conn, label, "thread")
        return int(node_id), True

    def _link_message_thread(
        self, conn: sqlite3.Connection, source_table: str, source_id: int, thread_node_id: int
    ) -> None:
        in_thread_id = self._resolve_predicate_id(conn, "inThread")
        record_label = f"{source_table}:{source_id}"
        record_node_id = resolve_node(
            conn, record_label, "record",
            source_table=source_table, source_id=source_id,
        )

        conn.execute(
            "INSERT OR IGNORE INTO triples"
            " (subject_node_id, predicate_id, object_node_id,"
            "  provenance, source_ref)"
            " VALUES (?, ?, ?, 'plugin', ?)",
            (record_node_id, in_thread_id, thread_node_id, self.name),
        )
