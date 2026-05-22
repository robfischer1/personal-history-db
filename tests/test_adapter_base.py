"""Tests for the Adapter base class and run() pipeline."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from phdb.adapters.base import Adapter, AdapterRow, DedupStrategy
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings


class StubAdapter(Adapter):
    """Minimal concrete adapter for testing."""

    name = "stub"
    source_kind = "test"
    file_kind = "csv"
    schema_type = "Message"
    dedup_strategy = DedupStrategy.CONTENT_HASH

    def __init__(self, rows: list[AdapterRow] | None = None) -> None:
        self._rows = rows or []

    def iter_rows(self, source_path: Path, **kwargs: object) -> Iterator[AdapterRow]:
        yield from self._rows


def _make_row(
    body: str = "hello world",
    sender: str = "someone@example.com",
    date: str = "2024-01-15T10:00:00Z",
) -> AdapterRow:
    return AdapterRow(
        body_text=body,
        body_text_source="test",
        sender_address=sender,
        date_sent=date,
    )


def test_adapter_run_inserts_rows(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()

    rows = [_make_row(f"message {i}") for i in range(5)]
    adapter = StubAdapter(rows)
    settings = Settings.load(db_path=db_path)

    with connect(db_path) as conn:
        report = adapter.run(Path("fake_source.csv"), conn, settings)

    assert report.rows_yielded == 5
    assert report.rows_inserted == 5
    assert report.rows_skipped == 0

    with connect(db_path) as conn:
        count = conn.execute("SELECT count(*) FROM chat_messages").fetchone()[0]
        assert count == 5


def test_adapter_dedup_skips_duplicates(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()

    rows = [_make_row("same message")] * 3
    adapter = StubAdapter(rows)
    settings = Settings.load(db_path=db_path)

    with connect(db_path) as conn:
        report = adapter.run(Path("source.csv"), conn, settings)

    assert report.rows_inserted == 1
    assert report.rows_skipped == 2


def test_adapter_registers_source_file(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()

    adapter = StubAdapter([_make_row()])
    settings = Settings.load(db_path=db_path)

    with connect(db_path) as conn:
        report = adapter.run(Path("my_source.csv"), conn, settings)

    with connect(db_path) as conn:
        row = conn.execute("SELECT source_path, file_kind, source_kind FROM source_files WHERE id = ?",
                           (report.source_file_id,)).fetchone()
        assert row[0] == "my_source.csv"
        assert row[1] == "csv"
        assert row[2] == "test"


def test_adapter_direction_inference(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()

    rows = [
        _make_row(sender="owner@example.com", body="outbound msg"),
        _make_row(sender="friend@example.com", body="inbound msg"),
    ]
    adapter = StubAdapter(rows)

    identity = IdentitySettings(owner_emails={"owner@example.com"})
    settings = Settings.load(db_path=db_path)
    settings.identity = identity

    with connect(db_path) as conn:
        adapter.run(Path("source.csv"), conn, settings)
        directions = [r[0] for r in conn.execute(
            "SELECT direction FROM chat_messages ORDER BY id"
        ).fetchall()]

    assert directions == ["outbound", "inbound"]


def test_adapter_emits_recipient_triples(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()

    row = _make_row()
    row.recipients = [
        {"address": "alice@example.com", "name": "Alice", "rtype": "to"},
        {"address": "bob@example.com", "name": "Bob", "rtype": "cc"},
    ]
    adapter = StubAdapter([row])
    settings = Settings.load(db_path=db_path)

    with connect(db_path) as conn:
        adapter.run(Path("source.csv"), conn, settings)
        # Verify sentTo triples were emitted (one per recipient)
        sent_to_id = conn.execute(
            "SELECT id FROM predicates WHERE name = 'sentTo'"
        ).fetchone()[0]
        triple_count = conn.execute(
            "SELECT count(*) FROM triples WHERE predicate_id = ?",
            (sent_to_id,),
        ).fetchone()[0]

    assert triple_count == 2

    # Verify contact nodes were created
    with connect(db_path) as conn:
        contacts = conn.execute(
            "SELECT normalized_label FROM nodes WHERE kind = 'contact' ORDER BY normalized_label"
        ).fetchall()
        assert [c[0] for c in contacts] == ["alice@example.com", "bob@example.com"]


def test_adapter_inserts_attachments(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()

    row = _make_row()
    row.has_attachments = 1
    row.attachment_count = 1
    row.attachments = [{"filename": "doc.pdf", "content_type": "application/pdf", "size_bytes": 1024}]
    adapter = StubAdapter([row])
    settings = Settings.load(db_path=db_path)

    with connect(db_path) as conn:
        adapter.run(Path("source.csv"), conn, settings)
        att = conn.execute("SELECT filename, content_type FROM attachments").fetchone()

    assert att[0] == "doc.pdf"
    assert att[1] == "application/pdf"


def test_adapter_direction_inference_with_handles(tmp_path: Path) -> None:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()

    rows = [
        _make_row(sender="discord:myhandle", body="outbound via handle"),
        _make_row(sender="discord:otheruser", body="inbound via handle"),
        _make_row(sender="owner@example.com", body="outbound via email"),
    ]
    adapter = StubAdapter(rows)

    identity = IdentitySettings(
        owner_emails={"owner@example.com"},
        owner_handles={"discord": {"myhandle"}},
    )
    settings = Settings.load(db_path=db_path)
    settings.identity = identity

    with connect(db_path) as conn:
        adapter.run(Path("source.csv"), conn, settings)
        directions = [r[0] for r in conn.execute(
            "SELECT direction FROM chat_messages ORDER BY id"
        ).fetchall()]

    assert directions == ["outbound", "inbound", "outbound"]
