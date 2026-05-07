"""Tests for the atom @type registry."""

from __future__ import annotations

from pathlib import Path

from phdb.atoms.registry import AtomRegistry, AtomType


def test_registry_loads_canonical_types() -> None:
    reg = AtomRegistry()
    assert reg.validate("EmailMessage")
    assert reg.validate("BookmarkAction")
    assert reg.validate("BefriendAction")
    assert reg.validate("Message")
    assert reg.validate("Dataset")


def test_registry_get_returns_atom() -> None:
    reg = AtomRegistry()
    atom = reg.get("BookmarkAction")
    assert atom is not None
    assert atom.table == "bookmarks"
    assert atom.identity_columns == ("normalized_url", "instrument")
    assert atom.is_canonical is True


def test_registry_unknown_type() -> None:
    reg = AtomRegistry()
    assert not reg.validate("NonExistentType")
    assert reg.get("NonExistentType") is None


def test_register_instance_type() -> None:
    reg = AtomRegistry()
    custom = AtomType(
        name="ChooseAction",
        table="messages",
        is_canonical=False,
        identity_columns=("source_file_id", "raw_hash"),
        description="Decision moments",
    )
    reg.register(custom)
    assert reg.validate("ChooseAction")
    assert reg.get("ChooseAction") is custom
    assert custom in reg.instance_types()
    assert custom not in reg.canonical_types()


def test_load_instance_types_from_toml(tmp_path: Path) -> None:
    atoms_toml = tmp_path / "atoms.toml"
    atoms_toml.write_text(
        '[types.ChooseAction]\n'
        'table = "messages"\n'
        'identity_columns = ["source_file_id", "raw_hash"]\n'
        'description = "Decision moments"\n',
        encoding="utf-8",
    )

    reg = AtomRegistry()
    reg.load_instance_types(atoms_toml)

    atom = reg.get("ChooseAction")
    assert atom is not None
    assert atom.table == "messages"
    assert not atom.is_canonical


def test_canonical_types_list() -> None:
    reg = AtomRegistry()
    canonical = reg.canonical_types()
    names = {t.name for t in canonical}
    assert "EmailMessage" in names
    assert "BookmarkAction" in names
    assert len(canonical) >= 9
