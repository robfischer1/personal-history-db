"""Tests for the google_contacts adapter."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.adapters.google_contacts import (
    GoogleContactsAdapter,
    _parse_vcf,
)
from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "google_contacts"
FIXTURE_VCF = FIXTURE_DIR / "contacts.vcf"


class TestParseVcf:
    def test_basic_parse(self) -> None:
        text = FIXTURE_VCF.read_text(encoding="utf-8")
        contacts = _parse_vcf(text)
        assert len(contacts) == 3

    def test_fn_extracted(self) -> None:
        text = FIXTURE_VCF.read_text(encoding="utf-8")
        contacts = _parse_vcf(text)
        fns = [c.get("fn") for c in contacts]
        assert "Jane Doe" in fns
        assert "Bob Smith" in fns

    def test_emails(self) -> None:
        text = FIXTURE_VCF.read_text(encoding="utf-8")
        contacts = _parse_vcf(text)
        jane = [c for c in contacts if c.get("fn") == "Jane Doe"][0]
        assert "jane.doe@example.com" in jane["emails"]

    def test_multiple_emails(self) -> None:
        text = FIXTURE_VCF.read_text(encoding="utf-8")
        contacts = _parse_vcf(text)
        bob = [c for c in contacts if c.get("fn") == "Bob Smith"][0]
        assert len(bob["emails"]) == 2

    def test_phone_normalized(self) -> None:
        text = FIXTURE_VCF.read_text(encoding="utf-8")
        contacts = _parse_vcf(text)
        jane = [c for c in contacts if c.get("fn") == "Jane Doe"][0]
        phones = jane["phones"]
        assert len(phones) == 1
        assert "(" not in phones[0]
        assert " " not in phones[0]

    def test_org_and_title(self) -> None:
        text = FIXTURE_VCF.read_text(encoding="utf-8")
        contacts = _parse_vcf(text)
        jane = [c for c in contacts if c.get("fn") == "Jane Doe"][0]
        assert "Acme Corp" in jane["orgs"]
        assert "Software Engineer" in jane["titles"]

    def test_empty_name_with_phone_included(self) -> None:
        text = FIXTURE_VCF.read_text(encoding="utf-8")
        contacts = _parse_vcf(text)
        phone_only = [c for c in contacts if not c.get("fn")]
        assert len(phone_only) == 1
        assert len(phone_only[0]["phones"]) == 1

    def test_empty_card_skipped(self) -> None:
        text = FIXTURE_VCF.read_text(encoding="utf-8")
        contacts = _parse_vcf(text)
        assert len(contacts) == 3


@pytest.fixture
def gc_settings(tmp_path: Path) -> Settings:
    return Settings(
        db_path=tmp_path / "test.db",
        identity=IdentitySettings(owner_names={"test user"}),
    )


@pytest.fixture
def gc_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return db_path


class TestGoogleContactsIntegration:
    def test_basic_ingest(self, gc_db: Path, gc_settings: Settings) -> None:
        gc_settings.db_path = gc_db
        adapter = GoogleContactsAdapter()
        with connect(gc_db) as conn:
            report = adapter.run(FIXTURE_DIR, conn, gc_settings)
        assert report.rows_inserted == 3
        assert report.rows_skipped == 0

    def test_schema_type_person(self, gc_db: Path, gc_settings: Settings) -> None:
        gc_settings.db_path = gc_db
        adapter = GoogleContactsAdapter()
        with connect(gc_db) as conn:
            adapter.run(FIXTURE_DIR, conn, gc_settings)
            types = conn.execute("SELECT DISTINCT schema_type FROM messages").fetchall()
        assert all(t[0] == "Person" for t in types)

    def test_all_bulk(self, gc_db: Path, gc_settings: Settings) -> None:
        gc_settings.db_path = gc_db
        adapter = GoogleContactsAdapter()
        with connect(gc_db) as conn:
            adapter.run(FIXTURE_DIR, conn, gc_settings)
            bulk = conn.execute("SELECT COUNT(*) FROM messages WHERE is_bulk = 1").fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        assert bulk == total

    def test_body_contains_info(self, gc_db: Path, gc_settings: Settings) -> None:
        gc_settings.db_path = gc_db
        adapter = GoogleContactsAdapter()
        with connect(gc_db) as conn:
            adapter.run(FIXTURE_DIR, conn, gc_settings)
            row = conn.execute(
                "SELECT body_text FROM messages WHERE subject = 'Jane Doe'"
            ).fetchone()
        body = row[0]
        assert "Acme Corp" in body
        assert "jane.doe@example.com" in body

    def test_primary_address_email(self, gc_db: Path, gc_settings: Settings) -> None:
        gc_settings.db_path = gc_db
        adapter = GoogleContactsAdapter()
        with connect(gc_db) as conn:
            adapter.run(FIXTURE_DIR, conn, gc_settings)
            row = conn.execute(
                "SELECT sender_address FROM messages WHERE subject = 'Jane Doe'"
            ).fetchone()
        assert row[0] == "jane.doe@example.com"

    def test_idempotent_rerun(self, gc_db: Path, gc_settings: Settings) -> None:
        gc_settings.db_path = gc_db
        adapter = GoogleContactsAdapter()
        with connect(gc_db) as conn:
            adapter.run(FIXTURE_DIR, conn, gc_settings)

        adapter2 = GoogleContactsAdapter()
        with connect(gc_db) as conn:
            r2 = adapter2.run(FIXTURE_DIR, conn, gc_settings)
        assert r2.rows_inserted == 0

    def test_threads(self, gc_db: Path, gc_settings: Settings) -> None:
        gc_settings.db_path = gc_db
        adapter = GoogleContactsAdapter()
        with connect(gc_db) as conn:
            adapter.run(FIXTURE_DIR, conn, gc_settings)
            threads = conn.execute("SELECT COUNT(*) FROM threads").fetchone()[0]
        assert threads >= 1

    def test_message_thread_bridge(self, gc_db: Path, gc_settings: Settings) -> None:
        gc_settings.db_path = gc_db
        adapter = GoogleContactsAdapter()
        with connect(gc_db) as conn:
            report = adapter.run(FIXTURE_DIR, conn, gc_settings)
            bridge = conn.execute("SELECT COUNT(*) FROM message_threads").fetchone()[0]
        assert bridge == report.rows_inserted
