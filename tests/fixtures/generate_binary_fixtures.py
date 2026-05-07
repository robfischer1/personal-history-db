#!/usr/bin/env python3
"""Generate binary test fixtures (zip archives and SQLite databases).

Run once to create:
  - facebook/facebook_test.zip
  - facebook_posts/facebook_posts_test.zip
  - phone_sms/mmssms.db
  - apple_notes_full/NoteStore.sqlite

All data is synthetic -- no real user data.
"""

import gzip
import io
import os
import sqlite3
import zipfile

FIXTURES = os.path.dirname(os.path.abspath(__file__))


# ── helpers ──────────────────────────────────────────────────────────────

def _encode_varint(value: int) -> bytes:
    """Encode an unsigned integer as a protobuf varint."""
    parts = []
    while value > 0x7F:
        parts.append((value & 0x7F) | 0x80)
        value >>= 7
    parts.append(value & 0x7F)
    return bytes(parts)


def _encode_length_delimited(field_number: int, data: bytes) -> bytes:
    """Encode a length-delimited protobuf field."""
    tag = (field_number << 3) | 2
    return _encode_varint(tag) + _encode_varint(len(data)) + data


def build_apple_notes_protobuf(text: str) -> bytes:
    """Build a gzip-compressed protobuf blob matching Apple Notes decode path.

    Decode path: gzip decompress -> field 2 -> field 3 -> field 2 = UTF-8 text.
    Encode (reversed): text -> wrap in field 2 -> wrap in field 3 -> wrap in field 2 -> gzip.
    """
    text_bytes = text.encode("utf-8")
    inner = _encode_length_delimited(2, text_bytes)       # field 2 = text
    middle = _encode_length_delimited(3, inner)            # field 3 wraps inner
    outer = _encode_length_delimited(2, middle)            # field 2 wraps middle
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
        gz.write(outer)
    return buf.getvalue()


# ── 1. facebook/facebook_test.zip ────────────────────────────────────────

def generate_facebook_zip():
    fb_msg_html = """\
<!DOCTYPE html>
<html><body>
<h1>Test User</h1>
<section class="_a6-g">
  <h2>Alice</h2>
  <div class="_a6-p"><div><div>Hey, how are you?</div></div></div>
  <footer><div class="_a72d">Mar 15, 2023 10:30:00 am</div></footer>
</section>
<section class="_a6-g">
  <h2>Bob</h2>
  <div class="_a6-p"><div><div>I'm good, thanks!</div></div></div>
  <footer><div class="_a72d">Mar 15, 2023 10:35:00 am</div></footer>
</section>
<section class="_a6-g">
  <h2>Alice</h2>
  <div class="_a6-p"><div><div>Want to grab lunch?</div></div></div>
  <footer><div class="_a72d">Mar 15, 2023 10:40:00 am</div></footer>
</section>
</body></html>"""

    path = os.path.join(FIXTURES, "facebook", "facebook_test.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "your_facebook_activity/messages/inbox/testuser_abc123/message_1.html",
            fb_msg_html,
        )
    print(f"  wrote {path}")


# ── 2. facebook_posts/facebook_posts_test.zip ───────────────────────────

def generate_facebook_posts_zip():
    fb_posts_html = """\
<!DOCTYPE html>
<html><body>
<section class="_a6-g">
  <h2><owner-name></h2>
  <div class="_a6-p">Just had an amazing day at the park!</div>
  <footer><div class="_a72d">Mar 15, 2023 10:30:00 am</div></footer>
</section>
<section class="_a6-g">
  <h2><owner-name></h2>
  <div class="_a6-p">Working on a new project today.</div>
  <footer><div class="_a72d">Mar 16, 2023 2:00:00 pm</div></footer>
</section>
</body></html>"""

    path = os.path.join(FIXTURES, "facebook_posts", "facebook_posts_test.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(
            "your_facebook_activity/posts/your_posts__check_ins__photos_and_videos_1.html",
            fb_posts_html,
        )
    print(f"  wrote {path}")


# ── 3. phone_sms/mmssms.db ──────────────────────────────────────────────

