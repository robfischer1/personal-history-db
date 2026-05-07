"""Generate test fixture: tar.gz containing browser.db for the TitaniumBackup adapter."""

from __future__ import annotations

import sqlite3
import tarfile
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
OUTPUT = HERE / "com.android.browser-test.tar.gz"


def main() -> None:
    tmpdir = Path(tempfile.mkdtemp())
    db_path = tmpdir / "browser.db"

    conn = sqlite3.connect(str(db_path))
    conn.execute("""CREATE TABLE bookmarks (
        _id INTEGER PRIMARY KEY,
        title TEXT,
        url TEXT,
        visits INTEGER DEFAULT 0,
        date INTEGER,
        created INTEGER,
        description TEXT,
        bookmark INTEGER DEFAULT 0
    )""")
    # Row 1: bookmark
    conn.execute(
        "INSERT INTO bookmarks (_id, title, url, visits, date, created, bookmark) "
        "VALUES (1, 'Python Docs', 'https://docs.python.org/3/', 5, 1325376000000, 1325376000000, 1)"
    )
    # Row 2: bookmark
    conn.execute(
        "INSERT INTO bookmarks (_id, title, url, visits, date, created, bookmark) "
        "VALUES (2, 'Stack Overflow', 'https://stackoverflow.com/questions/12345', 3, 1330000000000, 1325400000000, 1)"
    )
    # Row 3: history entry (not a bookmark)
    conn.execute(
        "INSERT INTO bookmarks (_id, title, url, visits, date, created, bookmark) "
        "VALUES (3, 'Reddit Front Page', 'https://www.reddit.com/', 10, 1335000000000, 1330000000000, 0)"
    )
    conn.commit()
    conn.close()

    with tarfile.open(str(OUTPUT), "w:gz") as tf:
        tf.add(str(db_path), arcname="dbdata/databases/com.android.browser/browser.db")

    # Cleanup
    db_path.unlink()
    tmpdir.rmdir()
    print(f"Wrote: {OUTPUT}")


if __name__ == "__main__":
    main()
