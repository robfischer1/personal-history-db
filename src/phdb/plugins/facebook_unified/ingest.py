"""Facebook Unified ingestion logic.

Ported from ``phdb.adapters.facebook_unified`` as part of Phase 7 of the
phdb Plugin Architecture plan.
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import TYPE_CHECKING

from phdb.formats.chat_upserts import (
    emit_chat_recipient_triples,
    emit_chat_thread_triple,
    upsert_chat_message,
)
from phdb.log import get_logger
from phdb.records import ChatMessage, Reaction, SocialPost
from phdb.triples import get_predicate, resolve_node

if TYPE_CHECKING:
    from phdb.settings import Settings

log = get_logger("phdb.plugins.facebook_unified.ingest")

_POST_TYPE_TO_SCHEMA: dict[str, str] = {
    "status": "SocialMediaPosting",
    "comment": "Comment",
    "group-comment": "Comment",
    "group-post": "SocialMediaPosting",
    "join": "JoinAction",
    "invite": "InviteAction",
    "marketplace": "Conversation",
}

_POST_TYPE_TO_TABLE: dict[str, str] = {
    "status": "social_postings",
    "comment": "comments",
    "group-comment": "comments",
    "group-post": "social_postings",
    "join": "join_actions",
    "invite": "invite_actions",
    "marketplace": "conversations_messages",
}

_BULK_POST_TYPES = {"join", "invite", "marketplace", "reaction"}


def infer_direction(record: ChatMessage, settings: Settings | None) -> str:
    """Infer message direction using identity settings."""
    if not settings or not settings.identity.is_configured:
        return "unknown"

    identity = settings.identity
    if not record.sender_address:
        return "unknown"

    if identity.is_me(record.sender_address):
        if record.recipients and any(identity.is_me(r.address) for r in record.recipients):
            return "self"
        return "outbound"
    return "inbound"


def ingest_facebook_record(
    conn: sqlite3.Connection,
    record: ChatMessage | SocialPost | Reaction,
    source_file_id: int,
    *,
    settings: Settings | None = None,
) -> int | None:
    """Ingest a single Facebook record into its typed table."""
    if isinstance(record, ChatMessage):
        return _ingest_chat_message(conn, record, source_file_id, settings=settings)
    elif isinstance(record, SocialPost):
        return _ingest_social_post(conn, record, source_file_id, settings=settings)
    elif isinstance(record, Reaction):
        return _ingest_reaction(conn, record, source_file_id, settings=settings)
    else:
        log.warning("Unknown record type: %s", type(record))
        return None


def _ingest_chat_message(
    conn: sqlite3.Connection,
    record: ChatMessage,
    source_file_id: int,
    *,
    settings: Settings | None = None,
) -> int | None:
    direction = infer_direction(record, settings)

    message_id = upsert_chat_message(
        conn, source_file_id, record,
        direction=direction, body_text_source="facebook-html"
    )
    if message_id:
        emit_chat_recipient_triples(conn, "facebook", message_id, record)
        if record.thread_key:
            emit_chat_thread_triple(conn, "facebook", message_id, record.thread_key)
        return message_id
    return None


def _ingest_social_post(
    conn: sqlite3.Connection,
    record: SocialPost,
    source_file_id: int,
    *,
    settings: Settings | None = None,
) -> int | None:
    schema_type = _POST_TYPE_TO_SCHEMA.get(record.post_type, "SocialMediaPosting")
    table = _POST_TYPE_TO_TABLE.get(record.post_type, "social_postings")
    
    body = record.body_text or "[empty post]"
    is_bulk = 1 if record.post_type in _BULK_POST_TYPES else 0
    
    sender_addr = "facebook:unknown"
    sender_name = None
    if settings:
        owner_handles = settings.identity.owner_handles.get("facebook", set())
        if owner_handles:
            handle = next(iter(owner_handles))
            sender_addr = f"facebook:{handle}"
        
        if settings.identity.owner_names:
            sender_name = next(iter(settings.identity.owner_names))

    # SocialPost from facebook_html has platform_id set to something like
    # f"facebook-posts:{raw_hash}" or f"facebook:{kind}:{content_hash}"
    
    # We need to map SocialPost to the appropriate table columns.
    # SocialMediaPosting: posting_key, subject, sender_address, sender_name, sender_domain,
    #                     direction, date_posted, body_text, body_text_source, body_text_hash,
    #                     is_bulk, bulk_signal, source_byte_offset, source_byte_length,
    #                     raw_hash, source_file_id, sender_domain
    
    body_hash = hashlib.sha256(body.encode()).hexdigest()
    
    # Most Facebook posts are outbound
    direction = "outbound"
    
    # Map based on table
    if table == "social_postings":
        sql = """INSERT OR IGNORE INTO social_postings (
            schema_type, posting_key, subject, sender_address, sender_name, sender_domain,
            direction, date_posted, body_text, body_text_source, body_text_hash,
            is_bulk, bulk_signal, source_byte_offset, source_byte_length,   
            raw_hash, source_file_id
        ) VALUES ('SocialMediaPosting', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""

        params = (
            record.platform_id, None, sender_addr, sender_name, "facebook",
            direction, record.date_posted or None, body, "facebook-html", body_hash,
            is_bulk, "facebook-post", record.provenance.source_byte_offset,
            record.provenance.source_byte_length, record.provenance.raw_hash, source_file_id
        )
    elif table == "comments":
        sql = """INSERT OR IGNORE INTO comments (
            schema_type, comment_key, subject, sender_address, sender_name,
            direction, date_posted, body_text, body_text_source, body_text_hash,
            is_bulk, bulk_signal, raw_hash, source_file_id
        ) VALUES ('Comment', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        params = (
            record.platform_id, None, sender_addr, sender_name,
            direction, record.date_posted or None, body, "facebook-html", body_hash,
            is_bulk, "facebook-comment", record.provenance.raw_hash, source_file_id
        )
    elif table == "join_actions":
        sql = """INSERT OR IGNORE INTO join_actions (
            schema_type, join_key, subject, sender_address, sender_name,
            direction, date_joined, body_text, body_text_source, body_text_hash,
            is_bulk, bulk_signal, raw_hash, source_file_id
        ) VALUES ('JoinAction', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        params = (
            record.platform_id, None, sender_addr, sender_name,
            direction, record.date_posted or None, body, "facebook-html", body_hash,
            is_bulk, "facebook-join", record.provenance.raw_hash, source_file_id
        )
    elif table == "invite_actions":
        sql = """INSERT OR IGNORE INTO invite_actions (
            schema_type, invite_key, subject, sender_address, sender_name,
            direction, date_invited, body_text, body_text_source, body_text_hash,
            is_bulk, bulk_signal, raw_hash, source_file_id
        ) VALUES ('InviteAction', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        params = (
            record.platform_id, None, sender_addr, sender_name,
            direction, record.date_posted or None, body, "facebook-html", body_hash,
            is_bulk, "facebook-invite", record.provenance.raw_hash, source_file_id
        )
    elif table == "conversations_messages":
        sql = """INSERT OR IGNORE INTO conversations_messages (
            schema_type, conversation_key, subject, sender_address, sender_name, sender_domain,
            direction, date_sent, body_text, body_text_source, body_text_hash,
            is_bulk, raw_hash, source_file_id
        ) VALUES ('Conversation', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
        params = (
            record.platform_id, None, sender_addr, sender_name, "facebook",
            direction, record.date_posted or None, body, "facebook-html", body_hash,
            is_bulk, record.provenance.raw_hash, source_file_id
        )
    else:
        log.warning("Unknown table for post_type %s: %s", record.post_type, table)
        return None

    cur = conn.execute(sql, params)
    if cur.rowcount == 0:
        return None
    
    row_id = int(cur.lastrowid)
    
    # Thread triple
    if record.thread_key:
        _emit_thread_triple(conn, "facebook", table, row_id, record.thread_key)
        
    return row_id


def _ingest_reaction(
    conn: sqlite3.Connection,
    record: Reaction,
    source_file_id: int,
    *,
    settings: Settings | None = None,
) -> int | None:
    body = record.target_summary or record.reaction_type
    sender_addr = "facebook:unknown"
    sender_name = None
    if settings:
        owner_handles = settings.identity.owner_handles.get("facebook", set())
        if owner_handles:
            handle = next(iter(owner_handles))
            sender_addr = f"facebook:{handle}"
        
        if settings.identity.owner_names:
            sender_name = next(iter(settings.identity.owner_names))

    body_hash = hashlib.sha256((body or "").encode()).hexdigest()
    
    # Reactions are outbound
    direction = "outbound"
    
    # LikeAction: like_key, subject, sender_address, sender_name,
    #             direction, date_liked, body_text, body_text_source, body_text_hash,
    #             is_bulk, bulk_signal, raw_hash, source_file_id
    
    # Note: legacy adapter uses raw_hash from provenance.
    # Reaction doesn't have a platform_id, so we synthesize one.
    like_key = f"facebook:reaction:{record.provenance.raw_hash[:16]}"
    
    sql = """INSERT OR IGNORE INTO like_actions (
        schema_type, like_key, subject, sender_address, sender_name,
        direction, date_liked, body_text, body_text_source, body_text_hash,
        is_bulk, bulk_signal, raw_hash, source_file_id
    ) VALUES ('LikeAction', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""
    params = (
        like_key, (record.reaction_type or "like")[:200], sender_addr, sender_name,
        direction, record.date_reacted or None, body, "facebook-html", body_hash,
        1, "facebook-reaction", record.provenance.raw_hash, source_file_id
    )
    
    cur = conn.execute(sql, params)
    if cur.rowcount == 0:
        return None
        
    row_id = int(cur.lastrowid)
    
    # Thread triple for reactions
    _emit_thread_triple(conn, "facebook", "like_actions", row_id, "facebook:reaction")
    
    return row_id


def _emit_thread_triple(
    conn: sqlite3.Connection,
    source_kind: str,
    table: str,
    row_id: int,
    thread_key: str,
) -> None:
    """Emit inThread triple for a record."""
    pred = get_predicate(conn, "inThread")
    if not pred:
        return
    in_thread_id = pred["id"]

    # Record node
    record_label = f"{table}:{row_id}"
    record_node_id = resolve_node(
        conn, record_label, "record",
        source_table=table, source_id=row_id,
    )

    # Thread node
    # thread_key might already contain source_kind prefix, check
    if ":" in thread_key:
        thread_label = thread_key
    else:
        thread_label = f"{source_kind}:{thread_key}"
        
    thread_node_id = resolve_node(conn, thread_label, "thread")

    conn.execute(
        """INSERT OR IGNORE INTO triples
           (subject_node_id, predicate_id, object_node_id, provenance, source_ref)
           VALUES (?, ?, ?, 'plugin', ?)""",
        (record_node_id, in_thread_id, thread_node_id, source_kind),
    )
