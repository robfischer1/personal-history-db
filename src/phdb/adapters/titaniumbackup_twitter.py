"""TitaniumBackup Twitter adapter — ingests from Android backup tar.gz.

Source: tar.gz containing data/data/com.twitter.android/databases/{user_id}.db
Three sub-tables: statuses (SocialMediaPosting), stories (SocialMediaPosting,
is_bulk=1), messages (Message, DMs). Author resolution via users table.
"""

from __future__ import annotations

import hashlib
import shutil
import sqlite3
import tarfile
import tempfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.log import get_logger

log = get_logger("phdb.adapters.titaniumbackup_twitter")

DEFAULT_OWNER_USER_ID = 72437370
DEFAULT_DB_FILENAME = "72437370.db"


def _epoch_ms_to_iso(ms: int | None) -> str | None:
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).isoformat()
    except (ValueError, OSError, OverflowError):
        return None


def _extract_strings(blob: bytes | None, min_len: int = 4) -> list[str]:
    if not blob:
        return []
    out: list[str] = []
    cur = bytearray()
    for b in blob:
        if 32 <= b < 127:
            cur.append(b)
        else:
            if len(cur) >= min_len:
                s = cur.decode("ascii", errors="ignore")
                if not (
                    s.startswith("Ljava")
                    or s.startswith("Lcom.")
                    or s.startswith("[L")
                    or s.startswith("([")
                ):
                    out.append(s)
            cur = bytearray()
    if len(cur) >= min_len:
        out.append(cur.decode("ascii", errors="ignore"))
    return out


def _load_users(src: sqlite3.Connection) -> dict[int, tuple[str, str]]:
    out: dict[int, tuple[str, str]] = {}
    for row in src.execute("SELECT user_id, username, name FROM users"):
        uid, uname, display = row
        if uid is not None:
            out[int(uid)] = (uname or "", display or "")
    return out


