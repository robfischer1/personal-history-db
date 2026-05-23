"""Tests for Phase 8B — places facet coalescence over shared coalescence lib.

Covers:
- Places rule loading from TOML + from in-memory dicts.
- Haversine geo-radius predicate at boundary distances (just inside,
  just outside, exactly on the radius — defining behavior).
- Named-location-exact predicate with normalization edge cases.
- ``_enrich_emission`` derives lat_rounded / lon_rounded /
  normalized_name / country_code.
- PlacesCoalescer.coalesce_batch produces MergeProposals.
- End-to-end via coalesce_buffer_to_db: insert 3 places (two within
  radius, one outside) + emit → expect one auto-merge proposal
  grouping the two close-coord places (after threshold tuning).
- PlacesFacetPlugin.coalesce(connection=...) returns the expected
  summary shape.
- Dry-run mode (no connection) returns proposals without DB writes.
- Bundled-defaults fallback when no instance TOML present.
"""

from __future__ import annotations

import math
import sqlite3
from pathlib import Path

import pytest

from phdb.core.plugin import EmissionBus, discover_facets, load_plugin
from phdb.core.plugin.bus import FacetEmission
from phdb.db import connect
from phdb.facets._coalescence_lib import (
    Coalescer,
    CoalescenceRule,
    MergeProposal,
)
from phdb.facets.places import PlacesFacetPlugin
from phdb.facets.places.coalescence import (
    AUTO_MERGE_THRESHOLD,
    DEFAULT_PLACES_RULES,
    CoalesceSummary,
    PlacesCoalescer,
    _enrich_emission,
    _haversine_meters,
    build_places_predicate,
    coalesce_buffer_to_db,
    load_places_rules_from_dicts,
    load_places_rules_from_toml,
)
from phdb.migrations.runner import MigrationRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit(facet_type: str = "Place", source_id: int = 0, **payload):
    return FacetEmission(
        source_table="test", source_id=source_id,
        facet_type=facet_type, payload=payload,
    )


@pytest.fixture
def migrated_conn(tmp_path: Path):
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    conn = sqlite3.connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Haversine math — independent of predicates
# ---------------------------------------------------------------------------


