"""Owner identity model for direction inference and PII filtering.

The identity module is optional — when no identity.toml exists in the
instance directory, all fields default to empty and the framework
degrades gracefully:
  - Direction inference returns "unknown" for all messages
  - PII filtering has nothing to redact
  - owner_sender() falls back to "owner" as the name

Populate identity.toml in your instance directory to enable these features.
"""

from __future__ import annotations

from pydantic import BaseModel


class IdentitySettings(BaseModel):
    """Owner identity for direction inference.

    In the project tier this is empty. The instance tier populates it
    with the actual owner's addresses, phones, name variants, and
    platform handles.
    """

    owner_names: set[str] = set()
    owner_emails: set[str] = set()
    owner_phones: set[str] = set()
    owner_handles: dict[str, set[str]] = {}

    @property
    def is_configured(self) -> bool:
        """True if any identity field is populated."""
        return bool(
            self.owner_names
            or self.owner_emails
            or self.owner_phones
            or self.owner_handles
        )

    def is_me(self, address: str) -> bool:
        """Check if an address belongs to the database owner."""
        addr = address.strip().lower()
        if ":" in addr:
            platform, handle = addr.split(":", 1)
            platform_handles = self.owner_handles.get(platform, set())
            if handle in platform_handles:
                return True
        return addr in self.owner_emails or addr in self.owner_phones or addr in self.owner_names

    def pii_literals(self) -> list[str]:
        """Return all identity strings suitable for PII redaction."""
        literals: list[str] = list(
            self.owner_names | self.owner_emails | self.owner_phones
        )
        for handles in self.owner_handles.values():
            literals.extend(handles)
        return literals
