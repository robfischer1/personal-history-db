"""Canonical Schema.org-keyed typed-table schemas.

Phase 2 deliverable — exhaustively encodes the 28+ typed tables that
currently live in the live phdb DB. Each schema knows its
``schema_type`` (the Schema.org @type string), its ``table_name`` (the
DB table), its column ``fields``, and its ``indexes``.

The classification (entity vs action) reflects the **current** DB
shape. WebPage is the only entity that has already been factored (per
the 2026-05-22 WebPage Entity Factoring plan); Place/Person/Thing/
Book/Product/GeoShape are still action-shaped in the live DB and will
be entity-factored in Phase 7 as their owning plugins port. This
module encodes the present, not the future; the framework supports
both shapes so Phase 7 can change classifications without touching
the framework.

Authoring style: the messages-decomposition shape gets a small
class-attribute pattern — subclasses set ``key_column`` / ``date_column``
/ extras and the metaclass-free shared assembly puts the canonical
shape together. Special-shape schemas (chat_messages, emails,
conversations_messages, observations, documents, articles, clippings,
photographs, bookmarks, web_pages) are authored explicitly.
"""

from __future__ import annotations

from phdb.schemas.base import (
    ActionSchema,
    EntityFK,
    EntitySchema,
    FieldSpec,
    IndexSpec,
    _body_text_fields,
    _bulk_fields,
    _byte_offset_fields,
    _provenance_fields,
)


# ---------------------------------------------------------------------------
# Helper: the messages-decomposition shape (used by ~16 action schemas)
# ---------------------------------------------------------------------------

def _messages_decomp_fields(
    *,
    schema_type: str,
    key_column: str,
    date_column: str,
    extras: list[FieldSpec] | None = None,
    direction_default: str = "self",
    is_bulk_default: str = "0",
    include_byte_offsets: bool = True,
    include_sender: bool = True,
    include_date_received: bool = False,
) -> list[FieldSpec]:
    """Assemble the canonical messages-decomposition column set.

    Order matters — matches the hand-authored migration DDL so the
    DB_SCHEMA.md diff in Phase 6 stays clean.
    """
    out: list[FieldSpec] = [
        FieldSpec("id", "INTEGER", primary_key=True),
        FieldSpec("schema_type", "TEXT", nullable=False, default=f"'{schema_type}'"),
        FieldSpec(key_column, "TEXT"),
        FieldSpec("subject", "TEXT"),
    ]
    if include_sender:
        out.append(FieldSpec("sender_address", "TEXT"))
        out.append(FieldSpec("sender_name", "TEXT"))
    if extras:
        out.extend(extras)
    out.append(FieldSpec("direction", "TEXT", nullable=False, default=f"'{direction_default}'"))
    out.append(FieldSpec(date_column, "TEXT"))
    if include_date_received:
        out.append(FieldSpec("date_received", "TEXT"))
    out.extend(_body_text_fields())
    out.append(FieldSpec("is_bulk", "INTEGER", nullable=False, default=is_bulk_default))
    out.append(FieldSpec("bulk_signal", "TEXT"))
    if include_byte_offsets:
        out.extend(_byte_offset_fields())
    out.extend(_provenance_fields())
    return out


def _standard_dedup_index(table: str) -> IndexSpec:
    return IndexSpec(
        name=f"idx_{table}_dedup",
        columns=["source_file_id", "raw_hash"],
        unique=True,
    )


def _date_index(table: str, date_column: str) -> IndexSpec:
    return IndexSpec(
        name=f"idx_{table}_date",
        columns=[date_column],
    )


# ---------------------------------------------------------------------------
# Entity schemas — identity-bearing (post-Entity-Factoring)
# ---------------------------------------------------------------------------


