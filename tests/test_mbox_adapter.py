"""Tests for the mbox adapter."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.mbox import MboxPlugin
from phdb.settings import IdentitySettings, Settings


def _write_mbox(path: Path, messages: list[str]) -> Path:
    """Write a list of raw RFC822 messages into an mbox file."""
    mbox_path = path / "test.mbox"
    parts: list[str] = []
    for msg in messages:
        parts.append(f"From sender@example.com Mon Jan 15 10:00:00 2024\n{msg}")
    mbox_path.write_text("\n".join(parts), encoding="utf-8")
    return mbox_path


BASIC_MSG = """\
Message-ID: <basic001@example.com>
From: Alice <alice@example.com>
To: bob@example.com
Subject: Hello
Date: Mon, 15 Jan 2024 10:00:00 +0000

Hello Bob, this is a test message.
"""

BULK_MSG_LIST_UNSUB = """\
Message-ID: <bulk001@example.com>
From: noreply@store.com
To: user@example.com
Subject: Sale!
Date: Mon, 15 Jan 2024 10:00:00 +0000
List-Unsubscribe: <mailto:unsubscribe@store.com>

This is a bulk marketing email with a long body that should be truncated to snippet length. \
Lorem ipsum dolor sit amet, consectetur adipiscing elit. Sed do eiusmod tempor incididunt ut \
labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris. \
This part should be cut off at the snippet boundary.
"""

HTML_ONLY_MSG = """\
Message-ID: <html001@example.com>
From: webmaster@example.com
To: user@example.com
Subject: HTML Newsletter
Date: Tue, 20 Feb 2024 14:30:00 -0500
MIME-Version: 1.0
Content-Type: text/html; charset=utf-8

<html><body><h1>Big News</h1><p>This is the paragraph content.</p></body></html>
"""

MULTIPART_MSG = """\
Message-ID: <multi001@example.com>
From: sender@example.com
To: receiver@example.com
Subject: Multipart test
Date: Wed, 10 Mar 2024 08:15:00 +0000
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="BOUNDARY123"

--BOUNDARY123
Content-Type: text/plain; charset=utf-8

Plain text part of the message.
--BOUNDARY123
Content-Type: application/pdf; name="report.pdf"
Content-Disposition: attachment; filename="report.pdf"
Content-Transfer-Encoding: base64

dGVzdA==
--BOUNDARY123--
"""

NO_MSGID = """\
From: anon@example.com
To: someone@example.com
Subject: No Message-ID
Date: Thu, 11 Apr 2024 09:00:00 +0000

Message without an ID header.
"""

GMAIL_MSG = """\
Message-ID: <gmail001@example.com>
From: sender@example.com
To: user@example.com
Subject: Gmail thread
Date: Fri, 12 Apr 2024 12:00:00 +0000
X-GM-THRID: 1234567890
X-Gmail-Labels: Important,Starred,Personal

Gmail-specific message.
"""

MSG_WITH_CC_BCC = """\
Message-ID: <ccbcc001@example.com>
From: alice@example.com
To: bob@example.com
Cc: carol@example.com
Bcc: dave@example.com
Subject: CC and BCC test
Date: Sat, 13 Apr 2024 15:00:00 +0000

Testing recipients.
"""


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.db"
    with connect(p, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    return p


class TestBasicIngest:
    def test_single_message_inserts(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [BASIC_MSG])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            report = adapter.run(mbox, conn, settings)

        assert report.rows_yielded == 1
        assert report.rows_inserted == 1
        assert report.rows_skipped == 0

    def test_message_fields_populated(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [BASIC_MSG])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            row = conn.execute(
                "SELECT schema_type, rfc822_message_id, subject, sender_address, "
                "sender_name, sender_domain, date_sent, body_text, body_text_source "
                "FROM emails"
            ).fetchone()

        assert row[0] == "EmailMessage"
        assert row[1] == "basic001@example.com"
        assert row[2] == "Hello"
        assert row[3] == "alice@example.com"
        assert row[4] == "Alice"
        assert row[5] == "example.com"
        assert row[6] is not None
        assert "Hello Bob" in row[7]
        assert row[8] == "plain"

    def test_multiple_messages(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [BASIC_MSG, HTML_ONLY_MSG, GMAIL_MSG])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            report = adapter.run(mbox, conn, settings)

        assert report.rows_yielded == 3
        assert report.rows_inserted == 3


class TestDedup:
    def test_duplicate_message_id_deduped_by_raw_hash(self, tmp_path: Path, db_path: Path) -> None:
        """Emails table dedupes by (source_file_id, raw_hash), not rfc822_message_id.

        Two copies of the same message at different byte offsets in an mbox have
        different raw_hashes, so both insert. True content-identical re-runs of
        the same mbox are caught by the resume-offset logic.
        """
        mbox = _write_mbox(tmp_path, [BASIC_MSG, BASIC_MSG])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            report = adapter.run(mbox, conn, settings)

        # Both inserts succeed — different byte offsets yield different raw_hashes
        assert report.rows_inserted == 2
        assert report.rows_skipped == 0

    def test_null_message_id_always_inserts(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [NO_MSGID, NO_MSGID])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            report = adapter.run(mbox, conn, settings)

        assert report.rows_inserted == 2


class TestBulkDetection:
    def test_list_unsubscribe_detected(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [BULK_MSG_LIST_UNSUB])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            row = conn.execute("SELECT is_bulk, bulk_signal FROM emails").fetchone()

        assert row[0] == 1
        assert row[1] == "List-Unsubscribe"

    def test_bulk_body_truncated_to_snippet(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [BULK_MSG_LIST_UNSUB])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            row = conn.execute("SELECT body_text, body_text_source FROM emails").fetchone()

        assert len(row[0]) <= 280
        assert row[1] == "plain-snippet"

    def test_noreply_pattern_detected(self, tmp_path: Path, db_path: Path) -> None:
        msg = """\
