#!/usr/bin/env python3
"""Generate a synthetic TitaniumBackup Twitter tar.gz for testing.

Creates test_twitter.tar.gz containing a SQLite DB at
  data/data/com.twitter.android/databases/72437370.db
with users, statuses, stories, and messages tables populated with synthetic rows.
"""
from __future__ import annotations

import sqlite3
import struct
import tarfile
import tempfile
from pathlib import Path

OWNER_USER_ID = 99999
OTHER_USER_ID = 88888

DB_INNER_PATH = "data/data/com.twitter.android/databases/72437370.db"


def _make_story_blob(text: str) -> bytes:
    """Build a minimal blob with extractable printable strings."""
    prefix = struct.pack("4B", 0xAC, 0xED, 0x00, 0x05)
    encoded = text.encode("ascii")
    return prefix + b"\x00\x00" + encoded + b"\x00\x00"


def _build_db(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            name TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE statuses (
            status_id INTEGER PRIMARY KEY,
            author_id INTEGER,
            content TEXT,
            created INTEGER
        )"""
    )
    conn.execute(
        """CREATE TABLE stories (
            identifier TEXT PRIMARY KEY,
            type INTEGER,
            score REAL,
            query TEXT,
            data BLOB
        )"""
    )
    conn.execute(
        """CREATE TABLE messages (
            msg_id INTEGER PRIMARY KEY,
            content TEXT,
            created INTEGER,
            sender_id INTEGER,
            recipient_id INTEGER
        )"""
    )

    conn.execute(
        "INSERT INTO users VALUES (?, ?, ?)",
        (OWNER_USER_ID, "testuser", "Test User"),
    )
    conn.execute(
        "INSERT INTO users VALUES (?, ?, ?)",
        (OTHER_USER_ID, "otherperson", "Other Person"),
    )

    conn.execute(
        "INSERT INTO statuses VALUES (?, ?, ?, ?)",
        (1001, OWNER_USER_ID, "Hello world from Twitter!", 1322956800000),
    )
    conn.execute(
        "INSERT INTO statuses VALUES (?, ?, ?, ?)",
        (1002, OTHER_USER_ID, "Reply to a tweet", 1322957000000),
    )
    conn.execute(
        "INSERT INTO statuses VALUES (?, ?, ?, ?)",
        (1003, OWNER_USER_ID, "Another tweet by me", 1322958000000),
    )

    conn.execute(
        "INSERT INTO stories VALUES (?, ?, ?, ?, ?)",
        ("story-abc", 1, 0.9, "trending", _make_story_blob("Big News Story Today")),
    )
    conn.execute(
        "INSERT INTO stories VALUES (?, ?, ?, ?, ?)",
        ("story-def", 2, 0.5, "discover", _make_story_blob("Celebrity Interview Clip")),
    )

    conn.execute(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
        (2001, "Hey whats up", 1322960000000, OWNER_USER_ID, OTHER_USER_ID),
    )
    conn.execute(
        "INSERT INTO messages VALUES (?, ?, ?, ?, ?)",
        (2002, "Not much you?", 1322961000000, OTHER_USER_ID, OWNER_USER_ID),
    )

    conn.commit()
    conn.close()


def main() -> None:
    out = Path(__file__).parent / "test_twitter.tar.gz"
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "72437370.db"
        _build_db(db_path)

        with tarfile.open(out, "w:gz") as tf:
            tf.add(str(db_path), arcname=DB_INNER_PATH)

    print(f"wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
