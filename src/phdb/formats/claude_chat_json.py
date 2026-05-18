"""Claude Chat JSON format parser — yields AISessionMessage records.

Parses Claude.ai data export files:
    conversations.json    -- array of conversations, each with chat_messages
    memories.json         -- array of `conversations_memory` payloads
    users.json            -- array of {uuid, full_name, email_address, ...}
    projects/{uuid}.json  -- per-project metadata + docs[]

Pure parser: no DB, no identity resolution.  Adapter-layer concerns (owner
sender addresses, schema_type routing to AdapterRow) stay in the adapter.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from phdb.records import AISessionMessage, Provenance

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


def _conv_thread_metadata(conv: dict[str, Any]) -> dict[str, Any]:
    """Build conversation thread metadata dict."""
    return {
        "uuid": conv.get("uuid"),
        "name": conv.get("name"),
        "summary": conv.get("summary"),
        "created_at": conv.get("created_at"),
        "updated_at": conv.get("updated_at"),
        "account_uuid": (conv.get("account") or {}).get("uuid"),
    }


def _project_thread_metadata(proj: dict[str, Any]) -> dict[str, Any]:
    """Build project thread metadata dict."""
    return {
        "uuid": proj.get("uuid"),
        "name": proj.get("name"),
        "description": proj.get("description"),
        "is_private": proj.get("is_private"),
        "is_starter_project": proj.get("is_starter_project"),
        "created_at": proj.get("created_at"),
        "updated_at": proj.get("updated_at"),
    }


def _attachments_for_message(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Map Claude attachments + files arrays into a serializable list."""
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


# Dispatched parsers ----------------------------------------------------------


def _parse_conversations(source_path: Path) -> Iterator[AISessionMessage]:
    """Yield AISessionMessage records from conversations.json."""
    source_str = str(source_path)
    with source_path.open(encoding="utf-8") as fh:
        convos: list[dict[str, Any]] = json.load(fh)

    for conv in convos:
        conv_uuid = conv.get("uuid")
        if not conv_uuid:
            continue
        thread_key = f"{_PLATFORM}-{conv_uuid}"
        thread_meta = _conv_thread_metadata(conv)

        for msg in conv.get("chat_messages") or []:
            msg_uuid = msg.get("uuid")
            if not msg_uuid:
                continue
            sender = msg.get("sender") or "unknown"
            date_sent = msg.get("created_at") or ""
            parent_uuid = msg.get("parent_message_uuid")
            attachments = _attachments_for_message(msg)

            blocks = msg.get("content") or []
            if not blocks:
                top_text = (msg.get("text") or "").strip()
                if not top_text:
                    continue
                blocks = [{"type": "text", "text": top_text}]

            for idx, block in enumerate(blocks):
                kind, role, tool_name, tool_use_id = _kind_role_from_block(block, sender)
                body_text = _text_from_content_block(block)

                raw_hash_seed = f"{_PLATFORM}:{conv_uuid}:{msg_uuid}:{idx}"
                raw_hash = _hash(raw_hash_seed)
                rfc822_id_suffix = f"{_PLATFORM}:{conv_uuid}:{msg_uuid}:{idx}"

                # Pack extra info into payload JSON
                payload_dict: dict[str, Any] = {
                    "schema_type": "Conversation",
                    "sender": sender,
                    "direction": "outbound" if sender == "human" else "inbound",
                    "tool_name": tool_name,
                    "tool_use_id": tool_use_id,
                    "parent_uuid": parent_uuid,
                    "rfc822_id_suffix": rfc822_id_suffix,
                }
                # Attachments only on first block
                if idx == 0 and attachments:
                    payload_dict["attachments"] = attachments
                # For non-message kinds, include the raw block
                if kind != "message":
                    payload_dict["raw_block"] = block

                yield AISessionMessage(
                    provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
                    date_sent=date_sent,
                    kind=kind,
                    role=role,
                    thread_key=thread_key,
                    body_text=body_text,
                    parent_uuid=parent_uuid,
                    tool_name=tool_name,
                    tool_use_id=tool_use_id,
                    payload=json.dumps(payload_dict),
                    thread_metadata=thread_meta,
                )