class TestHaversine:
    def test_identical_coords_zero_distance(self):
        d = _haversine_meters(40.7128, -74.0060, 40.7128, -74.0060)
        assert d == 0.0

    def test_known_distance_nyc_to_la(self):
        # NYC (40.7128, -74.0060) to LA (34.0522, -118.2437) ≈ 3,936 km
        # (per online great-circle calculators).
        d = _haversine_meters(40.7128, -74.0060, 34.0522, -118.2437)
        # Within 1% of the canonical value.
        assert 3_900_000 < d < 3_970_000

    def test_one_degree_latitude_approx_111_km(self):
        # 1° of latitude ≈ 111.195 km using mean Earth radius (6371000m).
        d = _haversine_meters(0.0, 0.0, 1.0, 0.0)
        assert 110_000 < d < 112_000

    def test_symmetric(self):
        a = _haversine_meters(40.0, -74.0, 41.0, -73.0)
        b = _haversine_meters(41.0, -73.0, 40.0, -74.0)
        assert math.isclose(a, b, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# Geo-radius predicate — boundary behavior
# ---------------------------------------------------------------------------


class TestGeoRadiusPredicate:
    def test_matches_within_radius(self):
        # Two points ~50m apart, 100m radius rule.
        pred = build_places_predicate({
            "shape": "geo_radius_meters", "radius_m": 100,
        })
        a = _emit(lat=40.7128, lon=-74.0060)
        # ~50m north (1m lat ≈ 1/111195°)
        b = _emit(lat=40.7128 + (50.0 / 111195.0), lon=-74.0060)
        assert pred(a, b) is True

    def test_no_match_outside_radius(self):
        # ~150m apart, 100m radius rule — outside.
        pred = build_places_predicate({
            "shape": "geo_radius_meters", "radius_m": 100,
        })
        a = _emit(lat=40.7128, lon=-74.0060)
        b = _emit(lat=40.7128 + (150.0 / 111195.0), lon=-74.0060)
        assert pred(a, b) is False

    def test_match_just_inside_100m(self):
        # 99.9m apart, 100m radius — must match.
        pred = build_places_predicate({
            "shape": "geo_radius_meters", "radius_m": 100,
        })
        a = _emit(lat=40.7128, lon=-74.0060)
        b = _emit(lat=40.7128 + (99.9 / 111195.0), lon=-74.0060)
        assert pred(a, b) is True

    def test_no_match_just_outside_100m(self):
        # 100.1m apart, 100m radius — must not match.
        pred = build_places_predicate({
            "shape": "geo_radius_meters", "radius_m": 100,
        })
        a = _emit(lat=40.7128, lon=-74.0060)
        b = _emit(lat=40.7128 + (100.1 / 111195.0), lon=-74.0060)
        assert pred(a, b) is False

    def test_exactly_on_boundary_matches(self):
        # Construct two points whose great-circle distance is
        # *exactly* 100m, then assert the predicate counts them as
        # inside (boundary policy: <= radius_m).
        pred = build_places_predicate({
            "shape": "geo_radius_meters", "radius_m": 100,
        })
        a_lat, a_lon = 40.7128, -74.0060
        # Find a small lat delta that produces exactly 100m via the
        # function we're testing (round-trips the same constants).
        b_lat = a_lat + (100.0 / 111195.0)
        actual_distance = _haversine_meters(a_lat, a_lon, b_lat, a_lon)
        # Make the rule radius = the actual distance — guaranteed boundary.
        pred_boundary = build_places_predicate({
            "shape": "geo_radius_meters", "radius_m": actual_distance,
        })
        a = _emit(lat=a_lat, lon=a_lon)
        b = _emit(lat=b_lat, lon=a_lon)
        assert pred_boundary(a, b) is True
        # And a hair under the radius rejects it.
        pred_strict = build_places_predicate({
            "shape": "geo_radius_meters", "radius_m": actual_distance - 0.001,
        })
        assert pred_strict(a, b) is False

    def test_missing_lat_returns_false(self):
        pred = build_places_predicate({
            "shape": "geo_radius_meters", "radius_m": 100,
        })
        a = _emit(lat=None, lon=-74.0)
        b = _emit(lat=40.7, lon=-74.0)
        assert pred(a, b) is False

    def test_missing_lon_returns_false(self):
        pred = build_places_predicate({
            "shape": "geo_radius_meters", "radius_m": 100,
        })
        a = _emit(lat=40.7, lon=None)
        b = _emit(lat=40.7, lon=-74.0)
        assert pred(a, b) is False

    def test_both_missing_returns_false(self):
        pred = build_places_predicate({
            "shape": "geo_radius_meters", "radius_m": 100,
        })
        a = _emit()
        b = _emit()
        assert pred(a, b) is False

    def test_string_coord_coerced(self):
        pred = build_places_predicate({
            "shape": "geo_radius_meters", "radius_m": 100,
        })
        a = _emit(lat="40.7128", lon="-74.0060")
        b = _emit(lat="40.7128", lon="-74.0060")
        assert pred(a, b) is True

    def test_custom_lat_lon_fields(self):
        pred = build_places_predicate({
            "shape": "geo_radius_meters",
            "radius_m": 100,
            "lat_field": "latitude",
            "lon_field": "longitude",
        })
        a = _emit(latitude=40.7128, longitude=-74.0060)
        b = _emit(latitude=40.7128, longitude=-74.0060)
        assert pred(a, b) is True


# ---------------------------------------------------------------------------
# Named-location predicate — normalization
# ---------------------------------------------------------------------------


class TestNamedLocationPredicate:
    def test_matches_exact(self):
        pred = build_places_predicate({
            "shape": "named_location_exact",
            "field": "name",
        })
        a = _emit(name="Central Park")
        b = _emit(name="Central Park")
        assert pred(a, b) is True

    def test_matches_case_insensitive(self):
        pred = build_places_predicate({
            "shape": "named_location_exact",
            "field": "name",
            "normalize": "lowercase_strip",
        })
        a = _emit(name="Central Park")
        b = _emit(name="central park")
        assert pred(a, b) is True

    def test_collapses_internal_whitespace(self):
        # "lowercase_strip" specifically collapses runs of whitespace.
        pred = build_places_predicate({
            "shape": "named_location_exact",
            "field": "name",
            "normalize": "lowercase_strip",
        })
        a = _emit(name="Central  Park")  # double space
        b = _emit(name="Central Park")
        assert pred(a, b) is True

    def test_strips_leading_trailing_whitespace(self):
        pred = build_places_predicate({
            "shape": "named_location_exact",
            "field": "name",
            "normalize": "lowercase_strip",
        })
        a = _emit(name="  Central Park  ")
        b = _emit(name="central park")
        assert pred(a, b) is True

    def test_no_match_different_names(self):
        pred = build_places_predicate({
            "shape": "named_location_exact",
            "field": "name",
        })
        a = _emit(name="Central Park")
        b = _emit(name="Bryant Park")
        assert pred(a, b) is False

    def test_none_does_not_match(self):
        pred = build_places_predicate({
            "shape": "named_location_exact",
            "field": "name",
        })
        a = _emit(name=None)
        b = _emit(name=None)
        assert pred(a, b) is False

    def test_empty_string_does_not_match(self):
        pred = build_places_predicate({
            "shape": "named_location_exact",
            "field": "name",
            "normalize": "lowercase_strip",
        })
        a = _emit(name="")
        b = _emit(name="")
        assert pred(a, b) is False


# ---------------------------------------------------------------------------
# Rule loading
# ---------------------------------------------------------------------------


class TestRuleLoading:
    def test_load_from_dicts_constructs_rules(self):
        rules = load_places_rules_from_dicts(DEFAULT_PLACES_RULES)
        assert len(rules) == 3
        names = {r.name for r in rules}
        assert "geo_within_100m" in names
        assert "geo_within_500m" in names
        assert "named_location_exact" in names

    def test_default_rules_have_valid_confidence(self):
        rules = load_places_rules_from_dicts(DEFAULT_PLACES_RULES)
        for r in rules:
            assert 0.0 <= r.confidence <= 1.0

    def test_default_rules_have_known_shapes(self):
        rules = load_places_rules_from_dicts(DEFAULT_PLACES_RULES)
        for r in rules:
            assert r.shape in {
                "geo_radius_meters", "named_location_exact",
                "exact_field", "two_field", "regex", "named_location",
            }

    def test_default_500m_rule_requires_manual_review(self):
        rules = load_places_rules_from_dicts(DEFAULT_PLACES_RULES)
        wide = next(r for r in rules if r.name == "geo_within_500m")
        assert wide.require_manual_review is True

    def test_load_from_toml(self, tmp_path: Path):
        toml_path = tmp_path / "rules.toml"
        toml_path.write_text("""
[[rules.places]]
name = "tight"
shape = "geo_radius_meters"
radius_m = 50
confidence = 0.95
""", encoding="utf-8")
        rules = load_places_rules_from_toml(toml_path)
        assert len(rules) == 1
        assert rules[0].name == "tight"
        assert rules[0].shape == "geo_radius_meters"
        # The loaded predicate is the real one (not the placeholder).
        a = _emit(lat=40.7, lon=-74.0)
        b = _emit(lat=40.7, lon=-74.0)
        assert rules[0].predicate(a, b) is True

    def test_load_from_missing_toml_returns_empty(self, tmp_path: Path):
        rules = load_places_rules_from_toml(tmp_path / "nonexistent.toml")
        assert rules == []

    def test_toml_geo_rule_predicate_is_real_haversine(self, tmp_path: Path):
        # Regression: shared lib's loader gives a no-op predicate for
        # geo_radius_meters. The places loader must override that.
        toml_path = tmp_path / "rules.toml"
        toml_path.write_text("""
[[rules.places]]
name = "geo_100m"
shape = "geo_radius_meters"
radius_m = 100
confidence = 0.9
""", encoding="utf-8")
        rules = load_places_rules_from_toml(toml_path)
        assert len(rules) == 1
        # Within radius → must match (placeholder would return False).
        a = _emit(lat=40.7128, lon=-74.0060)
        b = _emit(lat=40.7128, lon=-74.0060)
        assert rules[0].predicate(a, b) is True


# ---------------------------------------------------------------------------
# Enrichment
# ---------------------------------------------------------------------------


class TestEnrichEmission:
    def test_derives_lat_rounded(self):
        e = _enrich_emission(_emit(lat=40.71285, lon=-74.00601))
        assert e.payload["lat_rounded"] == 40.7129  # rounded to 4 places

    def test_derives_lon_rounded(self):
        e = _enrich_emission(_emit(lat=40.7128, lon=-74.006012))
        assert e.payload["lon_rounded"] == -74.0060

    def test_derives_normalized_name(self):
        e = _enrich_emission(_emit(name="  Central  Park  "))
        assert e.payload["normalized_name"] == "central park"

    def test_derives_country_code_from_country(self):
        e = _enrich_emission(_emit(country="US"))
        assert e.payload["country_code"] == "us"

    def test_passes_through_existing_country_code(self):
        e = _enrich_emission(_emit(country_code="DE", country="Germany"))
        assert e.payload["country_code"] == "de"

    def test_does_not_mutate_input_payload(self):
        original = _emit(lat=40.7, lon=-74.0, name="X")
        original_payload_snapshot = dict(original.payload)
        _enrich_emission(original)
        assert original.payload == original_payload_snapshot

    def test_handles_missing_fields_gracefully(self):
        e = _enrich_emission(_emit())
        assert "lat_rounded" not in e.payload
        assert "lon_rounded" not in e.payload
        assert "normalized_name" not in e.payload

    def test_dict_path(self):
        d = _enrich_emission({"lat": 40.7128, "lon": -74.006, "name": "Park"})
        assert d["lat_rounded"] == 40.7128
        assert d["normalized_name"] == "park"


# ---------------------------------------------------------------------------
# PlacesCoalescer — proposal generation
# ---------------------------------------------------------------------------


class TestPlacesCoalescer:
    def test_no_proposals_for_singletons(self):
        coalescer = PlacesCoalescer(
            rules=load_places_rules_from_dicts(DEFAULT_PLACES_RULES)
        )
        proposals = coalescer.coalesce_batch([_emit(lat=40.7, lon=-74.0)])
        assert proposals == []

    def test_proposal_for_geo_match(self):
        coalescer = PlacesCoalescer(
            rules=load_places_rules_from_dicts(DEFAULT_PLACES_RULES)
        )
        # Two points ~10m apart — definitely within 100m radius.
        emissions = [
            _emit(lat=40.7128, lon=-74.0060, source_id=1),
            _emit(lat=40.71289, lon=-74.0060, source_id=2),  # ~10m north
        ]
        proposals = coalescer.coalesce_batch(emissions)
        assert len(proposals) == 1
        # Should fire the tightest matching rule — geo_within_100m (highest conf).
        assert proposals[0].rule == "geo_within_100m"
        assert proposals[0].confidence == 0.85

    def test_proposal_for_named_match(self):
        coalescer = PlacesCoalescer(
            rules=load_places_rules_from_dicts(DEFAULT_PLACES_RULES)
        )
        emissions = [
            _emit(name="Central Park", source_id=1),
            _emit(name="CENTRAL PARK", source_id=2),
        ]
        proposals = coalescer.coalesce_batch(emissions)
        assert len(proposals) == 1
        assert proposals[0].rule == "named_location_exact"

    def test_highest_confidence_rule_wins(self):
        # Geo + name both fire; geo_within_100m (0.85) > named (0.80).
        coalescer = PlacesCoalescer(
            rules=load_places_rules_from_dicts(DEFAULT_PLACES_RULES)
        )
        emissions = [
            _emit(lat=40.7128, lon=-74.0060, name="Central Park", source_id=1),
            _emit(lat=40.7128, lon=-74.0060, name="Central Park", source_id=2),
        ]
        proposals = coalescer.coalesce_batch(emissions)
        assert len(proposals) == 1
        assert proposals[0].confidence == 0.85

    def test_far_points_no_proposal(self):
        coalescer = PlacesCoalescer(
            rules=load_places_rules_from_dicts(DEFAULT_PLACES_RULES)
        )
        # NYC vs LA — way outside any default radius.
        emissions = [
            _emit(lat=40.7128, lon=-74.0060, source_id=1),
            _emit(lat=34.0522, lon=-118.2437, source_id=2),
        ]
        proposals = coalescer.coalesce_batch(emissions)
        assert proposals == []

    def test_three_emissions_two_close_one_far(self):
        # Two within 50m (proposal); third 200km away (alone).
        coalescer = PlacesCoalescer(
            rules=load_places_rules_from_dicts(DEFAULT_PLACES_RULES)
        )
        emissions = [
            _emit(lat=40.7128, lon=-74.0060, source_id=1),
            _emit(lat=40.71285, lon=-74.0060, source_id=2),  # ~5m
            _emit(lat=42.0, lon=-74.0, source_id=3),  # ~140km north
        ]
        proposals = coalescer.coalesce_batch(emissions)
        assert len(proposals) == 1
        # Both close emissions belong to that one proposal.
        assert len(proposals[0].from_emissions) == 2


# ---------------------------------------------------------------------------
# End-to-end — coalesce_buffer_to_db
# ---------------------------------------------------------------------------


class TestPlacesCoalesceBufferToDb:
    def test_three_places_two_within_radius(
        self, migrated_conn: sqlite3.Connection,
    ):
        """Insert 3 places (two close, one far) and emit; auto-merge expected
        only when the firing rule is at-or-above the auto-merge threshold.

        With the bundled defaults the strongest geo rule is 0.85 — below
        the 0.90 threshold — so this run hits the pending-review buffer
        rather than auto-merging. Pass a low threshold to exercise the
        auto-merge path.
        """
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/timeline.json', 'json', 'google-timeline')"
        )
        for pk in ("p1", "p2", "p3"):
            migrated_conn.execute(
                "INSERT INTO places (id, schema_type, place_key, raw_hash, source_file_id)"
                " VALUES (?, 'Place', ?, ?, 1)",
                (
                    {"p1": 100, "p2": 101, "p3": 102}[pk],
                    pk,
                    f"hash-{pk}",
                ),
            )
        migrated_conn.commit()

        emissions = [
            FacetEmission(
                source_table="places", source_id=100,
                facet_type="Place",
                payload={"id": 100, "lat": 40.7128, "lon": -74.0060,
                         "name": "Central Park"},
            ),
            FacetEmission(
                source_table="places", source_id=101,
                facet_type="Place",
                payload={"id": 101, "lat": 40.71285, "lon": -74.0060,
                         "name": "Central Park"},  # ~5m + same name
            ),
            FacetEmission(
                source_table="places", source_id=102,
                facet_type="Place",
                payload={"id": 102, "lat": 42.0, "lon": -74.0,
                         "name": "Elsewhere"},
            ),
        ]
        summary, pending = coalesce_buffer_to_db(
            migrated_conn, emissions,
            auto_merge_threshold=0.80,  # tune below default 0.90
        )
        assert summary.emissions_processed == 3
        assert summary.proposals_generated == 1
        assert summary.auto_merged == 1
        assert summary.pending_review == 0
        assert pending == []
        # Row 101 should be merged into 100 (smallest id survives).
        remaining = {r[0] for r in migrated_conn.execute(
            "SELECT id FROM places WHERE id IN (100, 101, 102)"
        ).fetchall()}
        assert remaining == {100, 102}

    def test_default_threshold_buffers_for_review(
        self, migrated_conn: sqlite3.Connection,
    ):
        """At the default 0.90 threshold, even the 100m rule (0.85) is below
        auto-merge, so the proposal lands in pending_review."""
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/x.json', 'json', 'google-timeline')"
        )
        for pid, pk in [(200, "a"), (201, "b")]:
            migrated_conn.execute(
                "INSERT INTO places (id, schema_type, place_key, raw_hash, source_file_id)"
                " VALUES (?, 'Place', ?, ?, 1)",
                (pid, pk, f"h-{pid}"),
            )
        migrated_conn.commit()

        emissions = [
            FacetEmission(
                source_table="places", source_id=200,
                facet_type="Place",
                payload={"id": 200, "lat": 40.7128, "lon": -74.0060},
            ),
            FacetEmission(
                source_table="places", source_id=201,
                facet_type="Place",
                payload={"id": 201, "lat": 40.7128, "lon": -74.0060},
            ),
        ]
        summary, pending = coalesce_buffer_to_db(migrated_conn, emissions)
        assert summary.proposals_generated == 1
        assert summary.auto_merged == 0
        assert summary.pending_review == 1
        assert len(pending) == 1
        # Both rows still present.
        assert migrated_conn.execute(
            "SELECT COUNT(*) FROM places WHERE id IN (200, 201)"
        ).fetchone()[0] == 2

    def test_500m_rule_requires_manual_review(
        self, migrated_conn: sqlite3.Connection,
    ):
        """The 500m rule has require_manual_review=true; even when an
        operator drops auto-threshold below its 0.65 confidence, it
        still routes to pending_review."""
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/x.json', 'json', 'google-timeline')"
        )
        for pid, pk in [(300, "a"), (301, "b")]:
            migrated_conn.execute(
                "INSERT INTO places (id, schema_type, place_key, raw_hash, source_file_id)"
                " VALUES (?, 'Place', ?, ?, 1)",
                (pid, pk, f"h-{pid}"),
            )
        migrated_conn.commit()

        # 300m apart — within 500m but outside 100m.
        emissions = [
            FacetEmission(
                source_table="places", source_id=300,
                facet_type="Place",
                payload={"id": 300, "lat": 40.7128, "lon": -74.0060},
            ),
            FacetEmission(
                source_table="places", source_id=301,
                facet_type="Place",
                payload={"id": 301,
                         "lat": 40.7128 + (300.0 / 111195.0),
                         "lon": -74.0060},
            ),
        ]
        summary, pending = coalesce_buffer_to_db(
            migrated_conn, emissions, auto_merge_threshold=0.5,
        )
        assert summary.proposals_generated == 1
        # require_manual_review wins regardless of threshold.
        assert summary.auto_merged == 0
        assert summary.pending_review == 1
        assert len(pending) == 1

    def test_uses_instance_rules_when_present(
        self, migrated_conn: sqlite3.Connection, tmp_path: Path,
    ):
        # Instance file overrides bundled defaults.
        (tmp_path / "identity_rules.toml").write_text("""
[[rules.places]]
name = "instance_tight"
shape = "geo_radius_meters"
radius_m = 50
confidence = 0.99
""", encoding="utf-8")
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/x.json', 'json', 'google-timeline')"
        )
        for pid, pk in [(400, "a"), (401, "b")]:
            migrated_conn.execute(
                "INSERT INTO places (id, schema_type, place_key, raw_hash, source_file_id)"
                " VALUES (?, 'Place', ?, ?, 1)",
                (pid, pk, f"h-{pid}"),
            )
        migrated_conn.commit()
        emissions = [
            FacetEmission(
                source_table="places", source_id=400,
                facet_type="Place",
                payload={"id": 400, "lat": 40.7128, "lon": -74.0060},
            ),
            FacetEmission(
                source_table="places", source_id=401,
                facet_type="Place",
                payload={"id": 401, "lat": 40.7128, "lon": -74.0060},
            ),
        ]
        summary, _ = coalesce_buffer_to_db(
            migrated_conn, emissions, instance_dir=tmp_path,
        )
        assert summary.source.startswith("instance:")
        assert summary.rules_loaded == 1
        # 0.99 confidence — auto-merges by default 0.90 threshold.
        assert summary.auto_merged == 1

    def test_falls_back_to_bundled_when_no_instance_file(
        self, migrated_conn: sqlite3.Connection, tmp_path: Path,
    ):
        emissions: list[FacetEmission] = []
        summary, _ = coalesce_buffer_to_db(
            migrated_conn, emissions, instance_dir=tmp_path,
        )
        assert summary.source == "bundled-defaults"
        assert summary.rules_loaded == len(DEFAULT_PLACES_RULES)


