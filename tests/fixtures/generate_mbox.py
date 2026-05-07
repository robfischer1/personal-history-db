"""Generate deterministic synthetic mbox test fixtures for the mbox adapter.

Run from the project root:
    uv run python tests/fixtures/generate_mbox.py

Seed: 20240101
Reference time: 2024-01-15T10:00:00+00:00

All addresses, names, subjects, and bodies are synthetic.
No real PII of any kind.
"""

from __future__ import annotations

import base64
import hashlib
import random
import sys
from pathlib import Path

random.seed(20240101)

FIXTURES_DIR = Path(__file__).parent


# ---------------------------------------------------------------------------
# Low-level mbox helpers
# ---------------------------------------------------------------------------

def _asctime(dt_str: str) -> str:
    """Convert a fixed date label to asctime format used in From_ separator lines.

    dt_str is a descriptive label like 'Mon Jan 15 10:00:00 2024'.
    We just pass them through — all dates here are hand-curated.
    """
    return dt_str


def _message_block(
    from_addr: str,
    from_asctime: str,
    headers: list[tuple[str, str]],
    body: str,
) -> str:
    """Assemble one mbox message block (From_ line + headers + blank + body)."""
    lines = [f"From {from_addr} {from_asctime}"]
    for name, value in headers:
        lines.append(f"{name}: {value}")
    lines.append("")  # blank line separating headers from body
    lines.append(body)
    return "\n".join(lines)


def _make_mbox(messages: list[str]) -> str:
    """Join message blocks into a full mbox string."""
    return "\n\n".join(messages) + "\n"


def _base64_encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _multipart_body(
    boundary: str,
    plain_text: str,
    html_text: str,
) -> str:
    """Produce the body string for a multipart/alternative message."""
    lines = [
        f"--{boundary}",
        "Content-Type: text/plain; charset=utf-8",
        "Content-Transfer-Encoding: quoted-printable",
        "",
        plain_text,
        "",
        f"--{boundary}",
        "Content-Type: text/html; charset=utf-8",
        "Content-Transfer-Encoding: quoted-printable",
        "",
        html_text,
        "",
        f"--{boundary}--",
    ]
    return "\n".join(lines)


