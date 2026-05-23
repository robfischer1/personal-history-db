"""Atom @type registry.

Tracks known Schema.org @types, their target tables, and identity columns.
Project ships canonical types; instance can extend via atoms.toml.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AtomType:
    """A registered Schema.org @type."""

    name: str
    table: str
    is_canonical: bool = True
    identity_columns: tuple[str, ...] = ()
    description: str = ""


_CANONICAL_TYPES: list[AtomType] = [
    AtomType("Dataset", "source_files", identity_columns=("source_path",)),
    AtomType("EmailMessage", "emails", identity_columns=("rfc822_message_id",)),
    AtomType("Message", "chat_messages", identity_columns=("source_file_id", "raw_hash")),
    AtomType("Conversation", "threads", identity_columns=("source_kind", "thread_key")),
    AtomType(
        "DigitalDocument",
        "digital_documents",
        identity_columns=("source_file_id", "raw_hash"),
        description="Attachments metadata or document-shaped messages",
    ),
    AtomType(
        "BookmarkAction",
        "bookmarks",
        identity_columns=("web_page_id", "instrument"),
    ),
    AtomType(
        "BefriendAction",
        "connections",
        identity_columns=("dedupe_key", "instrument"),
    ),
    AtomType("CreativeWork", "creative_works", identity_columns=("source_file_id", "raw_hash")),
    AtomType("ListenAction", "listen_actions", identity_columns=("source_file_id", "raw_hash")),
]


@dataclass
class AtomRegistry:
    """Registry of known @types with table and identity mappings."""

    _types: dict[str, AtomType] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for atom in _CANONICAL_TYPES:
            self._types[atom.name] = atom

    def register(self, atom: AtomType) -> None:
        """Register a new @type. Instance types override canonical ones."""
        self._types[atom.name] = atom

    def get(self, name: str) -> AtomType | None:
        """Look up an @type by name."""
        return self._types.get(name)

    def validate(self, name: str) -> bool:
        """Check if an @type name is registered."""
        return name in self._types

    def canonical_types(self) -> list[AtomType]:
        """Return only project-canonical types."""
        return [t for t in self._types.values() if t.is_canonical]

    def instance_types(self) -> list[AtomType]:
        """Return only instance-specific types."""
        return [t for t in self._types.values() if not t.is_canonical]

    def all_types(self) -> list[AtomType]:
        """Return all registered types."""
        return list(self._types.values())

    def load_instance_types(self, atoms_toml: Path) -> None:
        """Extend registry from an instance atoms.toml file.

        Expected TOML format:
            [types.MyCustomType]
            table = "chat_messages"
            identity_columns = ["source_file_id", "raw_hash"]
            description = "Optional description"
        """
        if not atoms_toml.is_file():
            return

        with open(atoms_toml, "rb") as f:
            data = tomllib.load(f)

        for name, config in data.get("types", {}).items():
            atom = AtomType(
                name=name,
                table=config["table"],
                is_canonical=False,
                identity_columns=tuple(config.get("identity_columns", [])),
                description=config.get("description", ""),
            )
            self.register(atom)
