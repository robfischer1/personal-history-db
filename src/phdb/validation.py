"""Instance config validation.

Checks for common configuration issues at startup. Returns warnings
(never aborts) so the framework degrades gracefully when instance
config is missing or incomplete.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from phdb.settings import Settings

VECTOR_DIM_DEFAULT = 768


def validate_instance(settings: Settings) -> list[str]:
    """Check instance config for common issues. Returns warning messages."""
    warnings: list[str] = []

    if not settings.instance_dir:
        return warnings

    inst = Path(settings.instance_dir)

    if not inst.is_dir():
        warnings.append(f"Instance dir does not exist: {inst}")
        return warnings

    if not (inst / "identity.toml").is_file():
        warnings.append(
            "No identity.toml in instance dir; direction inference will not work"
        )

    identity = settings.identity
    if not (
        identity.owner_names
        or identity.owner_emails
        or identity.owner_phones
        or identity.owner_handles
    ):
        warnings.append(
            "Identity config is empty; direction inference will not work"
        )

    if settings.embedding.dim != VECTOR_DIM_DEFAULT:
        warnings.append(
            f"Embedding dim {settings.embedding.dim} differs from "
            f"project default {VECTOR_DIM_DEFAULT}"
        )

    return warnings
