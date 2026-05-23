"""Phase 3 plugin contract tests.

Validates PluginManifest TOML parsing, the PhdbPlugin ABC family's
runtime-validated contract (per Q4 override — ABCs not Protocols),
and the loader's entry-point + in-tree discovery + schema validation.

The Phase 3 deliverable bar is:
- contract documented;
- loader working;
- ``phdb plugin list`` returns [] in a fresh checkout (no plugins
  ported yet);
- manifest schema versioned;
- declared schemas resolve against the phdb.schemas registry.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.core.plugin import (
    MANIFEST_VERSION,
    PhdbFacetPlugin,
    PhdbPlugin,
    PhdbSourcePlugin,
    PluginDescriptor,
    PluginManifest,
    discover_plugins,
    parse_manifest_toml,
)

# ---------------------------------------------------------------------------
# Manifest parsing
# ---------------------------------------------------------------------------


def test_manifest_version_constant():
    assert MANIFEST_VERSION == 1


def test_parse_source_manifest_minimal():
    raw = b"""
[phdb]
manifest_version = 1

[plugin]
name = "raindrop"
version = "0.4.0"
description = "Raindrop.io bookmarks ingester"
kind = "source"
entry_point = "phdb.plugins.raindrop:RaindropPlugin"

[source]
emits = ["BookmarkAction"]
entity_refs = ["web_pages"]
formats_used = ["url"]
"""
    m = parse_manifest_toml(raw)
    assert m.name == "raindrop"
    assert m.version == "0.4.0"
    assert m.kind == "source"
    assert m.entry_point == "phdb.plugins.raindrop:RaindropPlugin"
    assert m.source is not None
    assert m.source.emits == ["BookmarkAction"]
    assert m.source.entity_refs == ["web_pages"]
    assert m.source.formats_used == ["url"]
    assert m.facet is None


def test_parse_facet_manifest_minimal():
    raw = b"""
[phdb]
manifest_version = 1

[plugin]
name = "people"
version = "0.4.0"
description = "People facet - Person identity coalescence"
kind = "facet"
entry_point = "phdb.facets.people:PeopleFacetPlugin"

[facet]
consumes = "Person"
node_table = "persons"
coalescence_rules_path = "identity_rules.toml"
"""
    m = parse_manifest_toml(raw)
    assert m.kind == "facet"
    assert m.facet is not None
    assert m.facet.consumes == "Person"
    assert m.facet.node_table == "persons"
    assert m.facet.coalescence_rules_path == "identity_rules.toml"
    assert m.source is None


def test_parse_manifest_rejects_missing_name():
    raw = b'[plugin]\nentry_point = "x:y"\n'
    with pytest.raises(ValueError, match="name is required"):
        parse_manifest_toml(raw)


def test_parse_manifest_rejects_missing_entry_point():
    raw = b'[plugin]\nname = "x"\n'
    with pytest.raises(ValueError, match="entry_point is required"):
        parse_manifest_toml(raw)


def test_parse_manifest_rejects_bad_kind():
    raw = b'[plugin]\nname = "x"\nentry_point = "x:y"\nkind = "weird"\n'
    with pytest.raises(ValueError, match="kind must be"):
        parse_manifest_toml(raw)


def test_parse_facet_manifest_rejects_missing_consumes():
    raw = b"""
[plugin]
name = "x"
entry_point = "x:y"
kind = "facet"

[facet]
node_table = "x"
"""
    with pytest.raises(ValueError, match="\\[facet\\].consumes"):
        parse_manifest_toml(raw)


# ---------------------------------------------------------------------------
# ABC contract — Q4 override (ABC + dataclass manifest)
# ---------------------------------------------------------------------------


def _example_manifest() -> PluginManifest:
    raw = b"""
[plugin]
name = "test"
version = "0.0.1"
description = "test plugin"
kind = "source"
entry_point = "tests.test_plugin_contract:Dummy"