Message-ID: <noreply001@example.com>
From: noreply@service.com
To: user@example.com
Subject: Notification
Date: Mon, 15 Jan 2024 10:00:00 +0000

Automated notification content here.
"""
        mbox = _write_mbox(tmp_path, [msg])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            row = conn.execute("SELECT is_bulk, bulk_signal FROM emails").fetchone()

        assert row[0] == 1
        assert row[1] == "noreply-pattern"

    def test_precedence_bulk_detected(self, tmp_path: Path, db_path: Path) -> None:
        msg = """\
Message-ID: <prec001@example.com>
From: list@example.com
To: user@example.com
Subject: List post
Date: Mon, 15 Jan 2024 10:00:00 +0000
Precedence: bulk

Mailing list message.
"""
        mbox = _write_mbox(tmp_path, [msg])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            row = conn.execute("SELECT is_bulk, bulk_signal FROM emails").fetchone()

        assert row[0] == 1
        assert row[1] == "Precedence:bulk"


class TestBodyExtraction:
    def test_plain_text_body(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [BASIC_MSG])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            row = conn.execute("SELECT body_text, body_text_source FROM emails").fetchone()

        assert "Hello Bob" in row[0]
        assert row[1] == "plain"

    def test_html_only_converted(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [HTML_ONLY_MSG])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            row = conn.execute(
                "SELECT body_text, body_text_source FROM emails"
            ).fetchone()

        assert "Big News" in row[0] or "paragraph content" in row[0]
        # body_html column is not present in the emails typed table
        assert row[1] == "html2text"

    def test_body_max_len_cap(self, tmp_path: Path, db_path: Path) -> None:
        long_body = "A" * 250_000
        msg = f"""\
Message-ID: <long001@example.com>
From: sender@example.com
To: user@example.com
Subject: Long body
Date: Mon, 15 Jan 2024 10:00:00 +0000

{long_body}
"""
        mbox = _write_mbox(tmp_path, [msg])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            row = conn.execute("SELECT body_text FROM emails").fetchone()

        assert len(row[0]) <= 200_000

    def test_empty_body(self, tmp_path: Path, db_path: Path) -> None:
        msg = """\
Message-ID: <empty001@example.com>
From: sender@example.com
To: user@example.com
Subject: Empty body
Date: Mon, 15 Jan 2024 10:00:00 +0000
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="BNDRY"

--BNDRY
Content-Type: application/pdf; name="file.pdf"
Content-Disposition: attachment; filename="file.pdf"
Content-Transfer-Encoding: base64

dGVzdA==
--BNDRY--
"""
        mbox = _write_mbox(tmp_path, [msg])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            row = conn.execute("SELECT body_text, body_text_source FROM emails").fetchone()

        assert row[0] is None
        assert row[1] == "empty"


class TestMultipart:
    def test_multipart_flags(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [MULTIPART_MSG])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            row = conn.execute(
                "SELECT is_multipart, has_attachments, attachment_count FROM emails"
            ).fetchone()

        assert row[0] == 1
        assert row[1] == 1
        assert row[2] == 1

    def test_attachment_metadata_extracted(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [MULTIPART_MSG])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            att = conn.execute(
                "SELECT filename, content_type, size_bytes FROM attachments"
            ).fetchone()

        assert att[0] == "report.pdf"
        assert att[1] == "application/pdf"
        assert att[2] is not None


class TestRecipients:
    def test_to_cc_bcc_extracted(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [MSG_WITH_CC_BCC])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            # Recipients are now emitted as sentTo triples to contact nodes
            sent_to_id = conn.execute(
                "SELECT id FROM predicates WHERE name = 'sentTo'"
            ).fetchone()[0]
            triple_count = conn.execute(
                "SELECT COUNT(*) FROM triples WHERE predicate_id = ?",
                (sent_to_id,),
            ).fetchone()[0]
            contacts = conn.execute(
                "SELECT normalized_label FROM nodes WHERE kind = 'contact'"
            ).fetchall()

        # All recipients (to + cc + bcc) become contact nodes with sentTo triples
        assert triple_count >= 3
        assert len(contacts) >= 3


class TestGmailHeaders:
    def test_gmail_thread_id_extracted(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [GMAIL_MSG])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            row = conn.execute("SELECT gmail_thread_id, gmail_labels FROM emails").fetchone()

        assert row[0] == "1234567890"
        labels = json.loads(row[1])
        assert "Important" in labels
        assert "Starred" in labels
        assert "Personal" in labels


class TestDirectionInference:
    def test_outbound_detected(self, tmp_path: Path, db_path: Path) -> None:
        msg = """\
