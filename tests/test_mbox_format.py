"""Tests for phdb.formats.mbox — pure format parser yielding EmailMessage records."""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest

from phdb.formats.mbox import parse, stream_raw_messages
from phdb.records import EmailMessage


def _write_mbox(tmp_path: Path, messages: list[str]) -> Path:
    mbox_file = tmp_path / "test.mbox"
    content = ""
    for msg in messages:
        content += dedent(msg).lstrip()
    mbox_file.write_bytes(content.encode())
    return mbox_file


@pytest.fixture
def simple_mbox(tmp_path: Path) -> Path:
    return _write_mbox(tmp_path, [
        """\
        From sender@example.com Mon Jan  1 00:00:00 2024
        Message-ID: <msg001@example.com>
        From: Alice <alice@example.com>
        To: Bob <bob@example.com>
        Subject: Hello World
        Date: Mon, 01 Jan 2024 10:00:00 +0000
        Content-Type: text/plain

        This is the body text.
        """,
    ])


@pytest.fixture
def multi_mbox(tmp_path: Path) -> Path:
    return _write_mbox(tmp_path, [
        """\
        From sender@example.com Mon Jan  1 00:00:00 2024
        Message-ID: <msg001@example.com>
        From: Alice <alice@example.com>
        To: Bob <bob@example.com>
        Date: Mon, 01 Jan 2024 10:00:00 +0000
        Content-Type: text/plain

        First message.
        """,
        """\
        From sender@example.com Tue Jan  2 00:00:00 2024
        Message-ID: <msg002@example.com>
        From: Bob <bob@example.com>
        To: Alice <alice@example.com>
        Date: Tue, 02 Jan 2024 11:00:00 +0000
        Content-Type: text/plain

        Second message.
        """,
    ])


class TestStreamRawMessages:
    def test_single_message(self, simple_mbox: Path) -> None:
        msgs = list(stream_raw_messages(simple_mbox))
        assert len(msgs) == 1
        raw, offset, length = msgs[0]
        assert b"Message-ID: <msg001@example.com>" in raw
        assert offset == 0
        assert length == len(raw)

    def test_multiple_messages(self, multi_mbox: Path) -> None:
        msgs = list(stream_raw_messages(multi_mbox))
        assert len(msgs) == 2
        assert b"msg001" in msgs[0][0]
        assert b"msg002" in msgs[1][0]

    def test_resume_from_offset(self, multi_mbox: Path) -> None:
        all_msgs = list(stream_raw_messages(multi_mbox))
        second_offset = all_msgs[1][1]
        resumed = list(stream_raw_messages(multi_mbox, skip_to_offset=second_offset))
        assert len(resumed) == 1
        assert b"msg002" in resumed[0][0]


class TestParse:
    def test_yields_email_message(self, simple_mbox: Path) -> None:
        records = list(parse(simple_mbox))
        assert len(records) == 1
        assert isinstance(records[0], EmailMessage)

    def test_fields_populated(self, simple_mbox: Path) -> None:
        rec = list(parse(simple_mbox))[0]
        assert rec.rfc822_message_id == "msg001@example.com"
        assert rec.sender_address == "alice@example.com"
        assert rec.sender_name == "Alice"
        assert rec.sender_domain == "example.com"
        assert rec.subject == "Hello World"
        assert "2024-01-01" in rec.date_sent
        assert rec.body_text == "This is the body text."
        assert rec.is_multipart is False
        assert rec.has_attachments is False

    def test_recipients(self, simple_mbox: Path) -> None:
        rec = list(parse(simple_mbox))[0]
        assert len(rec.recipients) == 1
        assert rec.recipients[0].address == "bob@example.com"
        assert rec.recipients[0].name == "Bob"
        assert rec.recipients[0].rtype == "to"

    def test_provenance(self, simple_mbox: Path) -> None:
        rec = list(parse(simple_mbox))[0]
        assert rec.provenance.source_path == str(simple_mbox)
        assert rec.provenance.raw_hash is not None
        assert len(rec.provenance.raw_hash) == 64  # sha256 hex
        assert rec.provenance.source_byte_offset == 0

    def test_multiple_messages(self, multi_mbox: Path) -> None:
        records = list(parse(multi_mbox))
        assert len(records) == 2
        assert records[0].sender_address == "alice@example.com"
        assert records[1].sender_address == "bob@example.com"

    def test_skip_to_offset(self, multi_mbox: Path) -> None:
        all_records = list(parse(multi_mbox))
        second_offset = all_records[1].provenance.source_byte_offset
        resumed = list(parse(multi_mbox, skip_to_offset=second_offset))
        assert len(resumed) == 1
        assert resumed[0].sender_address == "bob@example.com"


