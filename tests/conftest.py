"""Shared test fixtures."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.settings import IdentitySettings, Settings


@pytest.fixture
def tmp_db(tmp_path: Path) -> Path:
    """Return a path for a temporary database file."""
    return tmp_path / "test.db"


@pytest.fixture
def migrated_db(tmp_db: Path) -> Path:
    """Return a path to a DB with all project migrations applied."""
    with connect(tmp_db) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return tmp_db


@pytest.fixture
def test_settings(tmp_db: Path) -> Settings:
    """Settings pointing at a temp DB with no instance config."""
    return Settings.load(db_path=tmp_db)


@pytest.fixture
def test_identity() -> IdentitySettings:
    """A synthetic identity for testing direction inference."""
    return IdentitySettings(
        owner_names={"test user", "testuser"},
        owner_emails={"test@example.com", "testuser@example.com"},
        owner_phones={"+15555555555"},
        owner_handles={"discord": {"testhandle"}, "twitter": {"testtwitter"}},
    )


@pytest.fixture
def instance_dir(tmp_path: Path) -> Path:
    """A temporary instance directory with synthetic config."""
    inst = tmp_path / "instance"
    inst.mkdir()

    (inst / "identity.toml").write_text(
        '[identity]\n'
        'owner_names = ["alice", "alice smith"]\n'
        'owner_emails = ["alice@example.com"]\n'
        'owner_phones = ["+15550001234"]\n'
        '\n'
        '[identity.owner_handles]\n'
        'discord = ["alice_discord"]\n'
        'twitter = ["alice_twitter"]\n',
        encoding="utf-8",
    )

    (inst / "embedding.toml").write_text(
        '[embedding]\nmodel = "nomic-embed-text"\ndim = 768\n'
        'endpoint = "http://localhost:11434"\n',
        encoding="utf-8",
    )

    (inst / "atoms.toml").write_text(
        '[types.TestCustomType]\n'
        'table = "messages"\n'
        'identity_columns = ["source_file_id", "raw_hash"]\n'
        'description = "Test custom type"\n',
        encoding="utf-8",
    )

    (inst / "adapters").mkdir()
    (inst / "migrations").mkdir()

    return inst