class TitaniumBackupTwitterAdapter(Adapter):
    """Ingest Twitter Android SQLite from TitaniumBackup tar.gz archives."""

    name = "titaniumbackup_twitter"
    source_kind = "titaniumbackup-twitter"
    file_kind = "tar.gz"
    schema_type = "SocialMediaPosting"
    dedup_strategy = DedupStrategy.PLATFORM_SYNTHETIC
    batch_size = 500

    owner_user_id: int = DEFAULT_OWNER_USER_ID
    db_filename: str = DEFAULT_DB_FILENAME

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        tmpdir = Path(tempfile.mkdtemp(prefix="twitter-tb-"))
        try:
            db_path = self._extract_db(source_path, tmpdir)
            if db_path is None:
                log.warning("DB file not found inside %s", source_path)
                return

            src = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                user_lookup = _load_users(src)
                yield from self._iter_statuses(src, user_lookup)
                yield from self._iter_stories(src)
                yield from self._iter_dms(src, user_lookup)
            finally:
                src.close()
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _extract_db(self, tar_path: Path, tmpdir: Path) -> Path | None:
        with tarfile.open(tar_path, "r:gz") as tf:
            for m in tf.getmembers():
                if m.name.endswith(f"/databases/{self.db_filename}") or m.name.endswith(
                    f"databases/{self.db_filename}"
                ):
                    tf.extract(m, tmpdir)
                    return tmpdir / m.name
        return None

    def _resolve_sender(
        self, author_id: int | None, user_lookup: dict[int, tuple[str, str]]
    ) -> tuple[str, str, str]:
        """Returns (direction, sender_address, sender_name)."""
        if author_id == self.owner_user_id:
            username, display = user_lookup.get(self.owner_user_id, ("", ""))
            addr = f"twitter:{username}" if username else f"twitter:{self.owner_user_id}"
            name = display or username or str(self.owner_user_id)
            return "outbound", addr, name
        username, display = user_lookup.get(author_id, ("", "")) if author_id else ("", "")
        addr = f"twitter:{username}" if username else f"twitter:{author_id}"
        name = display or username or str(author_id or "unknown")
        return "inbound", addr, name

    def _iter_statuses(
        self,
        src: sqlite3.Connection,
        user_lookup: dict[int, tuple[str, str]],
    ) -> Iterator[AdapterRow]:
        owner_username, _ = user_lookup.get(self.owner_user_id, ("unknown", ""))
        thread_key = f"twitter:{owner_username}:statuses"

        for row in src.execute(
            "SELECT status_id, author_id, content, created FROM statuses WHERE status_id IS NOT NULL"
        ):
            status_id, author_id, content, created = row
            msg_id = f"twitter:{status_id}"
            direction, sender_addr, sender_name = self._resolve_sender(
                int(author_id) if author_id is not None else None, user_lookup
            )
            body = content or ""
            date_iso = _epoch_ms_to_iso(created)

            yield AdapterRow(
                schema_type="SocialMediaPosting",
                rfc822_message_id=msg_id,
                sender_address=sender_addr,
                sender_name=sender_name,
                sender_domain="twitter.com",
                direction=direction,
                date_sent=date_iso,
                date_received=date_iso,
                body_text=body,
                body_text_source="twitter-android",
                raw_hash=hashlib.sha256(msg_id.encode("utf-8")).hexdigest(),
                body_text_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                thread_key=thread_key,
            )

    def _iter_stories(self, src: sqlite3.Connection) -> Iterator[AdapterRow]:
        try:
            rows = src.execute(
                "SELECT identifier, type, score, query, data FROM stories"
            ).fetchall()
        except sqlite3.OperationalError:
            return

        owner_username = "unknown"
        try:
            user_row = src.execute(
                "SELECT username FROM users WHERE user_id = ?", (self.owner_user_id,)
            ).fetchone()
            if user_row:
                owner_username = user_row[0] or "unknown"
        except sqlite3.OperationalError:
            pass
        thread_key = f"twitter:{owner_username}:stories"

        for identifier, _type, _score, _query, data in rows:
            if identifier is None:
                continue
            msg_id = f"twitter-story:{identifier}"
            strings = _extract_strings(bytes(data) if data else None)
            body = (
                " | ".join(
                    s
                    for s in strings
                    if "TwitterStory" not in s and "ArrayList" not in s and "java/" not in s
                )
                or "(no extractable text)"
            )

            yield AdapterRow(
                schema_type="SocialMediaPosting",
                rfc822_message_id=msg_id,
                sender_address="twitter:discover",
                sender_name="Twitter Discover",
                sender_domain="twitter.com",
                direction="inbound",
                body_text=body,
                body_text_source="twitter-android-story",
                is_bulk=1,
                raw_hash=hashlib.sha256(msg_id.encode("utf-8")).hexdigest(),
                body_text_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                thread_key=thread_key,
            )

    def _iter_dms(
        self,
        src: sqlite3.Connection,
        user_lookup: dict[int, tuple[str, str]],
    ) -> Iterator[AdapterRow]:
        owner_username, _ = user_lookup.get(self.owner_user_id, ("unknown", ""))
        thread_key = f"twitter:{owner_username}:dms"

        for row in src.execute(
            "SELECT msg_id, content, created, sender_id, recipient_id FROM messages WHERE msg_id IS NOT NULL"
        ):
            msg_id_val, content, created, sender_id, _recipient_id = row
            msg_id = f"twitter-dm:{msg_id_val}"
            direction, sender_addr, sender_name = self._resolve_sender(
                int(sender_id) if sender_id is not None else None, user_lookup
            )
            body = content or ""
            date_iso = _epoch_ms_to_iso(created)

            yield AdapterRow(
                schema_type="Message",
                rfc822_message_id=msg_id,
                sender_address=sender_addr,
                sender_name=sender_name,
                sender_domain="twitter.com",
                direction=direction,
                date_sent=date_iso,
                date_received=date_iso,
                body_text=body,
                body_text_source="twitter-android-dm",
                raw_hash=hashlib.sha256(msg_id.encode("utf-8")).hexdigest(),
                body_text_hash=hashlib.sha256(body.encode("utf-8")).hexdigest(),
                thread_key=thread_key,
            )