class TestBulkDetection:
    def test_list_unsubscribe(self, tmp_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [
            """\
            From noreply@shop.com Mon Jan  1 00:00:00 2024
            Message-ID: <bulk001@shop.com>
            From: Shop <noreply@shop.com>
            To: user@example.com
            Date: Mon, 01 Jan 2024 10:00:00 +0000
            List-Unsubscribe: <http://unsub.example.com>
            Content-Type: text/plain

            Buy our stuff! This is a very long promotional email body that should be truncated to a snippet because it is detected as bulk.
            """,
        ])
        rec = list(parse(mbox))[0]
        assert rec.is_bulk is True
        assert rec.bulk_signal == "List-Unsubscribe"
        assert rec.body_text_source == "plain-snippet"

    def test_noreply_pattern(self, tmp_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [
            """\
            From noreply@service.com Mon Jan  1 00:00:00 2024
            Message-ID: <nr001@service.com>
            From: Service <noreply@service.com>
            To: user@example.com
            Date: Mon, 01 Jan 2024 10:00:00 +0000
            Content-Type: text/plain

            Automated notification.
            """,
        ])
        rec = list(parse(mbox))[0]
        assert rec.is_bulk is True
        assert rec.bulk_signal == "noreply-pattern"

    def test_normal_message_not_bulk(self, simple_mbox: Path) -> None:
        rec = list(parse(simple_mbox))[0]
        assert rec.is_bulk is False
        assert rec.bulk_signal is None


class TestNoMessageId:
    def test_synthetic_id_generated(self, tmp_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [
            """\
            From sender@example.com Mon Jan  1 00:00:00 2024
            From: Alice <alice@example.com>
            To: Bob <bob@example.com>
            Date: Mon, 01 Jan 2024 10:00:00 +0000
            Content-Type: text/plain

            No message-id here.
            """,
        ])
        rec = list(parse(mbox))[0]
        assert rec.rfc822_message_id.startswith("synth:")


class TestGmailHeaders:
    def test_gmail_labels_and_thread(self, tmp_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [
            """\
            From sender@example.com Mon Jan  1 00:00:00 2024
            Message-ID: <gm001@mail.gmail.com>
            From: Alice <alice@gmail.com>
            To: Bob <bob@example.com>
            Date: Mon, 01 Jan 2024 10:00:00 +0000
            X-GM-THRID: 1234567890
            X-Gmail-Labels: Important, Starred, Work
            Content-Type: text/plain

            Gmail message.
            """,
        ])
        rec = list(parse(mbox))[0]
        assert rec.gmail_thread_id == "1234567890"
        assert rec.gmail_labels == ("Important", "Starred", "Work")


class TestReplyChain:
    def test_in_reply_to_and_references(self, tmp_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [
            """\
            From sender@example.com Mon Jan  1 00:00:00 2024
            Message-ID: <reply001@example.com>
            In-Reply-To: <original@example.com>
            References: <original@example.com> <mid@example.com>
            From: Bob <bob@example.com>
            To: Alice <alice@example.com>
            Date: Mon, 01 Jan 2024 12:00:00 +0000
            Content-Type: text/plain

            This is a reply.
            """,
        ])
        rec = list(parse(mbox))[0]
        assert rec.in_reply_to == "original@example.com"
        assert "original@example.com" in rec.references_chain
