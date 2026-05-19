"""Tests for Pydantic three-tier settings."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.identity import IdentitySettings
from phdb.settings import Settings


def test_default_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHDB_INSTANCE_DIR", raising=False)
    monkeypatch.delenv("PHDB_DB_PATH", raising=False)
    s = Settings.load(db_path=":memory:")
    assert s.db_path == Path(":memory:")
    assert s.embedding.model == "nomic-embed-text"
    assert s.embedding.dim == 768
    assert s.instance_dir is None
    assert s.adapter_paths == []


def test_instance_toml_override(tmp_path: Path) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()

    (instance_dir / "config.toml").write_text(
        '[embedding]\nmodel = "custom-model"\ndim = 384\n',
        encoding="utf-8",
    )

    s = Settings.load(db_path=":memory:", instance_dir=instance_dir)
    assert s.embedding.model == "custom-model"
    assert s.embedding.dim == 384


def test_env_var_override(tmp_path: Path, monkeypatch: object) -> None:
    import pytest

    monkeypatch = pytest.MonkeyPatch()  # type: ignore[assignment]
    monkeypatch.setenv("PHDB_LOG_LEVEL", "DEBUG")
    try:
        s = Settings.load(db_path=":memory:")
        assert s.log_level == "DEBUG"
    finally:
        monkeypatch.undo()


def test_identity_is_me() -> None:
    identity = IdentitySettings(
        owner_names={"jane doe", "jane"},
        owner_emails={"jane@example.com"},
        owner_phones={"+15555555555"},
    )
    assert identity.is_me("jane@example.com")
    assert identity.is_me("Jane@Example.com")
    assert identity.is_me("+15555555555")
    assert identity.is_me("Jane Doe")
    assert not identity.is_me("someone@else.com")


def test_identity_toml_loading(tmp_path: Path) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()

    (instance_dir / "identity.toml").write_text(
        '[identity]\nowner_names = ["alice", "alice smith"]\n'
        'owner_emails = ["alice@example.com"]\n',
        encoding="utf-8",
    )

    s = Settings.load(db_path=":memory:", instance_dir=instance_dir)
    assert s.identity.is_me("alice@example.com")
    assert s.identity.is_me("Alice Smith")


def test_multiple_toml_files_merge(tmp_path: Path) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()

    (instance_dir / "01_paths.toml").write_text(
        'log_level = "WARNING"\n',
        encoding="utf-8",
    )
    (instance_dir / "02_embedding.toml").write_text(
        '[embedding]\nendpoint = "http://custom:11434"\n',
        encoding="utf-8",
    )

    s = Settings.load(db_path=":memory:", instance_dir=instance_dir)
    assert s.log_level == "WARNING"
    assert s.embedding.endpoint == "http://custom:11434"
    assert s.embedding.model == "nomic-embed-text"  # default preserved


def test_identity_handles_is_me() -> None:
    identity = IdentitySettings(
        owner_handles={"discord": {"myhandle"}, "twitter": {"mytwitter"}},
    )
    assert identity.is_me("discord:myhandle")
    assert identity.is_me("Discord:MyHandle")
    assert identity.is_me("twitter:mytwitter")
    assert not identity.is_me("discord:otheruser")
    assert not identity.is_me("myhandle")  # no platform prefix -> no match


def test_identity_handles_cross_platform() -> None:
    identity = IdentitySettings(
        owner_handles={"discord": {"alice"}, "twitter": {"bob"}},
    )
    assert identity.is_me("discord:alice")
    assert not identity.is_me("twitter:alice")
    assert identity.is_me("twitter:bob")
    assert not identity.is_me("discord:bob")


def test_identity_handles_fallback() -> None:
    identity = IdentitySettings(
        owner_emails={"test@example.com"},
        owner_handles={"discord": {"myhandle"}},
    )
    assert identity.is_me("test@example.com")
    assert identity.is_me("discord:myhandle")
    assert not identity.is_me("discord:other")
    assert not identity.is_me("unknown@example.com")


def test_identity_handles_toml_loading(tmp_path: Path) -> None:
    instance_dir = tmp_path / "instance"
    instance_dir.mkdir()

    (instance_dir / "identity.toml").write_text(
        '[identity]\n'
        'owner_emails = ["user@example.com"]\n'
        '\n'
        '[identity.owner_handles]\n'
        'discord = ["testhandle"]\n'
        'aim = ["oldhandle"]\n',
        encoding="utf-8",
    )

    s = Settings.load(db_path=":memory:", instance_dir=instance_dir)
    assert s.identity.is_me("discord:testhandle")
    assert s.identity.is_me("aim:oldhandle")
    assert s.identity.is_me("user@example.com")
    assert not s.identity.is_me("discord:other")


def test_identity_is_configured_empty() -> None:
    identity = IdentitySettings()
    assert not identity.is_configured


def test_identity_is_configured_populated() -> None:
    identity = IdentitySettings(owner_emails={"x@y.com"})
    assert identity.is_configured


def test_identity_pii_literals_empty() -> None:
    identity = IdentitySettings()
    assert identity.pii_literals() == []


def test_identity_pii_literals_populated() -> None:
    identity = IdentitySettings(
        owner_names={"alice"},
        owner_emails={"a@b.com"},
        owner_phones={"+15551234567"},
        owner_handles={"discord": {"alicehandle"}},
    )
    literals = identity.pii_literals()
    assert "alice" in literals
    assert "a@b.com" in literals
    assert "+15551234567" in literals
    assert "alicehandle" in literals


def test_settings_no_identity_no_traceback() -> None:
    """Framework loads cleanly with no identity config at all."""
    s = Settings.load(db_path=":memory:")
    assert not s.identity.is_configured
    assert s.identity.pii_literals() == []
    assert s.identity.is_me("anything@test.com") is False


def test_identity_importable_from_settings() -> None:
    """Backwards-compat: IdentitySettings still importable from phdb.settings."""
    from phdb.settings import IdentitySettings as IS
    assert IS is IdentitySettings
