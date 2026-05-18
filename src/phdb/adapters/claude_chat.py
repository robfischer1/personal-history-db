"""Claude Chat (claude.ai web/desktop) adapter.

Ingests the contents of a Claude account data export. A single export ships
four file kinds, all dispatched by this adapter:

    conversations.json    -- array of conversations, each with chat_messages
    memories.json         -- array of `conversations_memory` payloads
    users.json            -- array of {uuid, full_name, email_address, ...}
    projects/{uuid}.json  -- per-project metadata + docs[]

One `Conversation` thread per chat conversation (thread_key = conversation UUID).
One `CreativeWork` thread per project (thread_key = `project-{uuid}`).
memories.json and users.json emit standalone rows with no thread linkage.

Message-row strategy mirrors `claude_code`:
    - one row per content block (text / tool_use / tool_result)
    - kind in {message, tool_use, tool_result, conversation_memory,
              account_identity, project_definition, project_doc}
    - tool / non-message rows are flagged `is_bulk=1` to keep search results
      focused on conversational turns by default

Dedup: every row sets `raw_hash` explicitly using stable platform UUIDs
(conversation_uuid + message_uuid + content_index, or per-record UUIDs for
sibling files). Re-ingesting an overlapping takeout is a no-op -- same UUIDs
produce same `raw_hash`, INSERT OR IGNORE skips the duplicate. This is
the canonical recipe for any cloud-takeout adapter.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.formats.claude_chat_json import (
    _hash,  # noqa: F401
    _kind_role_from_block,  # noqa: F401
    _text_from_content_block,  # noqa: F401
    parse,
)
from phdb.log import get_logger

log = get_logger("phdb.adapters.claude_chat")

_PLATFORM = "claude-chat"


# Adapter ---------------------------------------------------------------------

class ClaudeChatAdapter(Adapter):
    """Ingest Claude.ai data exports -- conversations + memories + users + projects."""

    name = "claude_chat"
    source_kind = _PLATFORM
    file_kind = "json"
    schema_type = "Conversation"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        owner_addr, owner_name = self.owner_sender(_PLATFORM)

        for rec in parse(source_path):
            payload: dict[str, Any] = json.loads(rec.payload) if rec.payload else {}
            schema_type = payload.get("schema_type", "Conversation")
            kind = rec.kind
            role = rec.role

            # Resolve sender using identity-aware method
            sender = payload.get("sender", "")
            direction = payload.get("direction", "self")

            if kind in ("message", "tool_use", "tool_result"):
                # Conversation message
                sender_address = owner_addr if sender == "human" else f"{_PLATFORM}:claude"
                sender_name = owner_name if sender == "human" else "Claude"
                attachments = payload.get("attachments") or []
                rfc822_id = payload.get("rfc822_id_suffix")
                parent_uuid = rec.parent_uuid

                # For non-message kinds, reconstruct the raw block payload
                adapter_payload: str | None = None
                if kind != "message":
                    raw_block = payload.get("raw_block")
                    if raw_block:
                        adapter_payload = json.dumps(raw_block)

                thread_metadata_json: str | None = None
                if rec.thread_metadata:
                    thread_metadata_json = json.dumps(rec.thread_metadata)

                yield AdapterRow(
                    schema_type=schema_type,
                    rfc822_message_id=rfc822_id,
                    sender_address=sender_address,
                    sender_name=sender_name,
                    direction=direction,
                    date_sent=rec.date_sent or None,
                    body_text=rec.body_text,
                    body_text_source="claude-chat-json",
                    is_bulk=0 if kind == "message" else 1,
                    bulk_signal=None if kind == "message" else f"non_text:{kind}",
                    has_attachments=1 if attachments else 0,
                    attachment_count=len(attachments),
                    raw_hash=rec.provenance.raw_hash,
                    kind=kind,
                    role=role,
                    parent_uuid=parent_uuid,
                    tool_name=rec.tool_name,
                    tool_use_id=rec.tool_use_id,
                    model=None,
                    payload=adapter_payload,
                    thread_key=rec.thread_key,
                    thread_metadata=thread_metadata_json,
                    attachments=attachments,
                )

            elif kind == "conversation_memory":
                yield AdapterRow(
                    schema_type=schema_type,
                    rfc822_message_id=f"{_PLATFORM}:memory:{payload.get('account_uuid', '?')}",
                    sender_address=owner_addr,
                    sender_name=owner_name,
                    direction="outbound",
                    date_sent=None,
                    body_text=rec.body_text,
                    body_text_source="claude-chat-memory-json",
                    is_bulk=1,
                    bulk_signal="account_setting",
                    raw_hash=rec.provenance.raw_hash,
                    kind=kind,
                    role=role,
                    payload=json.dumps(payload.get("full_record")) if payload.get("full_record") else rec.payload,
                    thread_key=None,
                )

            elif kind == "account_identity":
                uuid = payload.get("uuid", "")
                full_name = payload.get("full_name", "")
                yield AdapterRow(
                    schema_type=schema_type,
                    rfc822_message_id=f"{_PLATFORM}:user:{uuid}",
                    sender_address=f"{_PLATFORM}:user:{uuid}",
                    sender_name=full_name,
                    direction="self",
                    date_sent=None,
                    body_text=rec.body_text,
                    body_text_source="claude-chat-user-json",
                    is_bulk=1,
                    bulk_signal="account_identity",
                    raw_hash=rec.provenance.raw_hash,
                    kind=kind,
                    role=role,
                    payload=json.dumps(payload.get("full_record")) if payload.get("full_record") else rec.payload,
                    thread_key=None,
                )

            elif kind == "project_definition":
                subject = payload.get("subject")
                thread_metadata_json = json.dumps(rec.thread_metadata) if rec.thread_metadata else None

                yield AdapterRow(
                    schema_type=schema_type,
                    rfc822_message_id=f"{_PLATFORM}:project:{rec.thread_key.replace(f'{_PLATFORM}-project-', '')}:def" if rec.thread_key else None,
                    subject=subject,
                    sender_address=owner_addr,
                    sender_name=owner_name,
                    direction="self",
                    date_sent=rec.date_sent or None,
                    body_text=rec.body_text,
                    body_text_source="claude-chat-project-json",
                    is_bulk=1,
                    bulk_signal="project_definition",
                    raw_hash=rec.provenance.raw_hash,
                    kind=kind,
                    role=role,
                    payload=json.dumps(payload.get("full_record")) if payload.get("full_record") else None,
                    thread_key=rec.thread_key,
                    thread_metadata=thread_metadata_json,
                )

            elif kind == "project_doc":
                subject = payload.get("subject")
                thread_metadata_json = json.dumps(rec.thread_metadata) if rec.thread_metadata else None

                yield AdapterRow(
                    schema_type=schema_type,
                    rfc822_message_id=f"{_PLATFORM}:project:{rec.thread_key.replace(f'{_PLATFORM}-project-', '')}:doc:{payload.get('uuid', '')}" if rec.thread_key else None,
                    subject=subject,
                    sender_address=owner_addr,
                    sender_name=owner_name,
                    direction="self",
                    date_sent=rec.date_sent or None,
                    body_text=rec.body_text,
                    body_text_source="claude-chat-project-doc",
                    is_bulk=1,
                    bulk_signal="project_doc",
                    raw_hash=rec.provenance.raw_hash,
                    kind=kind,
                    role=role,
                    payload=json.dumps({"uuid": payload.get("uuid"), "filename": payload.get("filename"), "created_at": payload.get("created_at")}),
                    thread_key=rec.thread_key,
                    thread_metadata=thread_metadata_json,
                )

            else:
                log.warning("[claude_chat] unrecognized kind: %s", kind)
