"""Tests for instance config validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.settings import Settings
from phdb.validation import validate_instance


def test_no_instance_dir(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PHDB_INSTANCE_DIR", raising=False)
    monkeypatch.delenv("PHDB_DB_PATH", raising=False)
    s = Settings.load(db_path=":memory:")
    warnings = validate_instance(s)
    assert warnings == []


def test_missing_instance_dir(tmp_path: Path) -> None:
    s = Settings.load(db_path=":memory:", instance_dir=tmp_path / "nonexistent")
    warnings = validate_instance(s)
    assert len(warnings) == 1
    assert "does not exist" in warnings[0]


def test_no_identity_toml(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    inst.mkdir()
    s = Settings.load(db_path=":memory:", instance_dir=inst)
    warnings = validate_instance(s)
    assert any("identity.toml" in w for w in warnings)


def test_empty_identity(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "identity.toml").write_text("[identity]\n", encoding="utf-8")
    s = Settings.load(db_path=":memory:", instance_dir=inst)
    warnings = validate_instance(s)
    assert any("empty" in w.lower() for w in warnings)


def test_healthy_instance(instance_dir: Path) -> None:
    s = Settings.load(db_path=":memory:", instance_dir=instance_dir)
    warnings = validate_instance(s)
    assert warnings == []


def test_embedding_dim_mismatch(tmp_path: Path) -> None:
    inst = tmp_path / "instance"
    inst.mkdir()
    (inst / "identity.toml").write_text(
        '[identity]\nowner_emails = ["a@b.com"]\n', encoding="utf-8"
    )
    (inst / "embedding.toml").write_text(
        "[embedding]\ndim = 384\n", encoding="utf-8"
    )
    s = Settings.load(db_path=":memory:", instance_dir=inst)
    warnings = validate_instance(s)
    assert any("384" in w for w in warnings)
