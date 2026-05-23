"""Tests for Phase 8A — people facet coalescence + shared coalescence lib.

Covers:
- Rule loading from TOML + from in-memory dicts.
- Each rule shape (exact_field, two_field, regex, geo_radius_meters
  placeholder, named_location).
- Predicate construction + evaluation.
- Coalescer.coalesce_batch produces MergeProposals.
- DB-write half: apply_merge updates FK columns + writes audit log.
- unmerge reverses an audit entry.
- Audit log table is created by migration 0029.
- ensure_audit_log is a no-op when the table already exists.
- PeopleFacetPlugin end-to-end: ingest, coalesce, auto-merge.
- Bundled-defaults fallback when no instance TOML present.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from phdb.core.plugin import EmissionBus, discover_facets, load_plugin
from phdb.core.plugin.bus import FacetEmission
from phdb.db import connect
from phdb.facets._coalescence_lib import (
    AuditEntry,
    Coalescer,
    CoalescenceRule,
    MergeProposal,
    apply_merge,
    build_predicate,
    load_rules_from_dicts,
    load_rules_from_toml,
    unmerge,
    write_audit_entry,
)
from phdb.facets.base import ensure_audit_log
from phdb.facets.people import PeopleFacetPlugin
from phdb.facets.people.coalescence import (
    AUTO_MERGE_THRESHOLD,
    DEFAULT_PEOPLE_RULES,
    CoalesceSummary,
    PeopleCoalescer,
    coalesce_buffer_to_db,
)
from phdb.migrations.runner import MigrationRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _emit(facet_type: str = "Person", source_id: int = 0, **payload):
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
# Rule loading
# ---------------------------------------------------------------------------


class TestRuleLoading:
    def test_load_from_dicts_constructs_rules(self):
        rules = load_rules_from_dicts(DEFAULT_PEOPLE_RULES)
        assert len(rules) == 5
        names = {r.name for r in rules}
        assert "exact_email_local_domain" in names
        assert "phone_e164" in names
        assert "discord_handle" in names
        assert "same_full_name_same_email_domain" in names
        assert "same_first_last" in names

    def test_default_rules_have_required_confidence(self):
        rules = load_rules_from_dicts(DEFAULT_PEOPLE_RULES)
        for r in rules:
            assert 0.0 <= r.confidence <= 1.0
            assert r.shape in {
                "exact_field", "two_field", "regex",
                "geo_radius_meters", "named_location",
            }

    def test_load_from_toml_file(self, tmp_path: Path):
        toml_path = tmp_path / "rules.toml"
        toml_path.write_text("""
[[rules.people]]
name = "test_email"
shape = "exact_field"
field = "email"
normalize = "lowercase"
confidence = 0.99
""", encoding="utf-8")
        rules = load_rules_from_toml(toml_path, facet="people")
        assert len(rules) == 1
        assert rules[0].name == "test_email"
        assert rules[0].confidence == 0.99

    def test_load_from_missing_toml_returns_empty(self, tmp_path: Path):
        rules = load_rules_from_toml(tmp_path / "nonexistent.toml")
        assert rules == []

    def test_load_from_toml_filters_by_facet(self, tmp_path: Path):
        toml_path = tmp_path / "rules.toml"
        toml_path.write_text("""
[[rules.people]]
name = "p1"
shape = "exact_field"
field = "email"
confidence = 0.9