# ---------------------------------------------------------------------------
# PlacesFacetPlugin — plugged into the bus, real coalesce()
# ---------------------------------------------------------------------------


class TestPlacesFacetPluginEndToEnd:
    def test_dry_run_without_connection(self):
        descriptors = discover_facets()
        places_desc = next(d for d in descriptors if d.name == "places")
        plugin = load_plugin(places_desc)
        assert isinstance(plugin, PlacesFacetPlugin)

        bus = EmissionBus()
        bus.subscribe(plugin)
        bus.emit(source_table="places", source_id=1, facet_type="Place",
                 payload={"id": 1, "lat": 40.7128, "lon": -74.0060})
        bus.emit(source_table="places", source_id=2, facet_type="Place",
                 payload={"id": 2, "lat": 40.7128, "lon": -74.0060})

        summary = plugin.coalesce()
        # Dry-run: counts proposals but doesn't write.
        assert summary["dry_run"] is True
        assert summary["emissions_processed"] == 2
        assert summary["proposals_generated"] == 1
        assert summary["audit_entries_written"] == 0
        # At default 0.90 threshold the 0.85 rule doesn't auto-merge.
        assert summary["would_auto_merge"] == 0
        assert summary["pending_review"] == 1
        assert summary["status"].startswith("phase-8b-coalescer")

    def test_live_mode_writes_audit(self, migrated_conn: sqlite3.Connection):
        descriptors = discover_facets()
        places_desc = next(d for d in descriptors if d.name == "places")
        plugin = load_plugin(places_desc)

        # Seed places rows.
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/x.json', 'json', 'google-timeline')"
        )
        migrated_conn.execute(
            "INSERT INTO places (id, schema_type, place_key, raw_hash, source_file_id)"
            " VALUES (500, 'Place', 'a', 'h500', 1), (501, 'Place', 'b', 'h501', 1)"
        )
        migrated_conn.commit()

        bus = EmissionBus()
        bus.subscribe(plugin)
        bus.emit(
            source_table="places", source_id=500, facet_type="Place",
            payload={"id": 500, "lat": 40.7128, "lon": -74.0060},
        )
        bus.emit(
            source_table="places", source_id=501, facet_type="Place",
            payload={"id": 501, "lat": 40.7128, "lon": -74.0060},
        )

        # Drop the threshold so the bundled 0.85 rule auto-merges.
        summary = plugin.coalesce(
            connection=migrated_conn, auto_merge_threshold=0.80,
        )
        assert summary["auto_merged"] == 1
        assert summary["audit_entries_written"] == 1
        assert summary["rules_loaded"] == len(DEFAULT_PLACES_RULES)
        assert summary["status"] == "phase-8b-coalescer"
        # Buffer drained.
        assert plugin.buffer == []
        # Audit log has a Place-type entry.
        audit_rows = migrated_conn.execute(
            "SELECT facet_type FROM facet_coalescence_log"
        ).fetchall()
        assert any(r[0] == "Place" for r in audit_rows)

    def test_summary_shape_has_required_keys(
        self, migrated_conn: sqlite3.Connection,
    ):
        descriptors = discover_facets()
        places_desc = next(d for d in descriptors if d.name == "places")
        plugin = load_plugin(places_desc)

        summary = plugin.coalesce(connection=migrated_conn)
        for key in (
            "emissions_processed",
            "auto_merged",
            "pending_review",
            "audit_entries_written",
        ):
            assert key in summary, f"missing key {key!r}"