[source]
emits = []
"""
    return parse_manifest_toml(raw)


def test_phdb_source_plugin_abc_rejects_incomplete_impl():
    """ABC missing methods raises TypeError at instantiation (Q4)."""

    class IncompletePlugin(PhdbSourcePlugin):
        pass  # missing all the @abstractmethod methods

    with pytest.raises(TypeError, match="abstract method"):
        IncompletePlugin(_example_manifest())  # type: ignore[abstract]


def test_phdb_source_plugin_abc_accepts_full_impl():
    """ABC with all methods implemented instantiates cleanly."""

    class CompleteSourcePlugin(PhdbSourcePlugin):
        def discover(self, root):
            return iter([])

        def parse(self, path):
            return iter([])

        def ingest_row(self, conn, record):
            return 0

        def register_cli(self, parser):
            pass

        def register_tools(self, server):
            pass

    p = CompleteSourcePlugin(_example_manifest())
    assert isinstance(p, PhdbPlugin)
    assert p.kind == "source"
    assert p.name == "test"


def test_phdb_facet_plugin_abc_rejects_incomplete_impl():
    """ABC missing methods raises TypeError at instantiation (Q4)."""

    class IncompleteFacetPlugin(PhdbFacetPlugin):
        pass

    with pytest.raises(TypeError, match="abstract method"):
        IncompleteFacetPlugin(_example_manifest())  # type: ignore[abstract]


def test_source_plugin_project_facets_is_optional():
    """Source plugin's project_facets defaults to no-op (not abstract)."""

    class MinimalSourcePlugin(PhdbSourcePlugin):
        def discover(self, root):
            return iter([])

        def parse(self, path):
            return iter([])

        def ingest_row(self, conn, record):
            return 0

        def register_cli(self, parser):
            pass

        def register_tools(self, server):
            pass

    p = MinimalSourcePlugin(_example_manifest())
    # No exception; returns None
    assert p.project_facets(emission_bus=object(), record=object()) is None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def test_discover_plugins_finds_raindrop_after_phase_5():
    """Phase 5 ported raindrop; loader finds it via the in-tree scan."""
    descriptors = discover_plugins()
    names = {d.name for d in descriptors}
    assert "raindrop" in names, (
        f"expected raindrop plugin in discovery; got {names}"
    )
    raindrop = next(d for d in descriptors if d.name == "raindrop")
    assert raindrop.manifest.kind == "source"
    # Phase 5 manifest declares emits = ["BookmarkAction"] — must resolve
    # against the schemas registry without issues.
    assert raindrop.issues == []


def test_loader_validates_emits_against_schemas_registry(tmp_path: Path):
    """A manifest declaring an unknown @type emits a validation issue."""
    # Construct an in-tree plugin directory with a manifest that declares
    # an unknown schema, then run the loader against that root.
    plugin_root = tmp_path / "phdb_plugins_test"
    plugin_root.mkdir()
    (plugin_root / "__init__.py").write_text("")
    raindrop_dir = plugin_root / "fakething"
    raindrop_dir.mkdir()
    (raindrop_dir / "plugin.toml").write_text(
        '[plugin]\nname = "fakething"\nversion = "0.0.1"\ndescription = "test"\n'
        'kind = "source"\nentry_point = "fakething:Plugin"\n\n'
        '[source]\nemits = ["NotARealSchemaType"]\n'
    )

    # Manually invoke the loader's in-tree scanner with this path
    import sys

    sys.path.insert(0, str(tmp_path))
    try:
        from phdb.core.plugin.loader import _in_tree_descriptors, _validate_descriptors

        descriptors = _in_tree_descriptors(["phdb_plugins_test"])
        validated = _validate_descriptors(descriptors)
        assert len(validated) == 1
        assert any("NotARealSchemaType" in i for i in validated[0].issues)
    finally:
        sys.path.remove(str(tmp_path))


def test_plugin_descriptor_is_dataclass_with_expected_fields():
    """Smoke check on the PluginDescriptor shape."""
    m = _example_manifest()
    d = PluginDescriptor(
        name="x",
        distribution=None,
        entry_point_value="x:y",
        manifest=m,
    )
    assert d.name == "x"
    assert d.source == "entry_point"
    assert d.issues == []