[[rules.places]]
name = "pl1"
shape = "named_location"
field = "name"
confidence = 0.8
""", encoding="utf-8")
        people = load_rules_from_toml(toml_path, facet="people")
        places = load_rules_from_toml(toml_path, facet="places")
        assert [r.name for r in people] == ["p1"]
        assert [r.name for r in places] == ["pl1"]

    def test_unknown_shape_raises(self):
        with pytest.raises(ValueError, match="unknown rule shape"):
            build_predicate({"shape": "no_such_shape"})


# ---------------------------------------------------------------------------
# Predicate behavior — each rule shape
# ---------------------------------------------------------------------------


class TestExactFieldPredicate:
    def test_matches_exact_field(self):
        pred = build_predicate({
            "shape": "exact_field", "field": "email",
        })
        a = _emit(email="alice@example.com")
        b = _emit(email="alice@example.com")
        assert pred(a, b) is True

    def test_no_match_different_values(self):
        pred = build_predicate({
            "shape": "exact_field", "field": "email",
        })
        a = _emit(email="alice@example.com")
        b = _emit(email="bob@example.com")
        assert pred(a, b) is False

    def test_lowercase_normalize(self):
        pred = build_predicate({
            "shape": "exact_field", "field": "email",
            "normalize": "lowercase",
        })
        a = _emit(email="Alice@Example.COM")
        b = _emit(email="alice@example.com")
        assert pred(a, b) is True

    def test_e164_normalize(self):
        pred = build_predicate({
            "shape": "exact_field", "field": "phone",
            "normalize": "e164",
        })
        a = _emit(phone="(973) 768-4297")
        b = _emit(phone="+19737684297")
        c = _emit(phone="973-768-4297")
        assert pred(a, b) is True
        assert pred(a, c) is True
        assert pred(b, c) is True

    def test_none_value_does_not_match(self):
        pred = build_predicate({
            "shape": "exact_field", "field": "email",
        })
        a = _emit(email=None)
        b = _emit(email=None)
        assert pred(a, b) is False

    def test_empty_string_does_not_match(self):
        pred = build_predicate({
            "shape": "exact_field", "field": "email",
            "normalize": "lowercase",
        })
        a = _emit(email="")
        b = _emit(email="")
        assert pred(a, b) is False


class TestTwoFieldPredicate:
    def test_matches_when_both_fields_match(self):
        pred = build_predicate({
            "shape": "two_field",
            "fields": ["first_name", "last_name"],
            "normalize": "lowercase",
        })
        a = _emit(first_name="Alice", last_name="Smith")
        b = _emit(first_name="alice", last_name="SMITH")
        assert pred(a, b) is True

    def test_no_match_when_one_field_differs(self):
        pred = build_predicate({
            "shape": "two_field",
            "fields": ["first_name", "last_name"],
        })
        a = _emit(first_name="Alice", last_name="Smith")
        b = _emit(first_name="Alice", last_name="Jones")
        assert pred(a, b) is False


class TestRegexPredicate:
    def test_matches_captured_group(self):
        pred = build_predicate({
            "shape": "regex",
            "field": "email",
            "pattern": r"^([^@]+)@",
        })
        a = _emit(email="rob@example.com")
        b = _emit(email="rob@other-domain.com")
        assert pred(a, b) is True

    def test_no_match_different_capture(self):
        pred = build_predicate({
            "shape": "regex",
            "field": "email",
            "pattern": r"^([^@]+)@",
        })
        a = _emit(email="rob@example.com")
        b = _emit(email="alice@example.com")
        assert pred(a, b) is False


class TestGeoRadiusPlaceholder:
    def test_placeholder_returns_false(self):
        pred = build_predicate({
            "shape": "geo_radius_meters",
            "radius_meters": 50.0,
        })
        a = _emit(lat=40.7, lon=-74.0)
        b = _emit(lat=40.7, lon=-74.0)
        # Phase 8A: placeholder. Real impl ships in places facet (8B).
        assert pred(a, b) is False


class TestNamedLocationPredicate:
    def test_matches_same_normalized_name(self):
        pred = build_predicate({
            "shape": "named_location",
            "field": "name",
            "normalize": "lowercase",
        })
        a = _emit(name="Central Park")
        b = _emit(name="central park")
        assert pred(a, b) is True


# ---------------------------------------------------------------------------
# Coalescer — proposal generation
# ---------------------------------------------------------------------------


class TestCoalescer:
    def test_no_proposals_for_singletons(self):
        coalescer = Coalescer(rules=load_rules_from_dicts(DEFAULT_PEOPLE_RULES))
        proposals = coalescer.coalesce_batch([_emit(email="a@x.com")])
        assert proposals == []

    def test_proposal_for_matching_pair(self):
        coalescer = Coalescer(rules=load_rules_from_dicts(DEFAULT_PEOPLE_RULES))
        emissions = [
            _emit(email="alice@example.com", source_id=1),
            _emit(email="ALICE@example.com", source_id=2),
        ]
        proposals = coalescer.coalesce_batch(emissions)
        assert len(proposals) == 1
        assert proposals[0].rule == "exact_email_local_domain"
        assert proposals[0].confidence == 0.95

    def test_highest_confidence_rule_wins(self):
        # Two rules fire on this pair: phone_e164 (0.95) +
        # same_full_name_same_email_domain (0.75). Highest wins.
        coalescer = PeopleCoalescer(
            rules=load_rules_from_dicts(DEFAULT_PEOPLE_RULES)
        )
        emissions = [
            _emit(
                phone="+19735551212",
                full_name="Alice Jones",
                email="alice@x.com",
                source_id=1,
            ),
            _emit(
                phone="(973) 555-1212",
                full_name="alice jones",
                email="alice@x.com",
                source_id=2,
            ),
        ]
        proposals = coalescer.coalesce_batch(emissions)
        assert len(proposals) == 1
        # Multiple rules fire; the highest-confidence one wins (0.95).
        assert proposals[0].confidence == 0.95

    def test_evaluate_emission_returns_candidates(self):
        coalescer = Coalescer(rules=load_rules_from_dicts(DEFAULT_PEOPLE_RULES))
        target = _emit(email="alice@example.com", source_id=10)
        candidates = [
            _emit(email="bob@example.com", source_id=1),
            _emit(email="alice@example.com", source_id=2),
            _emit(email="ALICE@EXAMPLE.com", source_id=3),
        ]
        matches = coalescer.evaluate_emission(target, candidates)
        # 2 of 3 candidates match (ids 2 + 3).
        assert len(matches) == 2

    def test_threshold_filters_low_confidence(self):
        coalescer = Coalescer(
            rules=load_rules_from_dicts(DEFAULT_PEOPLE_RULES),
            threshold=0.90,
        )
        emissions = [
            _emit(first_name="Alice", last_name="Smith", source_id=1),
            _emit(first_name="alice", last_name="smith", source_id=2),
        ]
        # same_first_last is 0.40 — filtered.
        proposals = coalescer.coalesce_batch(emissions)
        assert proposals == []

    def test_no_proposal_when_only_existing_node_matches_alone(self):
        coalescer = Coalescer(rules=load_rules_from_dicts(DEFAULT_PEOPLE_RULES))
        # Existing alone — no emissions to merge.
        proposals = coalescer.coalesce_batch(
            [],
            existing_nodes=[{"id": 1, "email": "x@y.com"}],
        )
        assert proposals == []


class TestPeopleCoalescerEnrichment:
    def test_email_domain_derived_from_email(self):
        coalescer = PeopleCoalescer(
            rules=load_rules_from_dicts(DEFAULT_PEOPLE_RULES)
        )
        # same_full_name_same_email_domain (0.75) requires email_domain;
        # we only pass email — enrichment must derive it.
        emissions = [
            _emit(email="alice@example.com", full_name="Alice X", source_id=1),
            _emit(email="alice2@example.com", full_name="Alice X", source_id=2),
        ]
        proposals = coalescer.coalesce_batch(emissions)
        # Should match on full_name + derived email_domain.
        assert len(proposals) == 1
        assert proposals[0].rule == "same_full_name_same_email_domain"

    def test_first_last_derived_from_full_name(self):
        coalescer = PeopleCoalescer(
            rules=load_rules_from_dicts(DEFAULT_PEOPLE_RULES)
        )
        emissions = [
            _emit(full_name="Alice Smith", source_id=1),
            _emit(full_name="ALICE SMITH", source_id=2),
        ]
        proposals = coalescer.coalesce_batch(emissions)
        assert len(proposals) == 1
        assert proposals[0].rule == "same_first_last"


# ---------------------------------------------------------------------------
# DB-write half — apply_merge + unmerge + audit
# ---------------------------------------------------------------------------


class TestAuditLog:
    def test_migration_0029_creates_table(self, migrated_conn: sqlite3.Connection):
        tables = {r[0] for r in migrated_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        assert "facet_coalescence_log" in tables

    def test_migration_0029_recorded(self, migrated_conn: sqlite3.Connection):
        row = migrated_conn.execute(
            "SELECT 1 FROM schema_migrations WHERE migration_id=?",
            ("0029_facet_coalescence_log",),
        ).fetchone()
        assert row is not None

    def test_migration_0029_creates_indexes(self, migrated_conn: sqlite3.Connection):
        idx_names = {r[0] for r in migrated_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' "
            "AND tbl_name='facet_coalescence_log'"
        ).fetchall()}
        assert "idx_facet_coalescence_log_facet" in idx_names
        assert "idx_facet_coalescence_log_created" in idx_names
        assert "idx_facet_coalescence_log_rule" in idx_names

    def test_ensure_audit_log_idempotent_on_migrated_db(
        self, migrated_conn: sqlite3.Connection,
    ):
        # Table already exists via migration — ensure_audit_log must not error.
        ensure_audit_log(migrated_conn)
        ensure_audit_log(migrated_conn)
        rows = migrated_conn.execute(
            "PRAGMA table_info(facet_coalescence_log)"
        ).fetchall()
        # Migration version has 9 columns; legacy version had 9 too — same shape.
        assert len(rows) == 9

    def test_ensure_audit_log_fallback_on_fresh_memory_db(self):
        conn = sqlite3.connect(":memory:")
        ensure_audit_log(conn)
        rows = conn.execute(
            "PRAGMA table_info(facet_coalescence_log)"
        ).fetchall()
        col_names = {r[1] for r in rows}
        assert {"id", "facet_type", "facet_node_id", "rule_name"} <= col_names

    def test_write_audit_entry_appends_row(self, migrated_conn: sqlite3.Connection):
        entry = AuditEntry(
            facet_type="Person",
            facet_node_id=42,
            rule_name="exact_email_local_domain",
            confidence=0.95,
            payload={"foo": "bar"},
        )
        rid = write_audit_entry(migrated_conn, entry)
        assert rid > 0
        row = migrated_conn.execute(
            "SELECT facet_type, facet_node_id, rule_name, confidence, payload "
            "FROM facet_coalescence_log WHERE id = ?",
            (rid,),
        ).fetchone()
        assert row[0] == "Person"
        assert row[1] == 42
        assert row[2] == "exact_email_local_domain"
        assert row[3] == 0.95
        assert "foo" in row[4]


class TestApplyMergeAndUnmerge:
    def test_apply_merge_collapses_persons_rows(self, migrated_conn: sqlite3.Connection):
        # Seed two persons rows; merge them.
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/test.vcf', 'vcf', 'google-contacts')"
        )
        migrated_conn.execute(
            "INSERT INTO persons (id, schema_type, person_key, sender_name, raw_hash, source_file_id)"
            " VALUES (1, 'Person', 'alice-1', 'Alice Smith', 'h1', 1)"
        )
        migrated_conn.execute(
            "INSERT INTO persons (id, schema_type, person_key, sender_name, raw_hash, source_file_id)"
            " VALUES (2, 'Person', 'alice-2', 'Alice Smith', 'h2', 1)"
        )
        migrated_conn.commit()

        proposal = MergeProposal(
            into_node_id=-1,
            from_emissions=[{"id": 1}, {"id": 2}],
            rule="exact_email_local_domain",
            confidence=0.95,
        )
        survivor = apply_merge(
            migrated_conn,
            node_table="persons",
            proposal=proposal,
            facet_type="Person",
        )
        assert survivor == 1
        # Row 2 deleted; row 1 survives.
        remaining = {r[0] for r in migrated_conn.execute(
            "SELECT id FROM persons WHERE id IN (1, 2)"
        ).fetchall()}
        assert remaining == {1}

    def test_apply_merge_rewrites_fk_references(self, migrated_conn: sqlite3.Connection):
        # Seed two persons + a dependent row holding an FK.
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/test.vcf', 'vcf', 'google-contacts')"
        )
        migrated_conn.execute(
            "INSERT INTO persons (id, schema_type, person_key, raw_hash, source_file_id)"
            " VALUES (10, 'Person', 'a', 'h10', 1), (11, 'Person', 'b', 'h11', 1)"
        )
        # Synthetic dependent FK table for the test.
        migrated_conn.execute(
            "CREATE TABLE dep (id INTEGER PRIMARY KEY, person_id INTEGER, label TEXT)"
        )
        migrated_conn.execute(
            "INSERT INTO dep (person_id, label) VALUES (10, 'x'), (11, 'y')"
        )
        migrated_conn.commit()

        proposal = MergeProposal(
            into_node_id=-1,
            from_emissions=[{"id": 10}, {"id": 11}],
            rule="exact_email_local_domain",
            confidence=0.95,
        )
        survivor = apply_merge(
            migrated_conn,
            node_table="persons",
            proposal=proposal,
            facet_type="Person",
            fk_columns=[("dep", "person_id")],
        )
        assert survivor == 10
        # Both dep rows now point at the survivor.
        fks = {r[0] for r in migrated_conn.execute(
            "SELECT person_id FROM dep"
        ).fetchall()}
        assert fks == {10}

    def test_apply_merge_writes_audit_entry(self, migrated_conn: sqlite3.Connection):
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/test.vcf', 'vcf', 'google-contacts')"
        )
        migrated_conn.execute(
            "INSERT INTO persons (id, schema_type, person_key, raw_hash, source_file_id)"
            " VALUES (20, 'Person', 'a', 'h20', 1), (21, 'Person', 'b', 'h21', 1)"
        )
        migrated_conn.commit()

        proposal = MergeProposal(
            into_node_id=-1,
            from_emissions=[{"id": 20}, {"id": 21}],
            rule="phone_e164",
            confidence=0.95,
        )
        apply_merge(
            migrated_conn,
            node_table="persons",
            proposal=proposal,
            facet_type="Person",
        )
        audit_rows = migrated_conn.execute(
            "SELECT facet_type, facet_node_id, rule_name FROM facet_coalescence_log"
        ).fetchall()
        assert len(audit_rows) == 1
        assert audit_rows[0][0] == "Person"
        assert audit_rows[0][1] == 20
        assert audit_rows[0][2] == "phone_e164"

    def test_unmerge_restores_rows(self, migrated_conn: sqlite3.Connection):
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/test.vcf', 'vcf', 'google-contacts')"
        )
        migrated_conn.execute(
            "INSERT INTO persons (id, schema_type, person_key, raw_hash, source_file_id)"
            " VALUES (30, 'Person', 'a', 'h30', 1), (31, 'Person', 'b', 'h31', 1)"
        )
        migrated_conn.commit()

        proposal = MergeProposal(
            into_node_id=-1,
            from_emissions=[{"id": 30}, {"id": 31}],
            rule="exact_email_local_domain",
            confidence=0.95,
        )
        apply_merge(
            migrated_conn,
            node_table="persons",
            proposal=proposal,
            facet_type="Person",
        )

        # Verify merge happened.
        assert migrated_conn.execute(
            "SELECT COUNT(*) FROM persons WHERE id IN (30, 31)"
        ).fetchone()[0] == 1

        audit_id = migrated_conn.execute(
            "SELECT id FROM facet_coalescence_log ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]

        summary = unmerge(migrated_conn, "persons", audit_id)
        assert summary["restored_count"] == 1
        # Both rows restored.
        assert migrated_conn.execute(
            "SELECT COUNT(*) FROM persons WHERE id IN (30, 31)"
        ).fetchone()[0] == 2
        # Audit row gone.
        assert migrated_conn.execute(
            "SELECT COUNT(*) FROM facet_coalescence_log WHERE id = ?",
            (audit_id,),
        ).fetchone()[0] == 0

    def test_unmerge_unknown_audit_id_raises(self, migrated_conn: sqlite3.Connection):
        with pytest.raises(ValueError, match="no audit entry"):
            unmerge(migrated_conn, "persons", 99999)

    def test_unmerge_wrong_table_raises(self, migrated_conn: sqlite3.Connection):
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/test.vcf', 'vcf', 'google-contacts')"
        )
        migrated_conn.execute(
            "INSERT INTO persons (id, schema_type, person_key, raw_hash, source_file_id)"
            " VALUES (40, 'Person', 'a', 'h40', 1), (41, 'Person', 'b', 'h41', 1)"
        )
        migrated_conn.commit()
        proposal = MergeProposal(
            into_node_id=-1,
            from_emissions=[{"id": 40}, {"id": 41}],
            rule="x",
            confidence=0.95,
        )
        apply_merge(
            migrated_conn,
            node_table="persons",
            proposal=proposal,
            facet_type="Person",
        )
        audit_id = migrated_conn.execute(
            "SELECT MAX(id) FROM facet_coalescence_log"
        ).fetchone()[0]
        with pytest.raises(ValueError, match="for table"):
            unmerge(migrated_conn, "places", audit_id)


# ---------------------------------------------------------------------------
# End-to-end — PeopleFacetPlugin coalesce_buffer_to_db
# ---------------------------------------------------------------------------


class TestPeopleCoalesceBufferToDb:
    def test_high_confidence_proposal_auto_merges(self, migrated_conn: sqlite3.Connection):
        # Seed two existing persons rows that emissions reference.
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/test.vcf', 'vcf', 'google-contacts')"
        )
        migrated_conn.execute(
            "INSERT INTO persons (id, schema_type, person_key, raw_hash, source_file_id)"
            " VALUES (100, 'Person', 'a', 'h100', 1), (101, 'Person', 'b', 'h101', 1)"
        )
        migrated_conn.commit()

        # Emissions that look up the same email — but tagged with the
        # row ids the source plugin already inserted.
        emissions = [
            FacetEmission(
                source_table="persons", source_id=100,
                facet_type="Person",
                payload={"id": 100, "email": "alice@example.com"},
            ),
            FacetEmission(
                source_table="persons", source_id=101,
                facet_type="Person",
                payload={"id": 101, "email": "ALICE@EXAMPLE.COM"},
            ),
        ]
        summary, pending = coalesce_buffer_to_db(migrated_conn, emissions)
        assert summary.emissions_processed == 2
        assert summary.proposals_generated == 1
        assert summary.auto_merged == 1
        assert summary.pending_review == 0
        assert pending == []
        # Row 101 should be gone.
        remaining = {r[0] for r in migrated_conn.execute(
            "SELECT id FROM persons WHERE id IN (100, 101)"
        ).fetchall()}
        assert remaining == {100}

    def test_low_confidence_buffered_for_review(self, migrated_conn: sqlite3.Connection):
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/test.vcf', 'vcf', 'google-contacts')"
        )
        migrated_conn.execute(
            "INSERT INTO persons (id, schema_type, person_key, raw_hash, source_file_id)"
            " VALUES (200, 'Person', 'a', 'h200', 1), (201, 'Person', 'b', 'h201', 1)"
        )
        migrated_conn.commit()

        emissions = [
            FacetEmission(
                source_table="persons", source_id=200,
                facet_type="Person",
                payload={"id": 200, "full_name": "Alice Jones"},
            ),
            FacetEmission(
                source_table="persons", source_id=201,
                facet_type="Person",
                payload={"id": 201, "full_name": "alice jones"},
            ),
        ]
        summary, pending = coalesce_buffer_to_db(migrated_conn, emissions)
        # same_first_last fires (0.40 + require_manual_review).
        assert summary.proposals_generated == 1
        assert summary.auto_merged == 0
        assert summary.pending_review == 1
        assert len(pending) == 1
        # Both rows still present.
        assert migrated_conn.execute(
            "SELECT COUNT(*) FROM persons WHERE id IN (200, 201)"
        ).fetchone()[0] == 2

    def test_uses_instance_rules_when_present(
        self, migrated_conn: sqlite3.Connection, tmp_path: Path,
    ):
        # Instance file overrides bundled defaults.
        (tmp_path / "identity_rules.toml").write_text("""
