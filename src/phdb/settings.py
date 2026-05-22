"""Three-tier settings: code defaults -> instance TOML -> environment variables.

The merge order (lowest to highest priority):
  1. Hardcoded defaults in the Pydantic models below
  2. Instance TOML files in instance_dir/ (paths.toml, identity.toml, etc.)
  3. Environment variables with PHDB_ prefix

Instance config is optional — the framework works with defaults alone for
testing and fresh-adopter setup. Your instance config lives in
personal-history-instance/ (separate from this project repo).
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

from phdb.identity import IdentitySettings


class EmbeddingSettings(BaseModel):
    """Embedding model configuration."""

    model: str = "nomic-embed-text"
    dim: int = 768
    endpoint: str = "http://localhost:11434"


def _discover_instance_dir() -> Path | None:
    """Walk up from cwd looking for a personal-history-instance/ dir or .phdbrc marker."""
    current = Path.cwd().resolve()
    while True:
        candidate = current / "personal-history-instance"
        if candidate.is_dir():
            return candidate
        marker = current / ".phdbrc"
        if marker.is_file():
            text = marker.read_text(encoding="utf-8").strip()
            if text:
                p = Path(text)
                if not p.is_absolute():
                    p = current / p
                if p.is_dir():
                    return p
        parent = current.parent
        if parent == current:
            return None
        current = parent


def _load_instance_toml(instance_dir: Path | None) -> dict[str, Any]:
    """Merge all .toml files in instance_dir into a single dict."""
    if not instance_dir or not instance_dir.is_dir():
        return {}

    merged: dict[str, Any] = {}
    for toml_file in sorted(instance_dir.glob("*.toml")):
        with open(toml_file, "rb") as f:
            data = tomllib.load(f)
        for key, value in data.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
    return merged


class Settings(BaseSettings):
    """Root settings for personal-history-db."""

    model_config = SettingsConfigDict(
        env_prefix="PHDB_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    db_path: Path = Path("personal-history.db")
    instance_dir: Path | None = None
    data_dir: Path | None = None
    adapter_paths: list[Path] = []
    log_level: str = "INFO"

    embedding: EmbeddingSettings = EmbeddingSettings()
    identity: IdentitySettings = IdentitySettings()

    @classmethod
    def load(
        cls,
        *,
        db_path: Path | str | None = None,
        instance_dir: Path | str | None = None,
    ) -> Settings:
        """Load settings with three-tier merge.

        Explicit arguments override everything. Then env vars. Then instance TOML.
        Then code defaults.
        """
        inst_dir = Path(instance_dir) if instance_dir else None

        if inst_dir is None:
            env_inst = os.environ.get("PHDB_INSTANCE_DIR")
            if env_inst:
                inst_dir = Path(env_inst)
            else:
                inst_dir = _discover_instance_dir()

        toml_data = _load_instance_toml(inst_dir)

        overrides: dict[str, Any] = {}
        if db_path is not None:
            overrides["db_path"] = str(db_path)
        if inst_dir is not None:
            overrides["instance_dir"] = str(inst_dir)

        merged = {**toml_data, **overrides}
        return cls(**merged)
