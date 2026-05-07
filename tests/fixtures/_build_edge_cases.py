"""Helper: build edge_cases.mbox with the large-body segment embedded.

This is NOT the main generator — it's a one-shot builder used to produce the
static edge_cases.mbox fixture. Run with:

    uv run python tests/fixtures/_build_edge_cases.py
"""
import base64
from pathlib import Path

OUT = Path(__file__).parent / "edge_cases.mbox"


def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


# Large body: 200,100 chars of repeating filler
FILLER_UNIT = "Synthetic filler text for MAX_BODY_LEN cap test. "
large_body = (FILLER_UNIT * (200100 // len(FILLER_UNIT) + 1))[:200100]
assert len(large_body) == 200100

# PDF stub for attachment-only message
pdf_stub = b"%PDF-1.4 synthetic fixture attachment empty-body test"
encoded_pdf = base64.b64encode(pdf_stub).decode("ascii")

# RFC 2047 encoded subject: =?UTF-8?B?...?= for "Héllo Wörld"
encoded_subject = f"=?UTF-8?B?{b64('Héllo Wörld')}?="

parts = []

# Edge 1: No Message-ID
parts.append(
    "From sender@example.com Mon Jan 15 09:00:00 2024\n"
    "Date: Mon, 15 Jan 2024 09:00:00 +0000\n"
    "From: Sender One <sender@example.com>\n"
    "To: receiver@example.com\n"
    "Subject: Message without Message-ID\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=utf-8\n"
    "\n"
    "This message intentionally has no Message-ID header."
)

# Edge 2: RFC 2047 encoded Subject
parts.append(
    "From encoder@example.com Tue Jan 16 09:00:00 2024\n"
    f"Message-ID: <edge002@example.com>\n"
    "Date: Tue, 16 Jan 2024 09:00:00 +0000\n"
    "From: Encoder <encoder@example.com>\n"
    "To: decoder@example.com\n"
    f"Subject: {encoded_subject}\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=utf-8\n"
    "\n"
    "Body of message with RFC 2047 encoded subject."
)

# Edge 3: No Date header
parts.append(
    "From nodater@example.com Wed Jan 17 09:00:00 2024\n"
    "Message-ID: <edge003@example.com>\n"
    "From: No Date Sender <nodater@example.com>\n"
    "To: receiver@example.com\n"
    "Subject: Message without Date header\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=utf-8\n"
    "\n"
    "This message has no Date header. date_sent should be None."
)

# Edge 4: Attachment-only (no text parts)
boundary4 = "=_boundary_edge004_empty_body"
parts.append(
    "From attach@example.com Thu Jan 18 09:00:00 2024\n"
    "Message-ID: <edge004@example.com>\n"
    "Date: Thu, 18 Jan 2024 09:00:00 +0000\n"
    "From: Attach Sender <attach@example.com>\n"
    "To: receiver@example.com\n"
    "Subject: Attachment-only message\n"
    "MIME-Version: 1.0\n"
    f'Content-Type: multipart/mixed; boundary="{boundary4}"\n'
    "\n"
    f"--{boundary4}\n"
    'Content-Type: application/pdf; name="document.pdf"\n'
    "Content-Transfer-Encoding: base64\n"
    'Content-Disposition: attachment; filename="document.pdf"\n'
    "\n"
    f"{encoded_pdf}\n"
    "\n"
    f"--{boundary4}--"
)

# Edge 5: Large body
parts.append(
    "From large@example.com Fri Jan 19 09:00:00 2024\n"
    "Message-ID: <edge005@example.com>\n"
    "Date: Fri, 19 Jan 2024 09:00:00 +0000\n"
    "From: Large Sender <large@example.com>\n"
    "To: receiver@example.com\n"
    "Subject: Very large body message\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=utf-8\n"
    "\n"
    + large_body
)

# Edge 6a: First duplicate
parts.append(
    "From dup1@example.com Sat Jan 20 09:00:00 2024\n"
    "Message-ID: <duplicate@example.com>\n"
    "Date: Sat, 20 Jan 2024 09:00:00 +0000\n"
    "From: Dup Sender A <dup1@example.com>\n"
    "To: receiver@example.com\n"
    "Subject: First duplicate\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=utf-8\n"
    "\n"
    "First message with duplicate Message-ID. This one should insert."
)

# Edge 6b: Second duplicate
parts.append(
    "From dup2@example.com Sun Jan 21 09:00:00 2024\n"
    "Message-ID: <duplicate@example.com>\n"
    "Date: Sun, 21 Jan 2024 09:00:00 +0000\n"
    "From: Dup Sender B <dup2@example.com>\n"
    "To: receiver@example.com\n"
    "Subject: Second duplicate — different content, same Message-ID\n"
    "MIME-Version: 1.0\n"
    "Content-Type: text/plain; charset=utf-8\n"
    "\n"
    "Second message with the SAME Message-ID. INSERT OR IGNORE must skip this row."
)

content = "\n\n".join(parts) + "\n"
OUT.write_text(content, encoding="utf-8", newline="\n")
print(f"Wrote {OUT} ({len(content):,} bytes)")
print(f"Large body segment: {len(large_body):,} chars")