class WebPage(EntitySchema):
    """One row per normalized URL — the URL-identity entity (migration 0023)."""

    table_name = "web_pages"
    schema_type = "WebPage"
    dedup_key = "normalized_url"
    coalesce_fields = ["url", "title", "excerpt", "cover_url", "domain",
                       "first_seen", "last_seen", "source_file_id"]
    fields = [
        FieldSpec("id", "INTEGER", primary_key=True),
        FieldSpec("schema_type", "TEXT", nullable=False, default="'WebPage'"),
        FieldSpec("url", "TEXT", nullable=False),
        FieldSpec("normalized_url", "TEXT", nullable=False),
        FieldSpec("title", "TEXT"),
        FieldSpec("excerpt", "TEXT"),
        FieldSpec("cover_url", "TEXT"),
        FieldSpec("domain", "TEXT"),
        FieldSpec("first_seen", "TEXT"),
        FieldSpec("last_seen", "TEXT"),
        FieldSpec("source_file_id", "INTEGER", references="source_files(id)"),
        FieldSpec(
            "created_at", "TEXT", nullable=False,
            default="(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
        ),
    ]
    indexes = [
        IndexSpec(name="idx_web_pages_normalized_url",
                  columns=["normalized_url"], unique=True, if_not_exists=False),
        IndexSpec(name="idx_web_pages_domain",
                  columns=["domain"], if_not_exists=False),
    ]
    description = (
        "URL-identity entity. One row per normalized URL across all "
        "bookmark / history / read-it-later sources. Bookmarks FK into "
        "this table via web_page_id; future BrowseAction / SearchAction / "
        "ReadAction tables also FK here."
    )


# ---------------------------------------------------------------------------
# Action schemas — messages-decomposition shape
# ---------------------------------------------------------------------------


class SearchAction(ActionSchema):
    table_name = "search_actions"
    schema_type = "SearchAction"
    date_column = "date_performed"
    fields = _messages_decomp_fields(
        schema_type="SearchAction",
        key_column="action_key",
        date_column="date_performed",
        extras=[FieldSpec("source_device", "TEXT"), FieldSpec("sender_name", "TEXT")],
        include_sender=False,
        is_bulk_default="1",
    )
    indexes = [
        _standard_dedup_index("search_actions"),
        _date_index("search_actions", "date_performed"),
    ]


class EmailMessage(ActionSchema):
    table_name = "emails"
    schema_type = "EmailMessage"
    date_column = "date_sent"
    fields = [
        FieldSpec("id", "INTEGER", primary_key=True),
        FieldSpec("schema_type", "TEXT", nullable=False, default="'EmailMessage'"),
        FieldSpec("rfc822_message_id", "TEXT"),
        FieldSpec("in_reply_to", "TEXT"),
        FieldSpec("references_chain", "TEXT"),
        FieldSpec("gmail_thread_id", "TEXT"),
        FieldSpec("gmail_labels", "TEXT"),
        FieldSpec("subject", "TEXT"),
        FieldSpec("sender_address", "TEXT"),
        FieldSpec("sender_name", "TEXT"),
        FieldSpec("sender_domain", "TEXT"),
        FieldSpec("direction", "TEXT", nullable=False, default="'unknown'"),
        FieldSpec("date_sent", "TEXT"),
        FieldSpec("date_received", "TEXT"),
        *_body_text_fields(),
        FieldSpec("is_multipart", "INTEGER", nullable=False, default="0"),
        FieldSpec("has_attachments", "INTEGER", nullable=False, default="0"),
        FieldSpec("attachment_count", "INTEGER", nullable=False, default="0"),
        *_bulk_fields(),
        *_byte_offset_fields(),
        *_provenance_fields(),
    ]
    indexes = [
        _standard_dedup_index("emails"),
        _date_index("emails", "date_sent"),
        IndexSpec(name="idx_emails_sender", columns=["sender_address"]),
        IndexSpec(name="idx_emails_thread", columns=["gmail_thread_id"]),
        IndexSpec(name="idx_emails_rfc822", columns=["rfc822_message_id"],
                  where_clause="rfc822_message_id IS NOT NULL"),
    ]


class Message(ActionSchema):
    """The chat/SMS/Discord ``chat_messages`` table — schema_type='Message'."""

    table_name = "chat_messages"
    schema_type = "Message"
    date_column = "date_sent"
    fields = [
        FieldSpec("id", "INTEGER", primary_key=True),
        FieldSpec("schema_type", "TEXT", nullable=False, default="'Message'"),
        FieldSpec("message_key", "TEXT"),
        FieldSpec("subject", "TEXT"),
        FieldSpec("sender_address", "TEXT"),
        FieldSpec("sender_name", "TEXT"),
        FieldSpec("sender_domain", "TEXT"),
        FieldSpec("direction", "TEXT", nullable=False, default="'unknown'"),
        FieldSpec("date_sent", "TEXT"),
        FieldSpec("date_received", "TEXT"),
        *_body_text_fields(),
        FieldSpec("is_multipart", "INTEGER", nullable=False, default="0"),
        FieldSpec("has_attachments", "INTEGER", nullable=False, default="0"),
        FieldSpec("attachment_count", "INTEGER", nullable=False, default="0"),
        *_bulk_fields(),
        *_byte_offset_fields(),
        *_provenance_fields(),
    ]
    indexes = [
        _standard_dedup_index("chat_messages"),
        IndexSpec(name="idx_chat_messages_date", columns=["date_sent"],
                  where_clause="date_sent IS NOT NULL"),
        IndexSpec(name="idx_chat_messages_sender", columns=["sender_address"],
                  where_clause="sender_address IS NOT NULL"),
        IndexSpec(name="idx_chat_messages_direction", columns=["direction"]),
    ]


