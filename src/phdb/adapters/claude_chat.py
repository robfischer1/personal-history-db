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

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.claude_chat")

_PLATFORM = "claude-chat"
_BODY_MAX = 200_000  # safety cap on per-row body bytes


# Helpers ---------------------------------------------------------------------

def _hash(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


def _text_from_content_block(block: dict[str, Any]) -> str:
    """Best-effort body_text for a content block."""
    btype = block.get("type")
    if btype == "text":
        return (block.get("text") or "")[:_BODY_MAX]
    if btype == "tool_use":
        name = block.get("name", "?")
        msg = block.get("message", "")
        return f"[tool_use: {name}] {msg}".strip()[:_BODY_MAX]
    if btype == "tool_result":
        # `content` is sometimes a list of blocks, sometimes a string
        content = block.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(c.get("text") or "")
                elif isinstance(c, str):
                    parts.append(c)
            return ("\n".join(parts) or block.get("message") or "")[:_BODY_MAX]
        if isinstance(content, str):
            return content[:_BODY_MAX]
        return (block.get("message") or "")[:_BODY_MAX]
    return ""


def _kind_role_from_block(block: dict[str, Any], sender: str) -> tuple[str, str, str | None, str | None]:
    """Return (kind, role, tool_name, tool_use_id) for a content block."""
    btype = block.get("type")
    role = "user" if sender == "human" else "assistant"
    if btype == "text":
        return "message", role, None, None
    if btype == "tool_use":
        return "tool_use", "assistant", block.get("name"), block.get("id")
    if btype == "tool_result":
        # tool_result blocks show up inside assistant turns in Claude's exports
        return "tool_result", "tool", None, block.get("tool_use_id")
    return "message", role, None, None


def _conv_thread_metadata(conv: dict[str, Any]) -> str:
    return json.dumps({
        "uuid": conv.get("uuid"),
        "name": conv.get("name"),
        "summary": conv.get("summary"),
        "created_at": conv.get("created_at"),
        "updated_at": conv.get("updated_at"),
        "account_uuid": (conv.get("account") or {}).get("uuid"),
    })


def _project_thread_metadata(proj: dict[str, Any]) -> str:
    return json.dumps({
        "uuid": proj.get("uuid"),
        "name": proj.get("name"),
        "description": proj.get("description"),
        "is_private": proj.get("is_private"),
        "is_starter_project": proj.get("is_starter_project"),
        "created_at": proj.get("created_at"),
        "updated_at": proj.get("updated_at"),
    })


def _attachments_for_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Map Claude attachments + files arrays into the AdapterRow.attachments shape."""
    out: list[dict[str, Any]] = []
    for a in msg.get("attachments") or []:
        if not isinstance(a, dict):
            continue
        extracted = a.get("extracted_content") or ""
        out.append({
            "filename": a.get("file_name"),
            "content_type": a.get("file_type"),
            "content_disposition": "inline",
            "size_bytes": a.get("file_size"),
            "on_disk_path": None,
            "content_hash": _hash(extracted) if extracted else None,
        })
    for f in msg.get("files") or []:
        if not isinstance(f, dict):
            continue
        out.append({
            "filename": f.get("file_name"),
            "content_type": None,
            "content_disposition": "attachment",
            "size_bytes": None,
            "on_disk_path": None,
            "content_hash": f.get("file_uuid"),
        })
    return out


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
        parent = source_path.parent.name
        fname = source_path.name

        if fname == "conversations.json":
            yield from self._iter_conversations(source_path, owner_addr, owner_name)
        elif fname == "memories.json":
            yield from self._iter_memories(source_path, owner_addr, owner_name)
        elif fname == "users.json":
            yield from self._iter_users(source_path)
        elif parent == "projects" and source_path.suffix == ".json":
            yield from self._iter_project(source_path, owner_addr, owner_name)
        else:
            log.warning("[claude_chat] unrecognized file: %s", source_path)
            return

    # conversations.json ------------------------------------------------------

    def _iter_conversations(
        self,
        source_path: Path,
        owner_addr: str,
        owner_name: str,
    ) -> Iterator[AdapterRow]:
        with source_path.open(encoding="utf-8") as fh:
            convos: list[dict[str, Any]] = json.load(fh)

        for conv in convos:
            conv_uuid = conv.get("uuid")
            if not conv_uuid:
                continue
            thread_key = f"{_PLATFORM}-{conv_uuid}"
            thread_meta_json = _conv_thread_metadata(conv)

            for msg in conv.get("chat_messages") or []:
                msg_uuid = msg.get("uuid")
                if not msg_uuid:
                    continue
                sender = msg.get("sender") or "unknown"
                sender_address = owner_addr if sender == "human" else f"{_PLATFORM}:claude"
                sender_name = owner_name if sender == "human" else "Claude"
                direction = "outbound" if sender == "human" else "inbound"
                date_sent = msg.get("created_at")
                parent_uuid = msg.get("parent_message_uuid")
                attachments = _attachments_for_message(msg)

                blocks = msg.get("content") or []
                if not blocks:
                    # Some messages have no content blocks. Fall back to the
                    # top-level `text` field.
                    top_text = (msg.get("text") or "").strip()
                    if not top_text:
                        continue
                    blocks = [{"type": "text", "text": top_text}]

                for idx, block in enumerate(blocks):
                    kind, role, tool_name, tool_use_id = _kind_role_from_block(block, sender)
                    body_text = _text_from_content_block(block)

                    raw_hash_seed = f"{_PLATFORM}:{conv_uuid}:{msg_uuid}:{idx}"
                    raw_hash = _hash(raw_hash_seed)
                    rfc822_id = f"{_PLATFORM}:{conv_uuid}:{msg_uuid}:{idx}"

                    yield AdapterRow(
                        schema_type=self.schema_type,
                        rfc822_message_id=rfc822_id,
                        sender_address=sender_address,
                        sender_name=sender_name,
                        direction=direction,
                        date_sent=date_sent,
                        body_text=body_text,
                        body_text_source="claude-chat-json",
                        is_bulk=0 if kind == "message" else 1,
                        bulk_signal=None if kind == "message" else f"non_text:{kind}",
                        has_attachments=1 if (idx == 0 and attachments) else 0,
                        attachment_count=len(attachments) if idx == 0 else 0,
                        raw_hash=raw_hash,
                        # AI session fields
                        kind=kind,
                        role=role,
                        parent_uuid=parent_uuid,
                        tool_name=tool_name,
                        tool_use_id=tool_use_id,
                        model=None,  # claude-chat exports don't include the model id
                        payload=json.dumps(block) if kind != "message" else None,
                        # Thread linkage
                        thread_key=thread_key,
                        thread_metadata=thread_meta_json,
                        # Sidecars -- only on the first content block per message
                        attachments=attachments if idx == 0 else [],
                    )

    # memories.json -----------------------------------------------------------

    def _iter_memories(
        self,
        source_path: Path,
        owner_addr: str,
        owner_name: str,
    ) -> Iterator[AdapterRow]:
        with source_path.open(encoding="utf-8") as fh:
            memories: list[dict[str, Any]] = json.load(fh)

        for i, mem in enumerate(memories):
            account_uuid = mem.get("account_uuid", "?")
            body = mem.get("conversations_memory") or ""
            raw_hash_seed = f"{_PLATFORM}:memory:{account_uuid}:{i}:{_hash(body)[:16]}"
            raw_hash = _hash(raw_hash_seed)

            yield AdapterRow(
                schema_type="Thing",
                rfc822_message_id=f"{_PLATFORM}:memory:{account_uuid}:{i}",
                sender_address=owner_addr,
                sender_name=owner_name,
                direction="outbound",
                date_sent=None,
                body_text=body[:_BODY_MAX],
                body_text_source="claude-chat-memory-json",
                is_bulk=1,
                bulk_signal="account_setting",
                raw_hash=raw_hash,
                kind="conversation_memory",
                role="user",
                payload=json.dumps(mem),
                thread_key=None,
            )

    # users.json --------------------------------------------------------------

    def _iter_users(self, source_path: Path) -> Iterator[AdapterRow]:
        with source_path.open(encoding="utf-8") as fh:
            users: list[dict[str, Any]] = json.load(fh)

        for u in users:
            uuid = u.get("uuid")
            if not uuid:
                continue
            full_name = u.get("full_name") or ""
            email = u.get("email_address") or ""
            phone = u.get("verified_phone_number") or ""
            body = json.dumps(
                {"full_name": full_name, "email_address": email, "verified_phone_number": phone},
                indent=2,
            )
            raw_hash = _hash(f"{_PLATFORM}:user:{uuid}")

            yield AdapterRow(
                schema_type="Person",
                rfc822_message_id=f"{_PLATFORM}:user:{uuid}",
                sender_address=f"{_PLATFORM}:user:{uuid}",
                sender_name=full_name,
                direction="self",
                date_sent=None,
                body_text=body,
                body_text_source="claude-chat-user-json",
                is_bulk=1,
                bulk_signal="account_identity",
                raw_hash=raw_hash,
                kind="account_identity",
                role="user",
                payload=json.dumps(u),
                thread_key=None,
            )

    # projects/{uuid}.json ----------------------------------------------------

    def _iter_project(
        self,
        source_path: Path,
        owner_addr: str,
        owner_name: str,
    ) -> Iterator[AdapterRow]:
        with source_path.open(encoding="utf-8") as fh:
            proj: dict[str, Any] = json.load(fh)

        proj_uuid = proj.get("uuid")
        if not proj_uuid:
            log.warning("[claude_chat] project missing uuid: %s", source_path)
            return

        thread_key = f"{_PLATFORM}-project-{proj_uuid}"
        thread_meta_json = _project_thread_metadata(proj)

        # Project definition row -- metadata + prompt_template + description
        proj_name = proj.get("name") or ""
        proj_desc = proj.get("description") or ""
        prompt_template = proj.get("prompt_template") or ""
        body_parts: list[str] = []
        if proj_name:
            body_parts.append(f"# {proj_name}")
        if proj_desc:
            body_parts.append(proj_desc)
        if prompt_template:
            body_parts.append("\n## Project prompt\n\n" + prompt_template)
        body = "\n\n".join(body_parts)[:_BODY_MAX]

        raw_hash_def = _hash(f"{_PLATFORM}:project:{proj_uuid}:def")
        yield AdapterRow(
            schema_type="CreativeWork",
            rfc822_message_id=f"{_PLATFORM}:project:{proj_uuid}:def",
            subject=proj_name or None,
            sender_address=owner_addr,
            sender_name=owner_name,
            direction="self",
            date_sent=proj.get("created_at"),
            body_text=body,
            body_text_source="claude-chat-project-json",
            is_bulk=1,
            bulk_signal="project_definition",
            raw_hash=raw_hash_def,
            kind="project_definition",
            role="user",
            payload=json.dumps({k: v for k, v in proj.items() if k != "docs"}),
            thread_key=thread_key,
            thread_metadata=thread_meta_json,
        )

        # Per-doc rows
        for doc in proj.get("docs") or []:
            if not isinstance(doc, dict):
                continue
            doc_uuid = doc.get("uuid")
            if not doc_uuid:
                continue
            filename = doc.get("filename") or ""
            content = doc.get("content") or ""
            raw_hash_doc = _hash(f"{_PLATFORM}:project:{proj_uuid}:doc:{doc_uuid}")
            yield AdapterRow(
                schema_type="DigitalDocument",
                rfc822_message_id=f"{_PLATFORM}:project:{proj_uuid}:doc:{doc_uuid}",
                subject=filename or None,
                sender_address=owner_addr,
                sender_name=owner_name,
                direction="self",
                date_sent=doc.get("created_at"),
                body_text=content[:_BODY_MAX],
                body_text_source="claude-chat-project-doc",
                is_bulk=1,
                bulk_signal="project_doc",
                raw_hash=raw_hash_doc,
                kind="project_doc",
                role="user",
                payload=json.dumps({"uuid": doc_uuid, "filename": filename, "created_at": doc.get("created_at")}),
                thread_key=thread_key,
                thread_metadata=thread_meta_json,
            )
