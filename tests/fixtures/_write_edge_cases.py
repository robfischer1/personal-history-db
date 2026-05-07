"""One-shot script to write edge_cases.mbox with the large-body segment.
Run: uv run python tests/fixtures/_write_edge_cases.py
"""
import base64
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

OUT = Path(__file__).parent / "edge_cases.mbox"

def b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")

# RFC 2047 encoded subject for "Héllo Wörld"
encoded_subject = f"=?UTF-8?B?{b64('Héllo Wörld')}?="

# PDF stub for attachment-only message
pdf_stub = b"%PDF-1.4 synthetic fixture attachment empty-body test"
encoded_pdf = base64.b64encode(pdf_stub).decode("ascii")

# Large body: exactly 200100 chars
FILLER_UNIT = "Synthetic filler text for MAX_BODY_LEN cap test. "
large_body = (FILLER_UNIT * (200100 // len(FILLER_UNIT) + 1))[:200100]
assert len(large_body) == 200100

boundary4 = "=_boundary_edge004_empty_body"

lines = []

# Edge 1: No Message-ID
lines.append("From sender@example.com Mon Jan 15 09:00:00 2024")
lines.append("Date: Mon, 15 Jan 2024 09:00:00 +0000")
lines.append("From: Sender One <sender@example.com>")
lines.append("To: receiver@example.com")
lines.append("Subject: Message without Message-ID")
lines.append("MIME-Version: 1.0")
lines.append("Content-Type: text/plain; charset=utf-8")
lines.append("")
lines.append("This message intentionally has no Message-ID header.")

lines.append("")  # blank between messages

# Edge 2: RFC 2047 encoded Subject
lines.append("From encoder@example.com Tue Jan 16 09:00:00 2024")
lines.append("Message-ID: <edge002@example.com>")
lines.append("Date: Tue, 16 Jan 2024 09:00:00 +0000")
lines.append("From: Encoder <encoder@example.com>")
lines.append("To: decoder@example.com")
lines.append(f"Subject: {encoded_subject}")
lines.append("MIME-Version: 1.0")
lines.append("Content-Type: text/plain; charset=utf-8")
lines.append("")
lines.append("Body of message with RFC 2047 encoded subject.")

lines.append("")

# Edge 3: No Date header
lines.append("From nodater@example.com Wed Jan 17 09:00:00 2024")
lines.append("Message-ID: <edge003@example.com>")
lines.append("From: No Date Sender <nodater@example.com>")
lines.append("To: receiver@example.com")
lines.append("Subject: Message without Date header")
lines.append("MIME-Version: 1.0")
lines.append("Content-Type: text/plain; charset=utf-8")
lines.append("")
lines.append("This message has no Date header. date_sent should be None.")

lines.append("")

# Edge 4: Attachment-only (no text parts)
lines.append("From attach@example.com Thu Jan 18 09:00:00 2024")
lines.append("Message-ID: <edge004@example.com>")
lines.append("Date: Thu, 18 Jan 2024 09:00:00 +0000")
lines.append("From: Attach Sender <attach@example.com>")
lines.append("To: receiver@example.com")
lines.append("Subject: Attachment-only message")
lines.append("MIME-Version: 1.0")
lines.append(f'Content-Type: multipart/mixed; boundary="{boundary4}"')
lines.append("")
lines.append(f"--{boundary4}")
lines.append('Content-Type: application/pdf; name="document.pdf"')
lines.append("Content-Transfer-Encoding: base64")
lines.append('Content-Disposition: attachment; filename="document.pdf"')
lines.append("")
lines.append(encoded_pdf)
lines.append("")
lines.append(f"--{boundary4}--")

lines.append("")

# Edge 5: Large body (200100 chars)
lines.append("From large@example.com Fri Jan 19 09:00:00 2024")
lines.append("Message-ID: <edge005@example.com>")
lines.append("Date: Fri, 19 Jan 2024 09:00:00 +0000")
lines.append("From: Large Sender <large@example.com>")
lines.append("To: receiver@example.com")
lines.append("Subject: Very large body message")
lines.append("MIME-Version: 1.0")
lines.append("Content-Type: text/plain; charset=utf-8")
lines.append("")
lines.append(large_body)

lines.append("")

# Edge 6a: First duplicate
lines.append("From dup1@example.com Sat Jan 20 09:00:00 2024")
lines.append("Message-ID: <duplicate@example.com>")
lines.append("Date: Sat, 20 Jan 2024 09:00:00 +0000")
lines.append("From: Dup Sender A <dup1@example.com>")
lines.append("To: receiver@example.com")
lines.append("Subject: First duplicate")
lines.append("MIME-Version: 1.0")
lines.append("Content-Type: text/plain; charset=utf-8")
lines.append("")
lines.append("First message with duplicate Message-ID. This one should insert.")

lines.append("")

# Edge 6b: Second duplicate
lines.append("From dup2@example.com Sun Jan 21 09:00:00 2024")
lines.append("Message-ID: <duplicate@example.com>")
lines.append("Date: Sun, 21 Jan 2024 09:00:00 +0000")
lines.append("From: Dup Sender B <dup2@example.com>")
lines.append("To: receiver@example.com")
lines.append("Subject: Second duplicate — different content, same Message-ID")
lines.append("MIME-Version: 1.0")
lines.append("Content-Type: text/plain; charset=utf-8")
lines.append("")
lines.append("Second message with the SAME Message-ID. INSERT OR IGNORE must skip this row.")
lines.append("")  # trailing newline

content = "\n".join(lines)
OUT.write_text(content, encoding="utf-8", newline="\n")
print(f"Wrote {OUT} ({len(content):,} bytes)")