class Conversation(ActionSchema):
    """AI-session conversation messages — ``conversations_messages`` table."""

    table_name = "conversations_messages"
    schema_type = "Conversation"
    date_column = "date_sent"
    fields = [
        FieldSpec("id", "INTEGER", primary_key=True),
        FieldSpec("schema_type", "TEXT", nullable=False, default="'Conversation'"),
        FieldSpec("conversation_key", "TEXT"),
        FieldSpec("subject", "TEXT"),
        FieldSpec("sender_address", "TEXT"),
        FieldSpec("sender_name", "TEXT"),
        FieldSpec("sender_domain", "TEXT"),
        FieldSpec("direction", "TEXT", nullable=False, default="'unknown'"),
        FieldSpec("date_sent", "TEXT"),
        *_body_text_fields(),
        *_bulk_fields(),
        FieldSpec("kind", "TEXT"),
        FieldSpec("role", "TEXT"),
        FieldSpec("parent_uuid", "TEXT"),
        FieldSpec("tool_name", "TEXT"),
        FieldSpec("tool_use_id", "TEXT"),
        FieldSpec("model", "TEXT"),
        FieldSpec("payload", "TEXT"),
        *_provenance_fields(),
    ]
    indexes = [
        _standard_dedup_index("conversations_messages"),
        IndexSpec(name="idx_conversations_messages_date", columns=["date_sent"]),
        IndexSpec(name="idx_conversations_messages_kind", columns=["kind"]),
        IndexSpec(name="idx_conversations_messages_model", columns=["model"],
                  where_clause="model IS NOT NULL"),
    ]


class Observation(ActionSchema):
    table_name = "observations"
    schema_type = "Observation"
    date_column = "date_observed"
    fields = [
        FieldSpec("id", "INTEGER", primary_key=True),
        FieldSpec("schema_type", "TEXT", nullable=False, default="'Observation'"),
        FieldSpec("observation_key", "TEXT"),
        FieldSpec("type_identifier", "TEXT"),
        FieldSpec("subject", "TEXT"),
        FieldSpec("source_device", "TEXT"),
        FieldSpec("direction", "TEXT", nullable=False, default="'self'"),
        FieldSpec("date_observed", "TEXT"),
        FieldSpec("date_end", "TEXT"),
        *_body_text_fields(),
        FieldSpec("is_bulk", "INTEGER", nullable=False, default="1"),
        FieldSpec("bulk_signal", "TEXT"),
        *_byte_offset_fields(),
        *_provenance_fields(),
    ]
    indexes = [
        _standard_dedup_index("observations"),
        IndexSpec(name="idx_observations_type", columns=["type_identifier"],
                  where_clause="type_identifier IS NOT NULL"),
        IndexSpec(name="idx_observations_date", columns=["date_observed"],
                  where_clause="date_observed IS NOT NULL"),
        IndexSpec(name="idx_observations_source", columns=["source_device"],
                  where_clause="source_device IS NOT NULL"),
    ]


class ExerciseAction(ActionSchema):
    table_name = "exercise_actions"
    schema_type = "ExerciseAction"
    date_column = "date_performed"
    fields = _messages_decomp_fields(
        schema_type="ExerciseAction",
        key_column="exercise_key",
        date_column="date_performed",
        extras=[
            FieldSpec("type_identifier", "TEXT"),
            FieldSpec("source_device", "TEXT"),
            FieldSpec("sender_domain", "TEXT"),
        ],
        include_sender=False,
        is_bulk_default="1",
        include_date_received=False,
    )
    # exercise_actions has date_performed + date_end (NOT date_received)
    # adjust the assembled fields to insert date_end after date_performed
    indexes = [
        _standard_dedup_index("exercise_actions"),
        _date_index("exercise_actions", "date_performed"),
        IndexSpec(name="idx_exercise_actions_type", columns=["type_identifier"]),
    ]