[[rules.people]]
name = "instance_only"
shape = "exact_field"
field = "email"
normalize = "lowercase"
confidence = 0.99
""", encoding="utf-8")
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/test.vcf', 'vcf', 'google-contacts')"
        )
        migrated_conn.execute(
            "INSERT INTO persons (id, schema_type, person_key, raw_hash, source_file_id)"
            " VALUES (300, 'Person', 'a', 'h300', 1), (301, 'Person', 'b', 'h301', 1)"
        )
        migrated_conn.commit()
        emissions = [
            FacetEmission(
                source_table="persons", source_id=300,
                facet_type="Person",
                payload={"id": 300, "email": "x@y.com"},
            ),
            FacetEmission(
                source_table="persons", source_id=301,
                facet_type="Person",
                payload={"id": 301, "email": "X@Y.com"},
            ),
        ]
        summary, _ = coalesce_buffer_to_db(
            migrated_conn, emissions, instance_dir=tmp_path,
        )
        assert summary.source.startswith("instance:")
        assert summary.rules_loaded == 1
        assert summary.auto_merged == 1

    def test_falls_back_to_bundled_when_no_instance_file(
        self, migrated_conn: sqlite3.Connection, tmp_path: Path,
    ):
        emissions = []
        summary, _ = coalesce_buffer_to_db(
            migrated_conn, emissions, instance_dir=tmp_path,
        )
        assert summary.source == "bundled-defaults"
        assert summary.rules_loaded == len(DEFAULT_PEOPLE_RULES)


# ---------------------------------------------------------------------------
# PeopleFacetPlugin — plugged into the bus, real coalesce()
# ---------------------------------------------------------------------------


class TestPeopleFacetPluginEndToEnd:
    def test_dry_run_without_connection(self):
        descriptors = discover_facets()
        people_desc = next(d for d in descriptors if d.name == "people")
        plugin = load_plugin(people_desc)
        assert isinstance(plugin, PeopleFacetPlugin)

        bus = EmissionBus()
        bus.subscribe(plugin)
        bus.emit(source_table="persons", source_id=1, facet_type="Person",
                 payload={"id": 1, "email": "a@x.com"})
        bus.emit(source_table="persons", source_id=2, facet_type="Person",
                 payload={"id": 2, "email": "A@X.com"})

        summary = plugin.coalesce()
        # Dry-run: counts proposals but doesn't write.
        assert summary["dry_run"] is True
        assert summary["emissions_processed"] == 2
        assert summary["proposals_generated"] == 1
        assert summary["audit_entries_written"] == 0
        assert summary["would_auto_merge"] == 1

    def test_live_mode_writes_audit(self, migrated_conn: sqlite3.Connection):
        descriptors = discover_facets()
        people_desc = next(d for d in descriptors if d.name == "people")
        plugin = load_plugin(people_desc)

        # Seed persons rows.
        migrated_conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/test.vcf', 'vcf', 'google-contacts')"
        )
        migrated_conn.execute(
            "INSERT INTO persons (id, schema_type, person_key, raw_hash, source_file_id)"
            " VALUES (400, 'Person', 'a', 'h400', 1), (401, 'Person', 'b', 'h401', 1)"
        )
        migrated_conn.commit()

        bus = EmissionBus()
        bus.subscribe(plugin)
        bus.emit(
            source_table="persons", source_id=400, facet_type="Person",
            payload={"id": 400, "email": "bob@example.com"},
        )
        bus.emit(
            source_table="persons", source_id=401, facet_type="Person",
            payload={"id": 401, "email": "Bob@Example.COM"},
        )

        summary = plugin.coalesce(connection=migrated_conn)
        assert summary["auto_merged"] == 1
        assert summary["audit_entries_written"] == 1
        assert summary["rules_loaded"] == len(DEFAULT_PEOPLE_RULES)
        assert summary["status"] == "phase-8a-coalescer"
        # Buffer drained.
        assert plugin.buffer == []

    def test_summary_shape_has_required_keys(self, migrated_conn: sqlite3.Connection):
        descriptors = discover_facets()
        people_desc = next(d for d in descriptors if d.name == "people")
        plugin = load_plugin(people_desc)

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
            rules_loaded=5,
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
        # exact_email_local_domain at 0.95 > AUTO_MERGE_THRESHOLD.
        # same_first_last at 0.40 < threshold AND require_manual_review.
        rules = load_rules_from_dicts(DEFAULT_PEOPLE_RULES)
        above = [r for r in rules if r.confidence >= AUTO_MERGE_THRESHOLD]
        manual = [r for r in rules if r.require_manual_review]
        assert len(above) >= 2
        assert len(manual) >= 1
        assert "same_first_last" in {r.name for r in manual}