def generate_phone_sms_db():
    path = os.path.join(FIXTURES, "phone_sms", "mmssms.db")
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    c = conn.cursor()

    # SMS table
    c.execute("""
        CREATE TABLE sms (
            _id INTEGER PRIMARY KEY,
            thread_id INTEGER,
            address TEXT,
            date INTEGER,
            body TEXT,
            type INTEGER
        )
    """)
    c.executemany("INSERT INTO sms VALUES (?,?,?,?,?,?)", [
        (1, 1, "+15551234567", 1678886400000, "Hey, what are you up to?", 1),
        (2, 1, "+15551234567", 1678886460000, "Not much, just hanging out", 2),
        (3, 2, "+15559876543", 1678972800000, "Happy birthday!", 2),
    ])

    # MMS tables
    c.execute("""
        CREATE TABLE pdu (
            _id INTEGER PRIMARY KEY,
            m_id TEXT,
            msg_box INTEGER,
            date INTEGER,
            sub TEXT
        )
    """)
    c.execute("""
        CREATE TABLE addr (
            msg_id INTEGER,
            address TEXT,
            type INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE part (
            mid INTEGER,
            ct TEXT,
            name TEXT,
            text TEXT
        )
    """)

    c.execute("INSERT INTO pdu VALUES (?,?,?,?,?)", (1, "mms-001", 1, 1678886500, "Photo"))
    c.executemany("INSERT INTO addr VALUES (?,?,?)", [
        (1, "+15551234567", 137),
        (1, "+15559876543", 151),
    ])
    c.executemany("INSERT INTO part VALUES (?,?,?,?)", [
        (1, "text/plain", None, "Check out this photo"),
        (1, "image/jpeg", "photo.jpg", None),
    ])

    conn.commit()
    conn.close()
    print(f"  wrote {path}")


# ── 4. apple_notes_full/NoteStore.sqlite ─────────────────────────────────

def generate_apple_notes_db():
    path = os.path.join(FIXTURES, "apple_notes_full", "NoteStore.sqlite")
    if os.path.exists(path):
        os.remove(path)
    conn = sqlite3.connect(path)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE ZICCLOUDSYNCINGOBJECT (
            Z_PK INTEGER PRIMARY KEY,
            Z_ENT INTEGER,
            ZTITLE TEXT,
            ZTITLE1 TEXT,
            ZSNIPPET TEXT,
            ZCREATIONDATE REAL,
            ZCREATIONDATE1 REAL,
            ZCREATIONDATE2 REAL,
            ZCREATIONDATE3 REAL,
            ZMARKEDFORDELETION INTEGER
        )
    """)
    c.execute("""
        CREATE TABLE ZICNOTEDATA (
            Z_PK INTEGER PRIMARY KEY,
            ZNOTE INTEGER,
            ZDATA BLOB
        )
    """)

    # Note 1: has ZTITLE1, ZCREATIONDATE3, and protobuf body
    full_text = (
        "This is the full body text of the test note "
        "that is longer than the snippet."
    )
    protobuf_blob = build_apple_notes_protobuf(full_text)

    c.execute(
        "INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES (?,?,?,?,?,?,?,?,?,?)",
        (1, 12, None, "Test Note", "Short snippet...",
         None, None, None, 700000000.0, 0),
    )
    c.execute(
        "INSERT INTO ZICNOTEDATA VALUES (?,?,?)",
        (1, 1, protobuf_blob),
    )

    # Note 2: has ZTITLE (no ZTITLE1), ZCREATIONDATE, no protobuf (NULL ZDATA)
    c.execute(
        "INSERT INTO ZICCLOUDSYNCINGOBJECT VALUES (?,?,?,?,?,?,?,?,?,?)",
        (2, 12, "Another Note", None, "Another snippet",
         700100000.0, None, None, None, 0),
    )
    c.execute(
        "INSERT INTO ZICNOTEDATA VALUES (?,?,?)",
        (2, 2, None),
    )

    conn.commit()
    conn.close()
    print(f"  wrote {path}")


# ── main ─────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("Generating binary test fixtures...")
    generate_facebook_zip()
    generate_facebook_posts_zip()
    generate_phone_sms_db()
    generate_apple_notes_db()
    print("Done.")