# exercise_actions actually has date_end after date_performed — patch in
# (the assembler doesn't model this; manual override)
def _patch_exercise_actions() -> None:
    new_fields: list[FieldSpec] = []
    for f in ExerciseAction.fields:
        new_fields.append(f)
        if f.name == "date_performed":
            new_fields.append(FieldSpec("date_end", "TEXT"))
    # Re-deduplicate in case date_end already present
    seen: set[str] = set()
    dedup: list[FieldSpec] = []
    for f in new_fields:
        if f.name in seen:
            continue
        seen.add(f.name)
        dedup.append(f)
    ExerciseAction.fields = dedup


_patch_exercise_actions()


class ListenAction(ActionSchema):
    table_name = "listen_actions"
    schema_type = "ListenAction"
    date_column = "date_listened"
    fields = _messages_decomp_fields(
        schema_type="ListenAction",
        key_column="listen_key",
        date_column="date_listened",
        extras=[
            FieldSpec("artist_name", "TEXT"),
            FieldSpec("source_device", "TEXT"),
        ],
        include_sender=False,
        is_bulk_default="1",
    )
    indexes = [
        _standard_dedup_index("listen_actions"),
        _date_index("listen_actions", "date_listened"),
    ]


class WatchAction(ActionSchema):
    table_name = "watch_actions"
    schema_type = "WatchAction"
    date_column = "date_watched"
    fields = _messages_decomp_fields(
        schema_type="WatchAction",
        key_column="watch_key",
        date_column="date_watched",
        extras=[
            FieldSpec("platform_name", "TEXT"),
            FieldSpec("source_device", "TEXT"),
        ],
        include_sender=False,
        is_bulk_default="1",
    )
    indexes = [
        _standard_dedup_index("watch_actions"),
        _date_index("watch_actions", "date_watched"),
    ]


class Action(ActionSchema):
    """Generic catch-all action — ``actions`` table."""

    table_name = "actions"
    schema_type = "Action"
    date_column = "date_performed"
    fields = _messages_decomp_fields(
        schema_type="Action",
        key_column="action_key",
        date_column="date_performed",
        direction_default="unknown",
        include_date_received=True,
    )
    indexes = [
        _standard_dedup_index("actions"),
        _date_index("actions", "date_performed"),
    ]


class Event(ActionSchema):
    table_name = "events"
    schema_type = "Event"
    date_column = "date_occurred"
    fields = _messages_decomp_fields(
        schema_type="Event",
        key_column="event_key",
        date_column="date_occurred",
        direction_default="unknown",
        include_date_received=True,
    )
    indexes = [_standard_dedup_index("events")]


class Product(ActionSchema):
    table_name = "products"
    schema_type = "Product"
    date_column = "date_recorded"
    fields = _messages_decomp_fields(
        schema_type="Product",
        key_column="product_key",
        date_column="date_recorded",
    )
    indexes = [_standard_dedup_index("products")]


class OrderAction(ActionSchema):
    table_name = "order_actions"
    schema_type = "OrderAction"
    date_column = "date_ordered"
    fields = _messages_decomp_fields(
        schema_type="OrderAction",
        key_column="order_key",
        date_column="date_ordered",
    )
    indexes = [_standard_dedup_index("order_actions")]


class LikeAction(ActionSchema):
    table_name = "like_actions"
    schema_type = "LikeAction"
    date_column = "date_liked"
    fields = _messages_decomp_fields(
        schema_type="LikeAction",
        key_column="like_key",
        date_column="date_liked",
    )
    indexes = [_standard_dedup_index("like_actions")]


class Person(ActionSchema):
    """Currently action-shaped; will be entity-factored in Phase 7."""

    table_name = "persons"
    schema_type = "Person"
    date_column = "date_recorded"
    fields = _messages_decomp_fields(
        schema_type="Person",
        key_column="person_key",
        date_column="date_recorded",
    )
    indexes = [_standard_dedup_index("persons")]


class SocialMediaPosting(ActionSchema):
    table_name = "social_postings"
    schema_type = "SocialMediaPosting"
    date_column = "date_posted"
    fields = _messages_decomp_fields(
        schema_type="SocialMediaPosting",
        key_column="posting_key",
        date_column="date_posted",
        direction_default="outbound",
        extras=[FieldSpec("sender_domain", "TEXT")],
    )
    indexes = [_standard_dedup_index("social_postings")]