Message-ID: <dir001@example.com>
From: owner@example.com
To: friend@example.com
Subject: Outbound
Date: Mon, 15 Jan 2024 10:00:00 +0000

Outbound message.
"""
        mbox = _write_mbox(tmp_path, [msg])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)
        settings.identity = IdentitySettings(owner_emails={"owner@example.com"})

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            direction = conn.execute("SELECT direction FROM emails").fetchone()[0]

        assert direction == "outbound"

    def test_inbound_detected(self, tmp_path: Path, db_path: Path) -> None:
        msg = """\
Message-ID: <dir002@example.com>
From: friend@example.com
To: owner@example.com
Subject: Inbound
Date: Mon, 15 Jan 2024 10:00:00 +0000

Inbound message.
"""
        mbox = _write_mbox(tmp_path, [msg])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)
        settings.identity = IdentitySettings(owner_emails={"owner@example.com"})

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            direction = conn.execute("SELECT direction FROM emails").fetchone()[0]

        assert direction == "inbound"

    def test_self_detected(self, tmp_path: Path, db_path: Path) -> None:
        msg = """\
Message-ID: <dir003@example.com>
From: owner@example.com
To: owner@example.com
Subject: Self
Date: Mon, 15 Jan 2024 10:00:00 +0000

Message to self.
"""
        mbox = _write_mbox(tmp_path, [msg])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)
        settings.identity = IdentitySettings(owner_emails={"owner@example.com"})

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            direction = conn.execute("SELECT direction FROM emails").fetchone()[0]

        assert direction == "self"

    def test_unknown_when_no_identity(self, tmp_path: Path, db_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        mbox = _write_mbox(tmp_path, [BASIC_MSG])
        adapter = MboxPlugin()
        monkeypatch.delenv("PHDB_INSTANCE_DIR", raising=False)
        monkeypatch.setattr("phdb.settings._discover_instance_dir", lambda: None)
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            direction = conn.execute("SELECT direction FROM emails").fetchone()[0]

        assert direction == "unknown"


class TestSourceRegistration:
    def test_source_file_registered(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [BASIC_MSG])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            report = adapter.run(mbox, conn, settings)
            row = conn.execute(
                "SELECT source_org, file_kind, source_kind, file_size, message_count "
                "FROM source_files WHERE id = ?",
                (report.source_file_id,),
            ).fetchone()

        assert row[0] == "Google Takeout"
        assert row[1] == "mbox"
        assert row[2] == "gmail"
        assert row[3] is not None
        assert row[4] == 1

    def test_custom_source_kind(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [BASIC_MSG])
        adapter = MboxPlugin(source_kind="thunderbird", source_org="Mozilla Thunderbird")
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            report = adapter.run(mbox, conn, settings)
            row = conn.execute(
                "SELECT source_org, source_kind FROM source_files WHERE id = ?",
                (report.source_file_id,),
            ).fetchone()

        assert row[0] == "Mozilla Thunderbird"
        assert row[1] == "thunderbird"


class TestResume:
    def test_rerun_skips_existing(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [BASIC_MSG, HTML_ONLY_MSG])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            report1 = adapter.run(mbox, conn, settings)

        assert report1.rows_inserted == 2

        adapter2 = MboxPlugin()
        with connect(db_path) as conn:
            report2 = adapter2.run(mbox, conn, settings)

        assert report2.rows_inserted == 0


class TestByteOffsets:
    def test_byte_offsets_recorded(self, tmp_path: Path, db_path: Path) -> None:
        mbox = _write_mbox(tmp_path, [BASIC_MSG])
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            adapter.run(mbox, conn, settings)
            row = conn.execute(
                "SELECT source_byte_offset, source_byte_length, raw_hash FROM emails"
            ).fetchone()

        assert row[0] is not None
        assert row[1] is not None
        assert row[1] > 0
        assert row[2] is not None
        assert len(row[2]) == 64  # SHA-256 hex


class TestErrorResilience:
    def test_malformed_message_skipped(self, tmp_path: Path, db_path: Path) -> None:
        mbox_path = tmp_path / "bad.mbox"
        mbox_path.write_bytes(
            b"From bad@example.com Mon Jan 15 10:00:00 2024\n"
            b"\xff\xfe\x00\x01invalid bytes that won't parse\n"
            b"\n"
            b"From good@example.com Mon Jan 15 10:00:00 2024\n"
            b"Message-ID: <good@example.com>\n"
            b"From: good@example.com\n"
            b"To: user@example.com\n"
            b"Subject: Good\n"
            b"Date: Mon, 15 Jan 2024 10:00:00 +0000\n"
            b"\n"
            b"Good message.\n"
        )
        adapter = MboxPlugin()
        settings = Settings.load(db_path=db_path)

        with connect(db_path) as conn:
            report = adapter.run(mbox_path, conn, settings)

        assert report.rows_inserted >= 1
