"""Tests for phdb.records — dataclass instantiation, frozen enforcement, parent-id linkage."""

from __future__ import annotations

import pytest

from phdb.records import (
    AISessionMessage,
    Attachment,
    BookmarkEvent,
    CalendarEvent,
    CallRecord,
    ChatMessage,
    Connection,
    ConsumedItem,
    Contact,
    DigitalDocument,
    EmailMessage,
    ExerciseSet,
    GeoTrace,
    HealthObservation,
    MediaPlay,
    Photograph,
    Provenance,
    Reaction,
    Recipient,
    SocialPost,
    Transaction,
    WebActivity,
)


@pytest.fixture
def prov() -> Provenance:
    return Provenance(source_path="/data/test.mbox", raw_hash="abc123def456")


class TestProvenance:
    def test_creation(self) -> None:
        p = Provenance(source_path="/test", raw_hash="hash1")
        assert p.source_path == "/test"
        assert p.raw_hash == "hash1"
        assert p.source_byte_offset is None
        assert p.source_byte_length is None

    def test_with_offsets(self) -> None:
        p = Provenance(source_path="/test", raw_hash="h", source_byte_offset=100, source_byte_length=500)
        assert p.source_byte_offset == 100
        assert p.source_byte_length == 500

    def test_frozen(self) -> None:
        p = Provenance(source_path="/test", raw_hash="h")
        with pytest.raises(AttributeError):
            p.source_path = "/other"  # type: ignore[misc]


class TestEmailMessage:
    def test_required_fields(self, prov: Provenance) -> None:
        e = EmailMessage(
            provenance=prov,
            rfc822_message_id="<msg@example.com>",
            sender_address="alice@example.com",
            date_sent="2026-01-01T00:00:00Z",
        )
        assert e.rfc822_message_id == "<msg@example.com>"
        assert e.is_multipart is False
        assert e.gmail_labels == ()
        assert e.recipients == ()

    def test_with_recipients(self, prov: Provenance) -> None:
        r = Recipient(address="bob@example.com", name="Bob", rtype="to")
        e = EmailMessage(
            provenance=prov,
            rfc822_message_id="<msg2@ex>",
            sender_address="a@b.com",
            date_sent="2026-01-01T00:00:00Z",
            recipients=(r,),
        )
        assert len(e.recipients) == 1
        assert e.recipients[0].address == "bob@example.com"

    def test_frozen(self, prov: Provenance) -> None:
        e = EmailMessage(
            provenance=prov,
            rfc822_message_id="<x>",
            sender_address="a@b.com",
            date_sent="2026-01-01T00:00:00Z",
        )
        with pytest.raises(AttributeError):
            e.subject = "new"  # type: ignore[misc]


class TestChatMessage:
    def test_basic(self, prov: Provenance) -> None:
        m = ChatMessage(
            provenance=prov,
            sender_address="+15551234567",
            date_sent="2026-03-15T10:30:00Z",
            body_text="Hello!",
            thread_key="sms:+15551234567",
        )
        assert m.sender_address == "+15551234567"
        assert m.is_multipart is False
        assert m.attachments == ()


class TestAISessionMessage:
    def test_basic(self, prov: Provenance) -> None:
        a = AISessionMessage(
            provenance=prov,
            date_sent="2026-05-17T12:00:00Z",
            kind="message",
            role="user",
            thread_key="session-abc",
            body_text="What is X?",
        )
        assert a.kind == "message"
        assert a.model is None
        assert a.thread_metadata is None


class TestCallRecord:
    def test_basic(self, prov: Provenance) -> None:
        c = CallRecord(
            provenance=prov,
            caller_address="+15551234567",
            direction="outbound",
            date_start="2026-01-02T14:00:00Z",
            call_type="voice",
            duration_seconds=120,
        )
        assert c.direction == "outbound"
        assert c.duration_seconds == 120


class TestWebActivity:
    def test_search(self, prov: Provenance) -> None:
        w = WebActivity(
            provenance=prov,
            activity_type="search",
            date_performed="2026-01-03T09:00:00Z",
            platform="google",
            query="python dataclasses",
        )
        assert w.query == "python dataclasses"
        assert w.url is None


class TestBookmarkEvent:
    def test_basic(self, prov: Provenance) -> None:
        b = BookmarkEvent(
            provenance=prov,
            url="https://example.com/page?q=1",
            normalized_url="https://example.com/page",
            date_added="2026-02-01T00:00:00Z",
            instrument="raindrop",
            title="Example Page",
            tags=("python", "tutorial"),
        )
        assert b.instrument == "raindrop"
        assert len(b.tags) == 2


class TestCalendarEvent:
    def test_basic(self, prov: Provenance) -> None:
        c = CalendarEvent(
            provenance=prov,
            uid="event-123@google.com",
            date_start="2026-03-01T09:00:00Z",
            summary="Team standup",
            is_all_day=False,
        )
        assert c.uid == "event-123@google.com"
        assert c.attendees == ()