class Comment(ActionSchema):
    table_name = "comments"
    schema_type = "Comment"
    date_column = "date_posted"
    fields = _messages_decomp_fields(
        schema_type="Comment",
        key_column="comment_key",
        date_column="date_posted",
        direction_default="outbound",
        include_byte_offsets=False,
    )
    indexes = [_standard_dedup_index("comments")]


class Place(ActionSchema):
    """Currently action-shaped; will be entity-factored in Phase 7."""

    table_name = "places"
    schema_type = "Place"
    date_column = "date_recorded"
    fields = _messages_decomp_fields(
        schema_type="Place",
        key_column="place_key",
        date_column="date_recorded",
    )
    indexes = [_standard_dedup_index("places")]


class TravelAction(ActionSchema):
    table_name = "travel_actions"
    schema_type = "TravelAction"
    date_column = "date_traveled"
    fields = _messages_decomp_fields(
        schema_type="TravelAction",
        key_column="travel_key",
        date_column="date_traveled",
    )
    indexes = [_standard_dedup_index("travel_actions")]


class GeoShape(ActionSchema):
    table_name = "geo_shapes"
    schema_type = "GeoShape"
    date_column = "date_recorded"
    fields = _messages_decomp_fields(
        schema_type="GeoShape",
        key_column="geo_key",
        date_column="date_recorded",
        include_byte_offsets=False,
    )
    indexes = [_standard_dedup_index("geo_shapes")]


class Book(ActionSchema):
    """Currently action-shaped; will be entity-factored in Phase 7."""

    table_name = "books"
    schema_type = "Book"
    date_column = "date_recorded"
    fields = _messages_decomp_fields(
        schema_type="Book",
        key_column="book_key",
        date_column="date_recorded",
        include_byte_offsets=False,
    )
    indexes = [_standard_dedup_index("books")]


class MedicalRecord(ActionSchema):
    table_name = "medical_records"
    schema_type = "MedicalRecord"
    date_column = "date_recorded"
    fields = _messages_decomp_fields(
        schema_type="MedicalRecord",
        key_column="record_key",
        date_column="date_recorded",
        include_byte_offsets=False,
    )
    indexes = [_standard_dedup_index("medical_records")]


class Review(ActionSchema):
    table_name = "reviews"
    schema_type = "Review"
    date_column = "date_reviewed"
    fields = _messages_decomp_fields(
        schema_type="Review",
        key_column="review_key",
        date_column="date_reviewed",
        include_byte_offsets=False,
    )
    indexes = [_standard_dedup_index("reviews")]


class InviteAction(ActionSchema):
    table_name = "invite_actions"
    schema_type = "InviteAction"
    date_column = "date_invited"
    fields = _messages_decomp_fields(
        schema_type="InviteAction",
        key_column="invite_key",
        date_column="date_invited",
        direction_default="unknown",
        include_byte_offsets=False,
    )
    indexes = [_standard_dedup_index("invite_actions")]


class CreativeWork(ActionSchema):
    table_name = "creative_works"
    schema_type = "CreativeWork"
    date_column = "date_created"
    fields = _messages_decomp_fields(
        schema_type="CreativeWork",
        key_column="work_key",
        date_column="date_created",
        include_byte_offsets=False,
    )
    indexes = [_standard_dedup_index("creative_works")]


class JoinAction(ActionSchema):
    table_name = "join_actions"
    schema_type = "JoinAction"
    date_column = "date_joined"
    fields = _messages_decomp_fields(
        schema_type="JoinAction",
        key_column="join_key",
        date_column="date_joined",
        include_byte_offsets=False,
    )
    indexes = [_standard_dedup_index("join_actions")]


class DigitalDocumentAction(ActionSchema):
    """The post-decomposition ``digital_documents`` table (action-shaped).

    Note: a separate ``documents`` table with a different shape holds
    file-system-extracted DigitalDocument rows (migration 0008). This
    schema covers the messages-decomposition variant ('DigitalDocument'
    rows that came in via the messages pipeline).
    """

    table_name = "digital_documents"
    schema_type = "DigitalDocument"
    date_column = "date_created"
    fields = _messages_decomp_fields(
        schema_type="DigitalDocument",
        key_column="doc_key",
        date_column="date_created",
        include_byte_offsets=False,
    )
    indexes = [_standard_dedup_index("digital_documents")]


