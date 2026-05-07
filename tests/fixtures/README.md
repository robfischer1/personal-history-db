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