# ---------------------------------------------------------------------------
# CoalesceSummary serialization
# ---------------------------------------------------------------------------


class TestCoalesceSummary:
    def test_to_dict_round_trip(self):
        s = CoalesceSummary(
            emissions_processed=10,
            proposals_generated=3,
            auto_merged=2,
            pending_review=1,
            audit_entries_written=2,
            rules_loaded=3,
            source="bundled-defaults",
        )
        d = s.to_dict()
        assert d["emissions_processed"] == 10
        assert d["auto_merged"] == 2
        assert d["pending_review"] == 1
        assert d["audit_entries_written"] == 2
        assert d["rules_source"] == "bundled-defaults"


# ---------------------------------------------------------------------------
# Bundled defaults sanity
# ---------------------------------------------------------------------------


class TestBundledDefaults:
    def test_threshold_split_works(self):
        # All bundled places rules are below AUTO_MERGE_THRESHOLD (0.90) —
        # the operator is expected to tune per instance.
        rules = load_places_rules_from_dicts(DEFAULT_PLACES_RULES)
        above = [r for r in rules if r.confidence >= AUTO_MERGE_THRESHOLD]
        assert above == []
        # And the 500m rule requires manual review regardless.
        manual = [r for r in rules if r.require_manual_review]
        assert "geo_within_500m" in {r.name for r in manual}