class Thing(ActionSchema):
    """Currently action-shaped; will be entity-factored in Phase 7."""

    table_name = "things"
    schema_type = "Thing"
    date_column = "date_recorded"
    fields = _messages_decomp_fields(
        schema_type="Thing",
        key_column="thing_key",
        date_column="date_recorded",
        include_byte_offsets=False,
    )
    indexes = [_standard_dedup_index("things")]


# ---------------------------------------------------------------------------
# Action schemas — entity-referencing (post-WPEF)
# ---------------------------------------------------------------------------


class BookmarkAction(ActionSchema):
    """Bookmark events FK to web_pages entity (post-WPEF, migration 0023)."""

    table_name = "bookmarks"
    schema_type = "BookmarkAction"
    date_column = "first_seen_in_instrument"
    entity_refs = [EntityFK(entity_table="web_pages", column_name="web_page_id")]
    fields = [
        FieldSpec("id", "INTEGER", primary_key=True),
        FieldSpec("schema_type", "TEXT", nullable=False, default="'BookmarkAction'"),
        FieldSpec("instrument", "TEXT", nullable=False),
        FieldSpec("url", "TEXT", nullable=False),
        FieldSpec("normalized_url", "TEXT", nullable=False),
        FieldSpec("raindrop_id", "TEXT"),
        FieldSpec("title", "TEXT"),
        FieldSpec("note", "TEXT"),
        FieldSpec("excerpt", "TEXT"),
        FieldSpec("cover_url", "TEXT"),
        FieldSpec("folder", "TEXT"),
        FieldSpec("tags", "TEXT"),
        FieldSpec("favorite", "INTEGER", nullable=False, default="0"),
        FieldSpec("highlights", "TEXT"),
        FieldSpec("first_seen_in_instrument", "TEXT"),
        FieldSpec("last_seen_in_instrument", "TEXT"),
        FieldSpec("raindrop_created", "TEXT"),
        FieldSpec("appearance_count", "INTEGER", nullable=False, default="1"),
        FieldSpec("excluded", "INTEGER", nullable=False, default="0"),
        FieldSpec("excluded_reason", "TEXT"),
        FieldSpec("source_file_id", "INTEGER", references="source_files(id)"),
        FieldSpec("raw_hash", "TEXT"),
        FieldSpec(
            "ingested_at", "TEXT", nullable=False,
            default="(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
        ),
        FieldSpec("web_page_id", "INTEGER", references="web_pages(id)"),
    ]
    indexes = [
        IndexSpec(name="idx_bookmarks_url_instrument",
                  columns=["normalized_url", "instrument"], unique=True),
        IndexSpec(name="idx_bookmarks_instrument", columns=["instrument"]),
        IndexSpec(name="idx_bookmarks_normalized_url", columns=["normalized_url"]),
        IndexSpec(name="idx_bookmarks_folder", columns=["folder"]),
        IndexSpec(name="idx_bookmarks_first_seen", columns=["first_seen_in_instrument"]),
        IndexSpec(name="idx_bookmarks_excluded", columns=["excluded"]),
        IndexSpec(name="idx_bookmarks_web_page_id",
                  columns=["web_page_id"], if_not_exists=False),
    ]


class BrowseAction(ActionSchema):
    """Safari/Chrome history visits (post-WPEF, migration 0023)."""

    table_name = "browse_actions"
    schema_type = "BrowseAction"
    date_column = "visit_time"
    entity_refs = [EntityFK(entity_table="web_pages", column_name="web_page_id")]
    fields = [
        FieldSpec("id", "INTEGER", primary_key=True),
        FieldSpec("schema_type", "TEXT", nullable=False, default="'BrowseAction'"),
        FieldSpec("web_page_id", "INTEGER", references="web_pages(id)"),
        FieldSpec("visit_time", "TEXT"),
        FieldSpec("source_device", "TEXT"),
        *_provenance_fields(),
    ]
    indexes = [
        IndexSpec(name="idx_browse_actions_web_page_id", columns=["web_page_id"]),
        IndexSpec(name="idx_browse_actions_visit_time", columns=["visit_time"]),
        _standard_dedup_index("browse_actions"),
    ]


# ---------------------------------------------------------------------------
# Document-shaped schemas — file-like with file_path / mtime / bucket
# ---------------------------------------------------------------------------