class TestContact:
    def test_basic(self, prov: Provenance) -> None:
        c = Contact(
            provenance=prov,
            full_name="Alice Smith",
            emails=("alice@example.com",),
            phones=("+15551234567",),
        )
        assert c.full_name == "Alice Smith"
        assert len(c.emails) == 1


class TestConnection:
    def test_basic(self, prov: Provenance) -> None:
        c = Connection(
            provenance=prov,
            display_name="John Doe",
            platform="facebook",
            connection_status="active",
            friends_since="2015-06-01",
        )
        assert c.platform == "facebook"


class TestSocialPost:
    def test_basic(self, prov: Provenance) -> None:
        s = SocialPost(
            provenance=prov,
            author_name="<owner-name>",
            date_posted="2020-01-15T12:00:00Z",
            post_type="status",
            body_text="Hello world",
        )
        assert s.post_type == "status"
        assert s.in_reply_to is None


class TestReaction:
    def test_parent_id(self, prov: Provenance) -> None:
        r = Reaction(
            provenance=prov,
            parent_id="parent_hash_abc123",
            reactor_name="Alice",
            reaction_type="like",
            date_reacted="2020-01-15T13:00:00Z",
        )
        assert r.parent_id == "parent_hash_abc123"


class TestHealthObservation:
    def test_basic(self, prov: Provenance) -> None:
        h = HealthObservation(
            provenance=prov,
            observation_type="HeartRate",
            date_start="2026-05-17T08:00:00Z",
            value=72.0,
            unit="count/min",
            source_device="Apple Watch",
        )
        assert h.value == 72.0
        assert h.metadata == ()


class TestExerciseSet:
    def test_with_parent(self, prov: Provenance) -> None:
        e = ExerciseSet(
            provenance=prov,
            exercise_name="Bench Press",
            date_performed="2026-05-17T07:00:00Z",
            parent_id="workout_hash_xyz",
            set_number=1,
            reps=8,
            weight_kg=80.0,
        )
        assert e.parent_id == "workout_hash_xyz"
        assert e.weight_kg == 80.0

    def test_standalone(self, prov: Provenance) -> None:
        e = ExerciseSet(
            provenance=prov,
            exercise_name="Running",
            date_performed="2026-05-17T06:00:00Z",
            duration_seconds=1800,
            distance_meters=5000.0,
        )
        assert e.parent_id is None


class TestGeoTrace:
    def test_visit(self, prov: Provenance) -> None:
        g = GeoTrace(
            provenance=prov,
            trace_type="visit",
            date_start="2026-05-17T12:00:00Z",
            latitude=40.7128,
            longitude=-74.0060,
            place_name="New York",
        )
        assert g.trace_type == "visit"
        assert g.waypoints == ()


class TestMediaPlay:
    def test_basic(self, prov: Provenance) -> None:
        m = MediaPlay(
            provenance=prov,
            media_type="track",
            title="Bohemian Rhapsody",
            date_played="2026-05-17T20:00:00Z",
            platform="spotify",
            artist="Queen",
            duration_ms=354000,
        )
        assert m.is_skipped is False


class TestConsumedItem:
    def test_book(self, prov: Provenance) -> None:
        c = ConsumedItem(
            provenance=prov,
            item_type="book",
            title="Thinking, Fast and Slow",
            platform="goodreads",
            author="Daniel Kahneman",
            isbn="9780374533557",
            rating=4.5,
            shelves=("psychology", "favorites"),
        )
        assert c.isbn == "9780374533557"
        assert len(c.shelves) == 2


class TestTransaction:
    def test_basic(self, prov: Provenance) -> None:
        t = Transaction(
            provenance=prov,
            order_id="112-1234567-8901234",
            date_ordered="2026-04-01T00:00:00Z",
            platform="amazon",
            total_amount=49.99,
            currency="USD",
        )
        assert t.order_id == "112-1234567-8901234"


class TestDigitalDocument:
    def test_basic(self, prov: Provenance) -> None:
        d = DigitalDocument(
            provenance=prov,
            title="Meeting Notes",
            body_text="Discussed Q2 goals...",
            body_text_source="docx",
            file_path="Documents/notes.docx",
            file_size=15000,
        )
        assert d.body_text_source == "docx"


class TestPhotograph:
    def test_basic(self, prov: Provenance) -> None:
        p = Photograph(
            provenance=prov,
            file_path="DCIM/IMG_001.jpg",
            file_name="IMG_001.jpg",
            date_taken="2026-05-01T14:30:00Z",
            width=4032,
            height=3024,
            latitude=40.7128,
            longitude=-74.006,
        )
        assert p.width == 4032


class TestAttachment:
    def test_basic(self, prov: Provenance) -> None:
        a = Attachment(
            provenance=prov,
            parent_id="email_hash_123",
            filename="report.pdf",
            content_type="application/pdf",
            size_bytes=102400,
        )
        assert a.parent_id == "email_hash_123"
        assert a.filename == "report.pdf"