def _parse_memories(source_path: Path) -> Iterator[AISessionMessage]:
    """Yield AISessionMessage records from memories.json."""
    source_str = str(source_path)
    with source_path.open(encoding="utf-8") as fh:
        memories: list[dict[str, Any]] = json.load(fh)

    for i, mem in enumerate(memories):
        account_uuid = mem.get("account_uuid", "?")
        body = mem.get("conversations_memory") or ""
        raw_hash_seed = f"{_PLATFORM}:memory:{account_uuid}:{i}:{_hash(body)[:16]}"
        raw_hash = _hash(raw_hash_seed)

        payload_dict: dict[str, Any] = {
            "schema_type": "Thing",
            "account_uuid": account_uuid,
            "full_record": mem,
        }

        yield AISessionMessage(
            provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
            date_sent="",
            kind="conversation_memory",
            role="user",
            thread_key="claude-chat:memories",
            body_text=body[:_BODY_MAX],
            payload=json.dumps(payload_dict),
        )


def _parse_users(source_path: Path) -> Iterator[AISessionMessage]:
    """Yield AISessionMessage records from users.json."""
    source_str = str(source_path)
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

        payload_dict: dict[str, Any] = {
            "schema_type": "Person",
            "uuid": uuid,
            "full_name": full_name,
            "email_address": email,
            "full_record": u,
        }

        yield AISessionMessage(
            provenance=Provenance(source_path=source_str, raw_hash=raw_hash),
            date_sent="",
            kind="account_identity",
            role="user",
            thread_key="claude-chat:users",
            body_text=body,
            payload=json.dumps(payload_dict),
        )


def _parse_project(source_path: Path) -> Iterator[AISessionMessage]:
    """Yield AISessionMessage records from projects/{uuid}.json."""
    source_str = str(source_path)
    with source_path.open(encoding="utf-8") as fh:
        proj: dict[str, Any] = json.load(fh)

    proj_uuid = proj.get("uuid")
    if not proj_uuid:
        return

    thread_key = f"{_PLATFORM}-project-{proj_uuid}"
    thread_meta = _project_thread_metadata(proj)

    # Project definition row
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

    payload_def: dict[str, Any] = {
        "schema_type": "CreativeWork",
        "subject": proj_name or None,
        "full_record": {k: v for k, v in proj.items() if k != "docs"},
    }

    yield AISessionMessage(
        provenance=Provenance(source_path=source_str, raw_hash=raw_hash_def),
        date_sent=proj.get("created_at") or "",
        kind="project_definition",
        role="user",
        thread_key=thread_key,
        body_text=body,
        payload=json.dumps(payload_def),
        thread_metadata=thread_meta,
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

        payload_doc: dict[str, Any] = {
            "schema_type": "DigitalDocument",
            "subject": filename or None,
            "uuid": doc_uuid,
            "filename": filename,
            "created_at": doc.get("created_at"),
        }

        yield AISessionMessage(
            provenance=Provenance(source_path=source_str, raw_hash=raw_hash_doc),
            date_sent=doc.get("created_at") or "",
            kind="project_doc",
            role="user",
            thread_key=thread_key,
            body_text=content[:_BODY_MAX],
            payload=json.dumps(payload_doc),
            thread_metadata=thread_meta,
        )


# Public API ------------------------------------------------------------------


def parse(source_path: Path) -> Iterator[AISessionMessage]:
    """Dispatch to the appropriate sub-parser based on filename.

    Recognized patterns:
        conversations.json    -> _parse_conversations
        memories.json         -> _parse_memories
        users.json            -> _parse_users
        projects/*.json       -> _parse_project
    """
    parent = source_path.parent.name
    fname = source_path.name

    if fname == "conversations.json":
        yield from _parse_conversations(source_path)
    elif fname == "memories.json":
        yield from _parse_memories(source_path)
    elif fname == "users.json":
        yield from _parse_users(source_path)
    elif parent == "projects" and source_path.suffix == ".json":
        yield from _parse_project(source_path)
    else:
        # Unrecognized file — yield nothing
        return
