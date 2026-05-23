"""Tests for the ``phdb plugin scaffold`` command + ``scaffold_plugin`` API.

Phase 9 of the phdb Plugin Architecture plan. Covers:

- valid scaffold produces all four files at the expected paths;
- name validation rejects bad slugs;
- ``--emits`` with an unknown @type errors out with a helpful list
  unless ``--force`` is passed;
- existing plugin dir refuses to overwrite without ``--force``;
- ``--force`` overwrites an existing plugin dir;
- generated ``plugin.toml`` parses cleanly via the manifest loader;
- generated plugin is found by ``discover_plugins()`` with zero issues;
- a scaffold mirroring raindrop's manifest produces the same shape.

Tests that scaffold under tmp_path don't need cleanup. The discovery
test scaffolds into the real ``src/phdb/plugins/`` tree (so the loader
can find it) and removes the directory in a try/finally.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from phdb.cli import cli
from phdb.core.plugin import discover_plugins, parse_manifest_toml
from phdb.core.plugin.scaffold import (
    ScaffoldError,
    default_plugins_root,
    scaffold_plugin,
    to_class_name,
)

# ---------------------------------------------------------------------------
# Direct ``scaffold_plugin`` API tests (use tmp_path — no real-tree writes)
# ---------------------------------------------------------------------------


def test_scaffold_produces_four_files(tmp_path: Path) -> None:
    """A valid scaffold writes plugin.toml + plugin.py + ingest.py + __init__.py."""
    result = scaffold_plugin(
        "myplug",
        description="Test plugin",
        plugins_root=tmp_path,
    )
    assert result.plugin_dir == tmp_path / "myplug"
    assert result.manifest_path.is_file()
    assert result.plugin_py.is_file()
    assert result.ingest_py.is_file()
    assert result.init_py.is_file()
    # File names are the canonical four.
    names = {p.name for p in result.all_paths()}
    assert names == {"plugin.toml", "plugin.py", "ingest.py", "__init__.py"}


def test_scaffold_rejects_invalid_name(tmp_path: Path) -> None:
    """Names with uppercase / spaces / leading digits fail validation."""
    for bad in ("MyPlug", "my plug", "9plug", "my-plug", "", "_plug"):
        with pytest.raises(ScaffoldError, match="invalid plugin name"):
            scaffold_plugin(bad, plugins_root=tmp_path)


def test_scaffold_rejects_unknown_emit_without_force(tmp_path: Path) -> None:
    """An unknown @type in --emits errors out + the message lists known types."""
    with pytest.raises(ScaffoldError) as exc_info:
        scaffold_plugin(
            "myplug",
            emits=["NotARealSchemaType"],
            plugins_root=tmp_path,
        )
    msg = str(exc_info.value)
    assert "NotARealSchemaType" in msg
    # The error message must list at least one known @type so the user
    # can see what was expected.
    assert "BookmarkAction" in msg or "WebPage" in msg


def test_scaffold_with_force_overrides_unknown_emit(tmp_path: Path) -> None:
    """--force scaffolds even with an unknown @type (escape hatch)."""
    result = scaffold_plugin(
        "myplug",
        emits=["NotARealSchemaType"],
        force=True,
        plugins_root=tmp_path,
    )
    assert result.plugin_dir.is_dir()


def test_scaffold_refuses_existing_dir(tmp_path: Path) -> None:
    """Existing plugin dir errors out without --force."""
    scaffold_plugin("myplug", plugins_root=tmp_path)
    with pytest.raises(ScaffoldError, match="already exists"):
        scaffold_plugin("myplug", plugins_root=tmp_path)


def test_scaffold_force_overwrites_existing_dir(tmp_path: Path) -> None:
    """--force replaces an existing plugin dir."""
    scaffold_plugin("myplug", description="First", plugins_root=tmp_path)
    # Drop a sentinel file inside the dir to confirm wipe.
    (tmp_path / "myplug" / "sentinel.txt").write_text("old", encoding="utf-8")
    scaffold_plugin(
        "myplug",
        description="Second",
        force=True,
        plugins_root=tmp_path,
    )
    assert not (tmp_path / "myplug" / "sentinel.txt").exists()
    # Manifest reflects the second scaffold.
    manifest_text = (tmp_path / "myplug" / "plugin.toml").read_text(encoding="utf-8")
    assert 'description = "Second"' in manifest_text


def test_generated_manifest_parses_cleanly(tmp_path: Path) -> None:
    """The generated plugin.toml round-trips through parse_manifest_toml."""
    result = scaffold_plugin(
        "myplug",
        description="Round-trip test",
        emits=["BookmarkAction"],
        entity_refs=["web_pages"],
        formats_used=["url"],
        facets_projected=["Time"],
        plugins_root=tmp_path,
    )
    raw = result.manifest_path.read_bytes()
    manifest = parse_manifest_toml(raw)
    assert manifest.name == "myplug"
    assert manifest.kind == "source"
    assert manifest.entry_point == "phdb.plugins.myplug:MyplugPlugin"
    assert manifest.source is not None
    assert manifest.source.emits == ["BookmarkAction"]
    assert manifest.source.entity_refs == ["web_pages"]
    assert manifest.source.formats_used == ["url"]
    assert manifest.source.facets_projected == ["Time"]


def test_scaffold_mirrors_raindrop_shape(tmp_path: Path) -> None:
    """Generated manifest with raindrop's flags matches raindrop's manifest shape.

    Excludes version (raindrop is at 0.4.0; scaffold lands at 0.1.0) and the
    description string (free-text).
    """
    result = scaffold_plugin(
        "raindrop_scaffold_test",
        emits=["BookmarkAction"],
        entity_refs=["web_pages"],
        formats_used=["url"],
        plugins_root=tmp_path,
    )
    generated = parse_manifest_toml(result.manifest_path.read_bytes())
    raindrop_root = default_plugins_root() / "raindrop"
    raindrop = parse_manifest_toml((raindrop_root / "plugin.toml").read_bytes())

    # Shape parity — name + version aside, the structural fields match.
    assert generated.kind == raindrop.kind
    assert generated.manifest_version == raindrop.manifest_version
    assert generated.source is not None and raindrop.source is not None
    assert generated.source.emits == raindrop.source.emits
    assert generated.source.entity_refs == raindrop.source.entity_refs
    assert generated.source.formats_used == raindrop.source.formats_used[:1]
    # entry_point follows the same template (phdb.plugins.<name>:<Name>Plugin)
    assert generated.entry_point.startswith("phdb.plugins.raindrop_scaffold_test:")


def test_to_class_name_handles_snake_case() -> None:
    """Snake-case names PascalCase + Plugin suffix."""
    assert to_class_name("raindrop") == "RaindropPlugin"
    assert to_class_name("apple_dbs") == "AppleDbsPlugin"
    assert to_class_name("call_log_2") == "CallLog2Plugin"


# ---------------------------------------------------------------------------
# CLI surface tests (Click runner)
# ---------------------------------------------------------------------------


def test_cli_plugin_scaffold_help() -> None:
    runner = CliRunner()
    result = runner.invoke(cli, ["plugin", "scaffold", "--help"])
    assert result.exit_code == 0
    assert "--emits" in result.output
    assert "--entity-refs" in result.output
    assert "--formats-used" in result.output
    assert "--facets-projected" in result.output
    assert "--force" in result.output


def test_cli_plugin_scaffold_lists_known_types_on_bad_emit(tmp_path: Path) -> None:
    """The CLI surfaces the ScaffoldError with the @type list."""
    # Use the standalone function — the CLI scaffold writes to the real
    # tree, but a bad emit fails before any write, so no cleanup needed.
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["plugin", "scaffold", "myplug_cli_bad_emit", "--emits", "NotARealSchemaType"],
    )
    assert result.exit_code == 1
    # Click writes to stderr; CliRunner combines into result.output by default.
    combined = result.output + (result.stderr if hasattr(result, "stderr") else "")
    assert "NotARealSchemaType" in combined
    assert "Error" in combined


# ---------------------------------------------------------------------------
# Discovery integration test — writes into the real plugins root + cleans up
# ---------------------------------------------------------------------------


def test_generated_plugin_is_discoverable() -> None:
    """A scaffolded plugin lands in discover_plugins() with zero issues."""
    test_name = "scaffolded_discovery_test"
    plugins_root = default_plugins_root()
    target = plugins_root / test_name
    if target.exists():
        shutil.rmtree(target)

    try:
        scaffold_plugin(
            test_name,
            description="Discovery integration test plugin",
            emits=["BookmarkAction"],
            entity_refs=["web_pages"],
            formats_used=["url"],
        )

        # The loader walks phdb.plugins.__path__ live; no need to flush
        # importlib caches because the scan reads the filesystem, not
        # already-imported submodules.
        descriptors = discover_plugins()
        found = next((d for d in descriptors if d.name == test_name), None)
        assert found is not None, (
            f"scaffolded plugin {test_name!r} not found in discover_plugins(); "
            f"got names: {sorted(d.name for d in descriptors)}"
        )
        assert found.issues == [], (
            f"scaffolded plugin had validation issues: {found.issues}"
        )
        assert found.manifest.kind == "source"
        assert found.manifest.source is not None
        assert found.manifest.source.emits == ["BookmarkAction"]
    finally:
        if target.exists():
            shutil.rmtree(target)
        # Drop any cached submodule import so re-scaffolds in the same
        # process don't see stale code.
        for mod in list(sys.modules):
            if mod.startswith(f"phdb.plugins.{test_name}"):
                del sys.modules[mod]
