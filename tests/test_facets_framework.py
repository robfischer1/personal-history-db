"""Phase 4 facets plugin framework tests.

Validates the EmissionBus dispatch + the 5 first-party facet plugins
discovering, validating, and loading from src/phdb/facets/<name>/.

Phase 4 deliverable bar:
- 5 facet plugins load via entry-point group phdb.facets;
- phdb plugin list shows them with kind: facet;
- bus dispatches emissions to subscribed plugins;
- the 5 facet-plugin MCP tools register and return empty results
  (skeleton — Phase 5+ fills in).
"""

from __future__ import annotations

from phdb.core.plugin import (
    EmissionBus,
    FacetEmission,
    discover_facets,
    load_plugin,
)
from phdb.facets.base import (
    AUDIT_LOG_DDL,
    SkeletonFacetPlugin,
    ensure_audit_log,
)
from phdb.facets.people import PeopleFacetPlugin
from phdb.facets.places import PlacesFacetPlugin
from phdb.facets.threads import ThreadsFacetPlugin
from phdb.facets.time import TimeFacetPlugin
from phdb.facets.topics import TopicsFacetPlugin


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


def test_discover_facets_finds_all_five_first_party_plugins():
    descriptors = discover_facets()
    names = {d.name for d in descriptors}
    assert names == {"people", "places", "time", "threads", "topics"}, (
        f"expected 5 first-party facet plugins; got {names}"
    )
    for d in descriptors:
        assert d.manifest.kind == "facet"
        assert d.manifest.facet is not None
        assert d.manifest.facet.consumes
        assert d.manifest.facet.node_table


def test_facet_plugins_load_via_entry_point_value():
    """load_plugin imports each facet plugin and instantiates it."""
    descriptors = discover_facets()
    expected_classes = {
        "people": PeopleFacetPlugin,
        "places": PlacesFacetPlugin,
        "time": TimeFacetPlugin,
        "threads": ThreadsFacetPlugin,
        "topics": TopicsFacetPlugin,
    }
    for d in descriptors:
        plugin = load_plugin(d)
        assert isinstance(plugin, expected_classes[d.name])
        assert plugin.kind == "facet"


# ---------------------------------------------------------------------------
# EmissionBus
# ---------------------------------------------------------------------------


def _make_plugin(consumes: str, node_table: str, plugin_name: str = "test") -> SkeletonFacetPlugin:
    from phdb.core.plugin.manifest import FacetManifestExtras, PluginManifest

    manifest = PluginManifest(
        name=plugin_name,
        version="0.0.1",
        description="test",
        kind="facet",
        entry_point="x:y",
        facet=FacetManifestExtras(consumes=consumes, node_table=node_table),
    )
    return SkeletonFacetPlugin(manifest)


def test_bus_dispatches_to_matching_subscriber():
    bus = EmissionBus()
    person_plugin = _make_plugin("Person", "persons")
    place_plugin = _make_plugin("Place", "places")
    bus.subscribe(person_plugin)
    bus.subscribe(place_plugin)

    delivered = bus.emit(
        source_table="emails",
        source_id=42,
        facet_type="Person",
        payload={"address": "alice@example.com"},
    )

    assert delivered == 1
    assert len(person_plugin.buffer) == 1
    assert len(place_plugin.buffer) == 0
    assert person_plugin.buffer[0].source_table == "emails"
    assert person_plugin.buffer[0].payload["address"] == "alice@example.com"


def test_bus_dispatches_to_multiple_subscribers_of_same_facet_type():
    bus = EmissionBus()
    a = _make_plugin("Person", "persons", plugin_name="a")
    b = _make_plugin("Person", "persons", plugin_name="b")
    bus.subscribe(a)
    bus.subscribe(b)

    delivered = bus.emit(source_table="emails", source_id=1, facet_type="Person")

    assert delivered == 2
    assert len(a.buffer) == 1
    assert len(b.buffer) == 1


def test_bus_dispatches_zero_when_no_subscriber():
    bus = EmissionBus()
    delivered = bus.dispatch(
        FacetEmission(source_table="emails", source_id=1, facet_type="Person", payload={})
    )
    assert delivered == 0


def test_skeleton_facet_plugin_buffer_round_trip():
    plugin = _make_plugin("Person", "persons")
    for i in range(3):
        plugin.consume(FacetEmission(
            source_table="emails", source_id=i, facet_type="Person", payload={},
        ))
    summary = plugin.coalesce()
    assert summary["buffered_emissions"] == 3
    assert summary["facet_type"] == "Person"
    assert summary["node_table"] == "persons"


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------


def test_ensure_audit_log_is_idempotent():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    # Run twice — second call must not error
    ensure_audit_log(conn)
    ensure_audit_log(conn)

    rows = conn.execute("PRAGMA table_info(facet_coalescence_log)").fetchall()
    col_names = {r[1] for r in rows}
    assert {
        "id", "facet_type", "facet_node_id", "rule_name", "confidence",
        "source_table", "source_id", "payload", "created_at",
    } <= col_names


def test_audit_log_ddl_constants_exist():
    """The DDL text constants are well-formed (basic sanity)."""
    assert "CREATE TABLE" in AUDIT_LOG_DDL
    assert "facet_coalescence_log" in AUDIT_LOG_DDL


# ---------------------------------------------------------------------------
# End-to-end: source plugin → bus → facet plugin (Phase 4 dry run)
# ---------------------------------------------------------------------------


def test_end_to_end_emission_to_loaded_facet_plugin():
    """A loaded PeopleFacetPlugin buffers an emission delivered via the bus."""
    descriptors = discover_facets()
    people_desc = next(d for d in descriptors if d.name == "people")
    people_plugin = load_plugin(people_desc)
    assert isinstance(people_plugin, PeopleFacetPlugin)

    bus = EmissionBus()
    bus.subscribe(people_plugin)

    delivered = bus.emit(
        source_table="emails",
        source_id=1,
        facet_type="Person",
        payload={"address": "alice@example.com", "display_name": "Alice"},
    )
    assert delivered == 1
    assert len(people_plugin.buffer) == 1
    assert people_plugin.buffer[0].payload["display_name"] == "Alice"

    summary = people_plugin.coalesce()
    assert summary["buffered_emissions"] == 1
    assert summary["facet_type"] == "Person"