class DigitalDocumentFile(ActionSchema):
    """File-system documents — ``documents`` table (migration 0008)."""

    table_name = "documents"
    schema_type = "DigitalDocument"  # same @type as DigitalDocumentAction; routes by table
    date_column = "mtime"
    fields = [
        FieldSpec("id", "INTEGER", primary_key=True),
        FieldSpec("schema_type", "TEXT", nullable=False, default="'DigitalDocument'"),
        FieldSpec("rfc822_message_id", "TEXT"),
        FieldSpec("subject", "TEXT"),
        FieldSpec("file_path", "TEXT"),
        FieldSpec("file_size", "INTEGER"),
        FieldSpec("mtime", "TEXT"),
        FieldSpec("ctime", "TEXT"),
        *_body_text_fields(),
        FieldSpec("raw_hash", "TEXT"),
        FieldSpec("is_bulk", "INTEGER", nullable=False, default="0"),
        FieldSpec("source_file_id", "INTEGER", references="source_files(id)"),
        FieldSpec("bucket", "TEXT"),
        FieldSpec(
            "created_at", "TEXT", nullable=False,
            default="(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
        ),
    ]
    indexes = [
        _standard_dedup_index("documents"),
        IndexSpec(name="idx_documents_path", columns=["file_path"]),
        IndexSpec(name="idx_documents_bucket", columns=["bucket"]),
    ]


class Article(ActionSchema):
    """Saved web articles — ``articles`` table (migration 0013)."""

    table_name = "articles"
    schema_type = "Article"
    date_column = "mtime"
    fields = [
        FieldSpec("id", "INTEGER", primary_key=True),
        FieldSpec("schema_type", "TEXT", nullable=False, default="'Article'"),
        FieldSpec("subject", "TEXT"),
        FieldSpec("url", "TEXT"),
        FieldSpec("publisher", "TEXT"),
        FieldSpec("creator", "TEXT"),
        FieldSpec("description", "TEXT"),
        FieldSpec("image_url", "TEXT"),
        FieldSpec("categories", "TEXT"),
        FieldSpec("tags", "TEXT"),
        FieldSpec("aliases", "TEXT"),
        FieldSpec("note_type", "TEXT"),
        FieldSpec("author_type", "TEXT"),
        FieldSpec("file_path", "TEXT"),
        FieldSpec("file_size", "INTEGER"),
        FieldSpec("ctime", "TEXT"),
        FieldSpec("mtime", "TEXT"),
        *_body_text_fields(),
        FieldSpec("raw_hash", "TEXT"),
        FieldSpec("bucket", "TEXT"),
        FieldSpec("source_file_id", "INTEGER", references="source_files(id)"),
        FieldSpec(
            "created_at", "TEXT", nullable=False,
            default="(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
        ),
    ]
    indexes = [
        _standard_dedup_index("articles"),
        IndexSpec(name="idx_articles_path", columns=["file_path"]),
        IndexSpec(name="idx_articles_url", columns=["url"]),
    ]


class Quotation(ActionSchema):
    """Clippings (Quotation + Reddit Posts) — ``clippings`` table (migration 0017)."""

    table_name = "clippings"
    schema_type = "Quotation"
    date_column = "mtime"
    fields = [
        FieldSpec("id", "INTEGER", primary_key=True),
        FieldSpec("schema_type", "TEXT", nullable=False, default="'Quotation'"),
        FieldSpec("subject", "TEXT"),
        FieldSpec("url", "TEXT"),
        FieldSpec("publisher", "TEXT"),
        FieldSpec("creator", "TEXT"),
        FieldSpec("description", "TEXT"),
        FieldSpec("image_url", "TEXT"),
        FieldSpec("categories", "TEXT"),
        FieldSpec("tags", "TEXT"),
        FieldSpec("aliases", "TEXT"),
        FieldSpec("note_type", "TEXT"),
        FieldSpec("author_type", "TEXT"),
        FieldSpec("file_path", "TEXT"),
        FieldSpec("file_size", "INTEGER"),
        FieldSpec("ctime", "TEXT"),
        FieldSpec("mtime", "TEXT"),
        *_body_text_fields(),
        FieldSpec("raw_hash", "TEXT"),
        FieldSpec("bucket", "TEXT"),
        FieldSpec("source_file_id", "INTEGER", references="source_files(id)"),
        FieldSpec(
            "created_at", "TEXT", nullable=False,
            default="(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
        ),
    ]
    indexes = [
        _standard_dedup_index("clippings"),
        IndexSpec(name="idx_clippings_path", columns=["file_path"]),
        IndexSpec(name="idx_clippings_url", columns=["url"]),
    ]


