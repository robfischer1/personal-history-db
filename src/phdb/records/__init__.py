"""phdb.records — Typed record intermediates between format parsers and adapters.

Records are frozen dataclasses representing one atomic unit of personal history.
Parsers yield records; adapters consume them and map to AdapterRow for DB insert.
"""

from phdb.records.ai_session_message import AISessionMessage
from phdb.records.bookmark_event import BookmarkEvent
from phdb.records.calendar_event import CalendarEvent
from phdb.records.call_record import CallRecord
from phdb.records.chat_message import ChatMessage
from phdb.records.common import Attachment, Recipient
from phdb.records.connection import Connection
from phdb.records.consumed_item import ConsumedItem
from phdb.records.contact import Contact
from phdb.records.digital_document import DigitalDocument
from phdb.records.email_message import EmailMessage
from phdb.records.exercise_set import ExerciseSet
from phdb.records.geo_trace import GeoTrace
from phdb.records.health_observation import HealthObservation
from phdb.records.media_play import MediaPlay
from phdb.records.photograph import Photograph
from phdb.records.provenance import Provenance
from phdb.records.reaction import Reaction
from phdb.records.social_post import SocialPost
from phdb.records.transaction import Transaction
from phdb.records.web_activity import WebActivity

__all__ = [
    "AISessionMessage",
    "Attachment",
    "BookmarkEvent",
    "CalendarEvent",
    "CallRecord",
    "ChatMessage",
    "Connection",
    "ConsumedItem",
    "Contact",
    "DigitalDocument",
    "EmailMessage",
    "ExerciseSet",
    "GeoTrace",
    "HealthObservation",
    "MediaPlay",
    "Photograph",
    "Provenance",
    "Reaction",
    "Recipient",
    "SocialPost",
    "Transaction",
    "WebActivity",
]