def _attachment_body(
    outer_boundary: str,
    plain_text: str,
    attachment_filename: str,
    attachment_content_type: str,
    attachment_data: bytes,
) -> str:
    """Produce the body string for a multipart/mixed message with one attachment."""
    encoded = base64.b64encode(attachment_data).decode("ascii")
    # Wrap encoded data at 76 chars per MIME convention
    encoded_wrapped = "\n".join(
        encoded[i : i + 76] for i in range(0, len(encoded), 76)
    )
    lines = [
        f"--{outer_boundary}",
        "Content-Type: text/plain; charset=utf-8",
        "Content-Transfer-Encoding: quoted-printable",
        "",
        plain_text,
        "",
        f"--{outer_boundary}",
        f'Content-Type: {attachment_content_type}; name="{attachment_filename}"',
        "Content-Transfer-Encoding: base64",
        f'Content-Disposition: attachment; filename="{attachment_filename}"',
        "",
        encoded_wrapped,
        "",
        f"--{outer_boundary}--",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# basic.mbox  (3 well-formed messages)
# ---------------------------------------------------------------------------

def _make_basic_mbox() -> str:
    """Three well-formed messages covering core mapping fields."""

    # --- Message 1: plain-text body with one attachment ---
    boundary1 = "=_boundary_msg001_mixed"
    # Minimal synthetic PDF bytes (not a real PDF, just a recognizable stub)
    pdf_stub = b"%PDF-1.4 synthetic fixture attachment for testing only"
    msg1 = _message_block(
        from_addr="alice@example.com",
        from_asctime="Mon Jan 15 10:00:00 2024",
        headers=[
            ("Message-ID", "<msg001@example.com>"),
            ("Date", "Mon, 15 Jan 2024 10:00:00 +0000"),
            ("From", "Alice Smith <alice@example.com>"),
            ("To", "bob@example.com"),
            ("Subject", "Meeting notes"),
            ("MIME-Version", "1.0"),
            ("Content-Type", f"multipart/mixed; boundary=\"{boundary1}\""),
        ],
        body=_attachment_body(
            outer_boundary=boundary1,
            plain_text="Hi Bob,\n\nPlease find the meeting notes attached.\n\nBest,\nAlice",
            attachment_filename="report.pdf",
            attachment_content_type="application/pdf",
            attachment_data=pdf_stub,
        ),
    )

    # --- Message 2: multipart/alternative (plain + HTML), To + Cc, no attachment ---
    boundary2 = "=_boundary_msg002_alt"
    msg2 = _message_block(
        from_addr="bob@example.com",
        from_asctime="Tue Feb 20 19:30:00 2024",
        headers=[
            ("Message-ID", "<msg002@example.com>"),
            ("Date", "Tue, 20 Feb 2024 14:30:00 -0500"),
            ("From", "bob@example.com"),
            ("To", "alice@example.com"),
            ("Cc", "carol@example.com"),
            ("Subject", "Project update"),
            ("MIME-Version", "1.0"),
            ("Content-Type", f"multipart/alternative; boundary=\"{boundary2}\""),
        ],
        body=_multipart_body(
            boundary=boundary2,
            plain_text=(
                "Hi Alice,\n\nThe project is on track. We expect to finish by end of month.\n\n"
                "-- Bob"
            ),
            html_text=(
                "<html><body><p>Hi Alice,</p>"
                "<p>The project is on track. We expect to finish by end of month.</p>"
                "<p>-- Bob</p></body></html>"
            ),
        ),
    )

    # --- Message 3: HTML-only body, Bcc recipient ---
    msg3 = _message_block(
        from_addr="news@company.com",
        from_asctime="Sun Mar 10 08:15:00 2024",
        headers=[
            ("Message-ID", "<msg003@example.com>"),
            ("Date", "Sun, 10 Mar 2024 08:15:00 +0000"),
            ("From", "news@company.com"),
            ("To", "alice@example.com"),
            ("Bcc", "secret@example.com"),
            ("Subject", "Newsletter"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/html; charset=utf-8"),
        ],
        body=(
            "<html><body>"
            "<h1>Company Newsletter</h1>"
            "<p>Welcome to the monthly newsletter. This month we have exciting updates.</p>"
            "<p>Thank you for subscribing.</p>"
            "</body></html>"
        ),
    )

    return _make_mbox([msg1, msg2, msg3])


# ---------------------------------------------------------------------------
# bulk.mbox  (6 messages — one per bulk detection signal)
# ---------------------------------------------------------------------------

LONG_PLAIN_BODY = (
    "This is a synthetic automated notification message. It contains enough text to "
    "exercise the 280-character snippet truncation limit that the mbox adapter applies "
    "to bulk messages. The body continues well beyond the snippet cutoff to ensure the "
    "cap is tested properly. Additional filler text follows to guarantee the character "
    "count exceeds 280. End of filler content."
)

assert len(LONG_PLAIN_BODY) > 280, "bulk body must exceed SNIPPET_LEN=280"


def _make_bulk_mbox() -> str:
    """Six bulk messages — one per detection signal in the legacy six-signal order."""

    # Signal 1: List-Unsubscribe header present
    msg1 = _message_block(
        from_addr="notify@service.example.com",
        from_asctime="Mon Jan 15 11:00:00 2024",
        headers=[
            ("Message-ID", "<bulk001@example.com>"),
            ("Date", "Mon, 15 Jan 2024 11:00:00 +0000"),
            ("From", "Notifications <notify@service.example.com>"),
            ("To", "user@example.com"),
            ("Subject", "Your weekly digest"),
            ("List-Unsubscribe", "<mailto:unsubscribe@service.example.com>"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body=LONG_PLAIN_BODY,
    )

    # Signal 2: List-Id header present
    msg2 = _message_block(
        from_addr="list@mailing.example.com",
        from_asctime="Tue Jan 16 11:00:00 2024",
        headers=[
            ("Message-ID", "<bulk002@example.com>"),
            ("Date", "Tue, 16 Jan 2024 11:00:00 +0000"),
            ("From", "Mailing List <list@mailing.example.com>"),
            ("To", "user@example.com"),
            ("Subject", "List announcement"),
            ("List-Id", "<announcements.mailing.example.com>"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body=LONG_PLAIN_BODY,
    )

    # Signal 3: Precedence: bulk
    msg3 = _message_block(
        from_addr="sender@bulk.example.com",
        from_asctime="Wed Jan 17 11:00:00 2024",
        headers=[
            ("Message-ID", "<bulk003@example.com>"),
            ("Date", "Wed, 17 Jan 2024 11:00:00 +0000"),
            ("From", "Bulk Sender <sender@bulk.example.com>"),
            ("To", "user@example.com"),
            ("Subject", "Bulk mail test"),
            ("Precedence", "bulk"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body=LONG_PLAIN_BODY,
    )

    # Signal 4: Auto-Submitted: auto-replied
    msg4 = _message_block(
        from_addr="autoresponder@company.example.com",
        from_asctime="Thu Jan 18 11:00:00 2024",
        headers=[
            ("Message-ID", "<bulk004@example.com>"),
            ("Date", "Thu, 18 Jan 2024 11:00:00 +0000"),
            ("From", "Auto Responder <autoresponder@company.example.com>"),
            ("To", "user@example.com"),
            ("Subject", "Out of office"),
            ("Auto-Submitted", "auto-replied"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body=LONG_PLAIN_BODY,
    )

    # Signal 5: X-Auto-Response-Suppress header present
    msg5 = _message_block(
        from_addr="system@corp.example.com",
        from_asctime="Fri Jan 19 11:00:00 2024",
        headers=[
            ("Message-ID", "<bulk005@example.com>"),
            ("Date", "Fri, 19 Jan 2024 11:00:00 +0000"),
            ("From", "Corp System <system@corp.example.com>"),
            ("To", "user@example.com"),
            ("Subject", "System notification"),
            ("X-Auto-Response-Suppress", "All"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body=LONG_PLAIN_BODY,
    )

    # Signal 6: Sender local part matches noreply pattern
    msg6 = _message_block(
        from_addr="noreply@example.com",
        from_asctime="Sat Jan 20 11:00:00 2024",
        headers=[
            ("Message-ID", "<bulk006@example.com>"),
            ("Date", "Sat, 20 Jan 2024 11:00:00 +0000"),
            ("From", "noreply@example.com"),
            ("To", "user@example.com"),
            ("Subject", "Automated message from example.com"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body=LONG_PLAIN_BODY,
    )

    return _make_mbox([msg1, msg2, msg3, msg4, msg5, msg6])


# ---------------------------------------------------------------------------
# edge_cases.mbox
# ---------------------------------------------------------------------------

def _make_edge_cases_mbox() -> str:
    """Edge case messages per spec section §edge_cases."""

    # --- Edge 1: No Message-ID header ---
    msg_no_msgid = _message_block(
        from_addr="sender@example.com",
        from_asctime="Mon Jan 15 09:00:00 2024",
        headers=[
            ("Date", "Mon, 15 Jan 2024 09:00:00 +0000"),
            ("From", "Sender One <sender@example.com>"),
            ("To", "receiver@example.com"),
            ("Subject", "Message without Message-ID"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body="This message intentionally has no Message-ID header.",
    )

    # --- Edge 2: RFC 2047 encoded Subject ---
    # "Héllo Wörld" encoded as UTF-8 base64
    encoded_subject = f"=?UTF-8?B?{_base64_encode('Héllo Wörld')}?="
    msg_encoded_subject = _message_block(
        from_addr="encoder@example.com",
        from_asctime="Tue Jan 16 09:00:00 2024",
        headers=[
            ("Message-ID", "<edge002@example.com>"),
            ("Date", "Tue, 16 Jan 2024 09:00:00 +0000"),
            ("From", "Encoder <encoder@example.com>"),
            ("To", "decoder@example.com"),
            ("Subject", encoded_subject),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body="Body of message with RFC 2047 encoded subject.",
    )

    # --- Edge 3: No Date header (date_sent should be None) ---
    msg_no_date = _message_block(
        from_addr="nodater@example.com",
        from_asctime="Wed Jan 17 09:00:00 2024",
        headers=[
            ("Message-ID", "<edge003@example.com>"),
            ("From", "No Date Sender <nodater@example.com>"),
            ("To", "receiver@example.com"),
            ("Subject", "Message without Date header"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body="This message has no Date header. date_sent should be None.",
    )

    # --- Edge 4: Empty body (attachment-only, one PDF, no text parts) ---
    boundary4 = "=_boundary_edge004_empty_body"
    pdf_stub = b"%PDF-1.4 synthetic fixture attachment empty-body test"
    encoded_pdf = base64.b64encode(pdf_stub).decode("ascii")
    encoded_pdf_wrapped = "\n".join(
        encoded_pdf[i : i + 76] for i in range(0, len(encoded_pdf), 76)
    )
    msg_empty_body = _message_block(
        from_addr="attach@example.com",
        from_asctime="Thu Jan 18 09:00:00 2024",
        headers=[
            ("Message-ID", "<edge004@example.com>"),
            ("Date", "Thu, 18 Jan 2024 09:00:00 +0000"),
            ("From", "Attach Sender <attach@example.com>"),
            ("To", "receiver@example.com"),
            ("Subject", "Attachment-only message"),
            ("MIME-Version", "1.0"),
            ("Content-Type", f"multipart/mixed; boundary=\"{boundary4}\""),
        ],
        body="\n".join([
            f"--{boundary4}",
            'Content-Type: application/pdf; name="document.pdf"',
            "Content-Transfer-Encoding: base64",
            'Content-Disposition: attachment; filename="document.pdf"',
            "",
            encoded_pdf_wrapped,
            "",
            f"--{boundary4}--",
        ]),
    )

    # --- Edge 5: Body exceeding 200,000 chars (MAX_BODY_LEN cap test) ---
    # 200,100 chars of filler to ensure the cap is hit
    filler_unit = "Synthetic filler text for MAX_BODY_LEN cap test. "
    large_body = filler_unit * (200100 // len(filler_unit) + 1)
    large_body = large_body[:200100]
    assert len(large_body) > 200000
    msg_large_body = _message_block(
        from_addr="large@example.com",
        from_asctime="Fri Jan 19 09:00:00 2024",
        headers=[
            ("Message-ID", "<edge005@example.com>"),
            ("Date", "Fri, 19 Jan 2024 09:00:00 +0000"),
            ("From", "Large Sender <large@example.com>"),
            ("To", "receiver@example.com"),
            ("Subject", "Very large body message"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body=large_body,
    )

    # --- Edge 6a: First message with duplicate Message-ID ---
    msg_dup_a = _message_block(
        from_addr="dup1@example.com",
        from_asctime="Sat Jan 20 09:00:00 2024",
        headers=[
            ("Message-ID", "<duplicate@example.com>"),
            ("Date", "Sat, 20 Jan 2024 09:00:00 +0000"),
            ("From", "Dup Sender A <dup1@example.com>"),
            ("To", "receiver@example.com"),
            ("Subject", "First duplicate"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body="First message with duplicate Message-ID. This one should insert.",
    )

    # --- Edge 6b: Second message with same duplicate Message-ID (different content) ---
    msg_dup_b = _message_block(
        from_addr="dup2@example.com",
        from_asctime="Sun Jan 21 09:00:00 2024",
        headers=[
            ("Message-ID", "<duplicate@example.com>"),
            ("Date", "Sun, 21 Jan 2024 09:00:00 +0000"),
            ("From", "Dup Sender B <dup2@example.com>"),
            ("To", "receiver@example.com"),
            ("Subject", "Second duplicate — different content, same Message-ID"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body="Second message with the SAME Message-ID. INSERT OR IGNORE must skip this row.",
    )

    return _make_mbox([
        msg_no_msgid,
        msg_encoded_subject,
        msg_no_date,
        msg_empty_body,
        msg_large_body,
        msg_dup_a,
        msg_dup_b,
    ])


# ---------------------------------------------------------------------------
# gmail.mbox  (2 messages — Gmail-specific headers)
# ---------------------------------------------------------------------------

def _make_gmail_mbox() -> str:
    """Two messages — one with Gmail-specific headers, one without."""

    # Message 1: X-GM-THRID + X-Gmail-Labels
    msg_gmail = _message_block(
        from_addr="sender@example.com",
        from_asctime="Mon Jan 15 12:00:00 2024",
        headers=[
            ("Message-ID", "<gmail001@example.com>"),
            ("Date", "Mon, 15 Jan 2024 12:00:00 +0000"),
            ("From", "Sender Name <sender@example.com>"),
            ("To", "owner@example.com"),
            ("Subject", "Gmail message with labels"),
            ("X-GM-THRID", "1234567890"),
            ("X-Gmail-Labels", "Important,Starred,Personal"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body="This message has Gmail-specific headers: X-GM-THRID and X-Gmail-Labels.",
    )

    # Message 2: No Gmail headers (generic mbox message)
    msg_generic = _message_block(
        from_addr="generic@otherprovider.example.com",
        from_asctime="Tue Jan 16 12:00:00 2024",
        headers=[
            ("Message-ID", "<gmail002@example.com>"),
            ("Date", "Tue, 16 Jan 2024 12:00:00 +0000"),
            ("From", "Generic Sender <generic@otherprovider.example.com>"),
            ("To", "owner@example.com"),
            ("Subject", "Generic mbox message without Gmail headers"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body="This is a generic RFC 2822 message with no Gmail-specific headers.",
    )

    return _make_mbox([msg_gmail, msg_generic])


# ---------------------------------------------------------------------------
# direction.mbox  (4 messages — direction inference)
# ---------------------------------------------------------------------------

def _make_direction_mbox() -> str:
    """Four messages for direction inference testing.

    The test owner email is test@example.com.
    Direction is determined by whether sender_address and/or recipients
    match IdentitySettings.owner_emails at ingest time.
    """

    # Message 1: From owner -> outbound (when owner is test@example.com)
    msg_outbound = _message_block(
        from_addr="test@example.com",
        from_asctime="Mon Jan 15 13:00:00 2024",
        headers=[
            ("Message-ID", "<dir001@example.com>"),
            ("Date", "Mon, 15 Jan 2024 13:00:00 +0000"),
            ("From", "Test Owner <test@example.com>"),
            ("To", "someone@other.com"),
            ("Subject", "Outbound message"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body="Sent by the owner. Direction should be outbound when owner=test@example.com.",
    )

    # Message 2: To owner -> inbound (when owner is test@example.com)
    msg_inbound = _message_block(
        from_addr="someone@other.com",
        from_asctime="Tue Jan 16 13:00:00 2024",
        headers=[
            ("Message-ID", "<dir002@example.com>"),
            ("Date", "Tue, 16 Jan 2024 13:00:00 +0000"),
            ("From", "Someone Else <someone@other.com>"),
            ("To", "test@example.com"),
            ("Subject", "Inbound message"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body="Sent to the owner. Direction should be inbound when owner=test@example.com.",
    )

    # Message 3: From owner To owner -> self
    msg_self = _message_block(
        from_addr="test@example.com",
        from_asctime="Wed Jan 17 13:00:00 2024",
        headers=[
            ("Message-ID", "<dir003@example.com>"),
            ("Date", "Wed, 17 Jan 2024 13:00:00 +0000"),
            ("From", "Test Owner <test@example.com>"),
            ("To", "test@example.com"),
            ("Subject", "Self message"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body="Sent from owner to owner. Direction should be self when owner=test@example.com.",
    )

    # Message 4: From stranger To stranger -> unknown
    msg_unknown = _message_block(
        from_addr="stranger@other.com",
        from_asctime="Thu Jan 18 13:00:00 2024",
        headers=[
            ("Message-ID", "<dir004@example.com>"),
            ("Date", "Thu, 18 Jan 2024 13:00:00 +0000"),
            ("From", "Stranger <stranger@other.com>"),
            ("To", "another@other.com"),
            ("Subject", "Unknown direction message"),
            ("MIME-Version", "1.0"),
            ("Content-Type", "text/plain; charset=utf-8"),
        ],
        body=(
            "Neither sender nor recipient is the owner. "
            "Direction should be unknown regardless of identity settings."
        ),
    )

    return _make_mbox([msg_outbound, msg_inbound, msg_self, msg_unknown])


# ---------------------------------------------------------------------------
# expected.json files  (downstream Golden-Diff Validator)
# ---------------------------------------------------------------------------

def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _make_basic_expected() -> list[dict]:
    """Expected rows for basic.mbox (3 messages)."""
    return [
        {
            "schema_type": "EmailMessage",
            "rfc822_message_id": "msg001@example.com",
            "subject": "Meeting notes",
            "sender_address": "alice@example.com",
            "sender_name": "Alice Smith",
            "sender_domain": "example.com",
            "date_sent": "2024-01-15T10:00:00+00:00",
            "is_bulk": 0,
            "bulk_signal": None,
            "is_multipart": 1,
            "has_attachments": 1,
            "attachment_count": 1,
            "body_text_source": "plain",
            "recipients": [
                {"address": "bob@example.com", "name": None, "rtype": "to"}
            ],
            "attachments": [
                {
                    "filename": "report.pdf",
                    "content_type": "application/pdf",
                }
            ],
        },
        {
            "schema_type": "EmailMessage",
            "rfc822_message_id": "msg002@example.com",
            "subject": "Project update",
            "sender_address": "bob@example.com",
            "sender_name": None,
            "sender_domain": "example.com",
            "date_sent": "2024-02-20T14:30:00-05:00",
            "is_bulk": 0,
            "bulk_signal": None,
            "is_multipart": 1,
            "has_attachments": 0,
            "attachment_count": 0,
            "body_text_source": "plain",
            "recipients": [
                {"address": "alice@example.com", "name": None, "rtype": "to"},
                {"address": "carol@example.com", "name": None, "rtype": "cc"},
            ],
        },
        {
            "schema_type": "EmailMessage",
            "rfc822_message_id": "msg003@example.com",
            "subject": "Newsletter",
            "sender_address": "news@company.com",
            "sender_name": None,
            "sender_domain": "company.com",
            "date_sent": "2024-03-10T08:15:00+00:00",
            "is_bulk": 0,
            "bulk_signal": None,
            "is_multipart": 0,
            "has_attachments": 0,
            "attachment_count": 0,
            "body_text_source": "html2text",
            "recipients": [
                {"address": "alice@example.com", "name": None, "rtype": "to"},
                {"address": "secret@example.com", "name": None, "rtype": "bcc"},
            ],
        },
    ]


def _make_bulk_expected() -> list[dict]:
    """Expected rows for bulk.mbox (6 messages).

    All six must have is_bulk=1 and the correct bulk_signal.
    Body text must be truncated to <= 280 chars (SNIPPET_LEN).
    """
    signals = [
        ("bulk001@example.com", "list-unsubscribe"),
        ("bulk002@example.com", "list-id"),
        ("bulk003@example.com", "precedence"),
        ("bulk004@example.com", "auto-submitted"),
        ("bulk005@example.com", "x-auto-response-suppress"),
        ("bulk006@example.com", "noreply-pattern"),
    ]
    rows = []
    for msg_id, signal in signals:
        rows.append({
            "schema_type": "EmailMessage",
            "rfc822_message_id": msg_id,
            "is_bulk": 1,
            "bulk_signal": signal,
            "body_text_length_max": 280,
        })
    return rows


def _make_edge_cases_expected() -> list[dict]:
    """Expected rows for edge_cases.mbox (7 messages).

    Notes:
    - msg_no_msgid  => rfc822_message_id is None
    - msg_encoded_subject => subject is "Héllo Wörld"
    - msg_no_date => date_sent is None
    - msg_empty_body => body_text is None, body_text_source is "empty"
    - msg_large_body => body_text length is exactly 200000 chars (cap applied)
    - msg_dup_a => inserts successfully
    - msg_dup_b => skipped by INSERT OR IGNORE (same rfc822_message_id as msg_dup_a)
    """
    return [
        {
            "rfc822_message_id": None,
            "subject": "Message without Message-ID",
            "is_bulk": 0,
            "note": "NULL Message-ID — partial unique index allows insert",
        },
        {
            "rfc822_message_id": "edge002@example.com",
            "subject": "Héllo Wörld",
            "is_bulk": 0,
            "note": "RFC 2047 encoded Subject decoded correctly",
        },
        {
            "rfc822_message_id": "edge003@example.com",
            "date_sent": None,
            "is_bulk": 0,
            "note": "Missing Date header — date_sent must be None",
        },
        {
            "rfc822_message_id": "edge004@example.com",
            "body_text": None,
            "body_text_source": "empty",
            "has_attachments": 1,
            "attachment_count": 1,
            "note": "Attachment-only message — no text or HTML parts",
        },
        {
            "rfc822_message_id": "edge005@example.com",
            "body_text_length": 200000,
            "note": "Body capped at MAX_BODY_LEN=200000",
        },
        {
            "rfc822_message_id": "duplicate@example.com",
            "subject": "First duplicate",
            "note": "First occurrence — inserts",
        },
        {
            "rfc822_message_id": "duplicate@example.com",
            "subject": "Second duplicate — different content, same Message-ID",
            "note": "Second occurrence — skipped by INSERT OR IGNORE",
            "expected_insert": False,
        },
    ]


def _make_gmail_expected() -> list[dict]:
    """Expected rows for gmail.mbox (2 messages)."""
    return [
        {
            "rfc822_message_id": "gmail001@example.com",
            "gmail_thread_id": "1234567890",
            "gmail_labels": '["Important", "Starred", "Personal"]',
            "note": "Gmail headers parsed — labels stored as JSON array string",
        },
        {
            "rfc822_message_id": "gmail002@example.com",
            "gmail_thread_id": None,
            "gmail_labels": None,
            "note": "No Gmail headers — both fields are None",
        },
    ]


def _make_direction_expected() -> list[dict]:
    """Expected rows for direction.mbox (4 messages).

    Direction values depend on IdentitySettings being populated with
    owner_emails=["test@example.com"] at ingest time.
    """
    return [
        {
            "rfc822_message_id": "dir001@example.com",
            "direction_with_identity": "outbound",
            "direction_without_identity": "unknown",
            "note": "Sender is owner — outbound when identity configured",
        },
        {
            "rfc822_message_id": "dir002@example.com",
            "direction_with_identity": "inbound",
            "direction_without_identity": "unknown",
            "note": "Recipient is owner — inbound when identity configured",
        },
        {
            "rfc822_message_id": "dir003@example.com",
            "direction_with_identity": "self",
            "direction_without_identity": "unknown",
            "note": "Sender and recipient both owner — self when identity configured",
        },
        {
            "rfc822_message_id": "dir004@example.com",
            "direction_with_identity": "unknown",
            "direction_without_identity": "unknown",
            "note": "Neither party is owner — always unknown",
        },
    ]


# ---------------------------------------------------------------------------
# Write all files
# ---------------------------------------------------------------------------

def main() -> None:
    import json

    fixture_sets = [
        ("basic.mbox",          _make_basic_mbox,          "basic.expected.json",          _make_basic_expected),
        ("bulk.mbox",           _make_bulk_mbox,           "bulk.expected.json",           _make_bulk_expected),
        ("edge_cases.mbox",     _make_edge_cases_mbox,     "edge_cases.expected.json",     _make_edge_cases_expected),
        ("gmail.mbox",          _make_gmail_mbox,          "gmail.expected.json",          _make_gmail_expected),
        ("direction.mbox",      _make_direction_mbox,      "direction.expected.json",      _make_direction_expected),
    ]

    for mbox_name, mbox_fn, expected_name, expected_fn in fixture_sets:
        mbox_path = FIXTURES_DIR / mbox_name
        expected_path = FIXTURES_DIR / expected_name

        mbox_content = mbox_fn()
        mbox_path.write_text(mbox_content, encoding="utf-8", newline="\n")
        print(f"Wrote {mbox_path}  ({len(mbox_content):,} bytes)")

        expected_data = expected_fn()
        expected_path.write_text(
            json.dumps(expected_data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print(f"Wrote {expected_path}")

    # README
    readme_path = FIXTURES_DIR / "README.md"
    readme_path.write_text(_make_readme(), encoding="utf-8", newline="\n")
    print(f"Wrote {readme_path}")


def _make_readme() -> str:
    return """\
# Fixtures — mbox

Seed: 20240101
Reference time: 2024-01-15T10:00:00+00:00

All addresses, names, subjects, and bodies are entirely synthetic.
No real PII of any kind is present in these files.

## Generator

Run from the project root:

    uv run python tests/fixtures/generate_mbox.py

Re-running the generator is idempotent and byte-identical (random.seed(20240101) at top).

## Fixtures

### basic.mbox
Three well-formed RFC 2822 messages. Exercises core field mapping:
- Message 1: plain-text body with one PDF attachment (report.pdf), single To recipient,
  From with display name, date 2024-01-15T10:00:00+00:00, Message-ID msg001.
- Message 2: multipart/alternative (text/plain + text/html), To + Cc recipients,
  no attachments, date 2024-02-20T14:30:00-05:00, Message-ID msg002.
- Message 3: HTML-only body (no text/plain part — triggers html2text conversion),
  Bcc recipient, date 2024-03-10T08:15:00+00:00, Message-ID msg003.

### bulk.mbox
Six messages — one per bulk-detection signal in the adapter's six-signal detection order.
Each message body is longer than 280 chars to exercise SNIPPET_LEN truncation.
Signals in order:
1. List-Unsubscribe header present
2. List-Id header present
3. Precedence: bulk
4. Auto-Submitted: auto-replied
5. X-Auto-Response-Suppress: All
6. Sender local part matches noreply pattern (noreply@example.com)

### edge_cases.mbox
Seven messages covering degenerate inputs:
1. No Message-ID header (rfc822_message_id=None, partial index allows insert)
2. RFC 2047 base64-encoded Subject (=?UTF-8?B?...?= decodes to "Héllo Wörld")
3. No Date header (date_sent must be None)
4. Empty body / attachment-only (one PDF, no text parts; body_text_source="empty")
5. Body exceeding 200,000 chars (MAX_BODY_LEN cap — stored body is exactly 200,000 chars)
6. First of two messages with duplicate Message-ID <duplicate@example.com> (inserts)
7. Second of two messages with duplicate Message-ID <duplicate@example.com> (INSERT OR IGNORE skips)

### gmail.mbox
Two messages for Gmail-specific header handling:
1. X-GM-THRID: 1234567890 and X-Gmail-Labels: "Important,Starred,Personal"
   — labels must be stored as JSON array: ["Important", "Starred", "Personal"]
2. Generic mbox message without X-GM-THRID or X-Gmail-Labels
   — both fields must be None

### direction.mbox
Four messages for direction inference. The test owner email is test@example.com.
1. From test@example.com To someone@other.com — outbound (with identity), unknown (without)
2. From someone@other.com To test@example.com — inbound (with identity), unknown (without)
3. From test@example.com To test@example.com — self (with identity), unknown (without)
4. From stranger@other.com To another@other.com — always unknown

## Expected output files

Each .expected.json declares the rows the adapter should produce.
Used downstream by the Golden-Diff Validator.

| File | Messages | Notes |
|:---|:---|:---|
| basic.expected.json | 3 | Full field-by-field expectations |
| bulk.expected.json | 6 | is_bulk=1, correct bulk_signal, body_text_length_max=280 |
| edge_cases.expected.json | 7 | Includes dedup skip expectation for msg 7 |
| gmail.expected.json | 2 | gmail_thread_id, gmail_labels as JSON array |
| direction.expected.json | 4 | direction_with_identity vs direction_without_identity |
"""


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