class Photograph(ActionSchema):
    """Photo metadata — ``photographs`` table (migration 0016)."""

    table_name = "photographs"
    schema_type = "Photograph"
    date_column = "captured_at"
    fields = [
        FieldSpec("id", "INTEGER", primary_key=True),
        FieldSpec("schema_type", "TEXT", nullable=False, default="'Photograph'"),
        FieldSpec("source_path", "TEXT", nullable=False),
        FieldSpec("album_root", "TEXT", nullable=False),
        FieldSpec("content_hash", "TEXT"),
        FieldSpec("captured_at", "TEXT"),
        FieldSpec("digitized_at", "TEXT"),
        FieldSpec("width", "INTEGER"),
        FieldSpec("height", "INTEGER"),
        FieldSpec("format", "TEXT"),
        FieldSpec("file_size", "INTEGER"),
        FieldSpec("camera_make", "TEXT"),
        FieldSpec("camera_model", "TEXT"),
        FieldSpec("lens", "TEXT"),
        FieldSpec("focal_length", "REAL"),
        FieldSpec("aperture", "REAL"),
        FieldSpec("exposure_time", "REAL"),
        FieldSpec("iso", "INTEGER"),
        FieldSpec("latitude", "REAL"),
        FieldSpec("longitude", "REAL"),
        FieldSpec("altitude", "REAL"),
        FieldSpec("rating", "INTEGER"),
        FieldSpec("source_org", "TEXT", nullable=False, default="'digikam'"),
        FieldSpec("source_kind", "TEXT", nullable=False, default="'photo-metadata'"),
        FieldSpec("provenance", "TEXT", nullable=False),
        FieldSpec("raw_hash", "TEXT"),
        FieldSpec("source_file_id", "INTEGER", references="source_files(id)"),
        FieldSpec(
            "created_at", "TEXT", nullable=False,
            default="(strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
        ),
    ]
    indexes = [
        _standard_dedup_index("photographs"),
        IndexSpec(name="idx_photographs_hash", columns=["content_hash"],
                  where_clause="content_hash IS NOT NULL"),
        IndexSpec(name="idx_photographs_captured", columns=["captured_at"],
                  where_clause="captured_at IS NOT NULL"),
        IndexSpec(name="idx_photographs_geo", columns=["latitude", "longitude"],
                  where_clause="latitude IS NOT NULL"),
        IndexSpec(name="idx_photographs_path", columns=["album_root", "source_path"]),
    ]


# ---------------------------------------------------------------------------
# All schemas — order matters for registry walk (entities first, then actions)
# ---------------------------------------------------------------------------


ENTITY_SCHEMAS: list[type[EntitySchema]] = [WebPage]


ACTION_SCHEMAS: list[type[ActionSchema]] = [
    # Communication / messages-decomposition shape
    EmailMessage,
    Message,
    Conversation,
    Observation,
    SearchAction,
    ExerciseAction,
    ListenAction,
    WatchAction,
    Action,
    Event,
    Product,
    OrderAction,
    LikeAction,
    Person,
    SocialMediaPosting,
    Comment,
    Place,
    TravelAction,
    GeoShape,
    Book,
    MedicalRecord,
    Review,
    InviteAction,
    CreativeWork,
    JoinAction,
    DigitalDocumentAction,
    Thing,
    # Entity-FK actions
    BookmarkAction,
    BrowseAction,
    # Document-shaped
    DigitalDocumentFile,
    Article,
    Quotation,
    Photograph,
]


def register_all(registry) -> None:  # type: ignore[no-untyped-def]
    """Register every canonical schema in a SchemaRegistry."""
    for schema in ENTITY_SCHEMAS:
        registry.register(schema)
    for schema in ACTION_SCHEMAS:
        registry.register(schema)


__all__ = [
    "ACTION_SCHEMAS",
    "Action",
    "Article",
    "Book",
    "BookmarkAction",
    "BrowseAction",
    "Comment",
    "Conversation",
    "CreativeWork",
    "DigitalDocumentAction",
    "DigitalDocumentFile",
    "ENTITY_SCHEMAS",
    "EmailMessage",
    "Event",
    "ExerciseAction",
    "GeoShape",
    "InviteAction",
    "JoinAction",
    "LikeAction",
    "MedicalRecord",
    "Message",
    "Observation",
    "OrderAction",
    "Person",
    "Photograph",
    "Place",
    "Product",
    "Quotation",
    "Review",
    "SearchAction",
    "SocialMediaPosting",
    "Thing",
    "TravelAction",
    "WatchAction",
    "WebPage",
    "register_all",
]
