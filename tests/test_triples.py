"""Tests for the triple store service module."""

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.triples import (
    add_qualifier,
    add_triple,
    emit_for_frontmatter,
    get_predicate,
    get_qualifiers,
    list_predicates,
    node_neighborhood,
    query_triples,
    resolve_node,
    resolve_node_for_wikilink,
    triple_stats,
)


@pytest.fixture
def triple_db(tmp_path: Path) -> Path:
    """DB with all migrations applied (including 0012)."""
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()
    return db_path


class TestPredicates:
    def test_seed_predicates_exist(self, triple_db: Path):
        with connect(triple_db) as conn:
            preds = list_predicates(conn)
            assert len(preds) == 35
            names = {p["name"] for p in preds}
            assert "childOf" in names
            assert "relatesTo" in names
            assert "taggedWith" in names

    def test_get_predicate_by_name(self, triple_db: Path):
        with connect(triple_db) as conn:
            p = get_predicate(conn, "childOf")
            assert p is not None
            assert p["name"] == "childOf"
            assert p["symmetric"] is False

    def test_inverse_wiring(self, triple_db: Path):
        with connect(triple_db) as conn:
            child = get_predicate(conn, "childOf")
            parent = get_predicate(conn, "parentOf")
            assert child["inverse_predicate_id"] == parent["id"]
            assert parent["inverse_predicate_id"] == child["id"]

    def test_symmetric_predicate(self, triple_db: Path):
        with connect(triple_db) as conn:
            p = get_predicate(conn, "relatesTo")
            assert p["symmetric"] is True
            assert p["inverse_predicate_id"] == p["id"]

    def test_unknown_predicate_returns_none(self, triple_db: Path):
        with connect(triple_db) as conn:
            assert get_predicate(conn, "nonexistent") is None


class TestNodeResolution:
    def test_create_and_find(self, triple_db: Path):
        with connect(triple_db) as conn:
            id1 = resolve_node(conn, "Test Node", "concept")
            id2 = resolve_node(conn, "test node", "concept")
            assert id1 == id2

    def test_different_kinds_are_distinct(self, triple_db: Path):
        with connect(triple_db) as conn:
            id1 = resolve_node(conn, "Rob", "person")
            id2 = resolve_node(conn, "Rob", "concept")
            assert id1 != id2

    def test_create_false_returns_none(self, triple_db: Path):
        with connect(triple_db) as conn:
            result = resolve_node(conn, "Nonexistent", "concept", create=False)
            assert result is None

    def test_vault_path(self, triple_db: Path):
        with connect(triple_db) as conn:
            nid = resolve_node(
                conn, "Daily Log", "file",
                vault_path="Timelines/Journal/2026-05-18.md",
            )
            row = conn.execute(
                "SELECT vault_path FROM nodes WHERE id = ?", (nid,)
            ).fetchone()
            assert row[0] == "Timelines/Journal/2026-05-18.md"

    def test_corpus_linkage(self, triple_db: Path):
        with connect(triple_db) as conn:
            nid = resolve_node(
                conn, "Email Thread", "concept",
                source_table="messages", source_id=42,
            )
            row = conn.execute(
                "SELECT source_table, source_id FROM nodes WHERE id = ?",
                (nid,),
            ).fetchone()
            assert row[0] == "messages"
            assert row[1] == 42

    def test_corpus_backfill_on_existing_node(self, triple_db: Path):
        with connect(triple_db) as conn:
            nid = resolve_node(conn, "BackfillMe", "concept")
            row = conn.execute(
                "SELECT source_table FROM nodes WHERE id = ?", (nid,)
            ).fetchone()
            assert row[0] is None

            nid2 = resolve_node(
                conn, "BackfillMe", "concept",
                source_table="documents", source_id=99,
            )
            assert nid == nid2
            row = conn.execute(
                "SELECT source_table, source_id FROM nodes WHERE id = ?",
                (nid,),
            ).fetchone()
            assert row[0] == "documents"
            assert row[1] == 99

    def test_wikilink_resolution(self, triple_db: Path):
        with connect(triple_db) as conn:
            id1 = resolve_node_for_wikilink(conn, "[[Brain Soup]]")
            id2 = resolve_node_for_wikilink(conn, "[[Brain Soup|alias]]")
            assert id1 == id2

    def test_wikilink_bare_string(self, triple_db: Path):
        with connect(triple_db) as conn:
            nid = resolve_node_for_wikilink(conn, "Plain Label")
            assert nid is not None


class TestAddTriple:
    def test_add_by_labels(self, triple_db: Path):
        with connect(triple_db) as conn:
            result = add_triple(
                conn,
                subject="Vault",
                predicate="childOf",
                object_="System",
                provenance="explicit",
            )
            assert result["created"] is True
            assert result["triple_id"] is not None

    def test_add_by_ids(self, triple_db: Path):
        with connect(triple_db) as conn:
            subj = resolve_node(conn, "A", "concept")
            obj = resolve_node(conn, "B", "concept")
            pred = get_predicate(conn, "relatesTo")
            result = add_triple(conn, subj, pred["id"], obj)
            assert result["created"] is True

    def test_idempotent(self, triple_db: Path):
        with connect(triple_db) as conn:
            r1 = add_triple(
                conn, "X", "mentions", "Y",
                provenance="extraction", source_ref="test.md",
            )
            r2 = add_triple(
                conn, "X", "mentions", "Y",
                provenance="extraction", source_ref="test.md",
            )
            assert r1["created"] is True
            assert r2["created"] is False
            assert r1["triple_id"] == r2["triple_id"]

    def test_null_object(self, triple_db: Path):
        with connect(triple_db) as conn:
            result = add_triple(
                conn, "Rob", "wantsTo", None,
                provenance="explicit",
            )
            assert result["created"] is True
            assert result["object_node_id"] is None

    def test_unknown_predicate_raises(self, triple_db: Path):
        with connect(triple_db) as conn:
            with pytest.raises(ValueError, match="Unknown predicate"):
                add_triple(conn, "A", "bogus", "B")

    def test_with_qualifiers(self, triple_db: Path):
        with connect(triple_db) as conn:
            result = add_triple(
                conn, "Rob", "wentTo", "Gym",
                provenance="explicit",
                qualifiers=[
                    {"key": "context", "value": "morning workout"},
                    {"key": "confidence", "value": "0.9"},
                ],
            )
            assert result["created"] is True
            quals = get_qualifiers(conn, result["triple_id"])
            assert len(quals) == 2
            keys = {q["key"] for q in quals}
            assert keys == {"context", "confidence"}

    def test_observed_at(self, triple_db: Path):
        with connect(triple_db) as conn:
            result = add_triple(
                conn, "Rob", "wentTo", "Store",
                observed_at="2026-05-18T10:00:00Z",
                provenance="explicit",
            )
            triples = query_triples(conn, subject="Rob", predicate="wentTo")
            assert len(triples) == 1
            assert triples[0]["observed_at"] == "2026-05-18T10:00:00Z"


class TestQueryTriples:
    def test_query_all(self, triple_db: Path):
        with connect(triple_db) as conn:
            add_triple(conn, "A", "mentions", "B", provenance="test")
            add_triple(conn, "C", "childOf", "D", provenance="test")
            results = query_triples(conn)
            assert len(results) == 2

    def test_query_by_subject(self, triple_db: Path):
        with connect(triple_db) as conn:
            add_triple(conn, "Alpha", "mentions", "Beta", provenance="test")
            add_triple(conn, "Gamma", "mentions", "Delta", provenance="test")
            results = query_triples(conn, subject="Alpha")
            assert len(results) == 1
            assert results[0]["subject_label"] == "Alpha"

    def test_query_by_predicate(self, triple_db: Path):
        with connect(triple_db) as conn:
            add_triple(conn, "A", "mentions", "B", provenance="test")
            add_triple(conn, "C", "childOf", "D", provenance="test")
            results = query_triples(conn, predicate="mentions")
            assert len(results) == 1
            assert results[0]["predicate_name"] == "mentions"

    def test_query_null_objects_only(self, triple_db: Path):
        with connect(triple_db) as conn:
            add_triple(conn, "Rob", "wantsTo", None, provenance="test")
            add_triple(conn, "Rob", "wantsTo", "Coffee", provenance="test")
            results = query_triples(
                conn, subject="Rob", include_null_objects=True,
            )
            assert len(results) == 1
            assert results[0]["object_node_id"] is None

    def test_query_temporal_window(self, triple_db: Path):
        with connect(triple_db) as conn:
            add_triple(
                conn, "Rob", "wentTo", "Gym",
                observed_at="2026-05-01T00:00:00Z",
                provenance="test", source_ref="a",
            )
            add_triple(
                conn, "Rob", "wentTo", "Store",
                observed_at="2026-05-15T00:00:00Z",
                provenance="test", source_ref="b",
            )
            results = query_triples(
                conn, since="2026-05-10T00:00:00Z",
            )
            assert len(results) == 1
            assert results[0]["object_label"] == "Store"

    def test_query_with_inverse(self, triple_db: Path):
        with connect(triple_db) as conn:
            add_triple(conn, "Child", "childOf", "Parent", provenance="test")
            add_triple(conn, "Parent", "parentOf", "Child2", provenance="test")
            results = query_triples(
                conn, predicate="childOf", include_inverse=True,
            )
            assert len(results) == 2

    def test_query_limit_offset(self, triple_db: Path):
        with connect(triple_db) as conn:
            for i in range(5):
                add_triple(
                    conn, f"S{i}", "mentions", f"O{i}",
                    provenance="test", source_ref=f"ref{i}",
                )
            results = query_triples(conn, limit=2, offset=0)
            assert len(results) == 2


class TestQualifiers:
    def test_add_and_get(self, triple_db: Path):
        with connect(triple_db) as conn:
            r = add_triple(conn, "A", "relatesTo", "B", provenance="test")
            qid = add_qualifier(conn, r["triple_id"], "bucket", "friendship")
            assert qid is not None
            quals = get_qualifiers(conn, r["triple_id"])
            assert len(quals) == 1
            assert quals[0]["key"] == "bucket"
            assert quals[0]["value"] == "friendship"


class TestNeighborhood:
    def test_neighborhood(self, triple_db: Path):
        with connect(triple_db) as conn:
            add_triple(conn, "Hub", "mentions", "Spoke1", provenance="test", source_ref="a")
            add_triple(conn, "Hub", "mentions", "Spoke2", provenance="test", source_ref="b")
            add_triple(conn, "Upstream", "childOf", "Hub", provenance="test")
            result = node_neighborhood(conn, "Hub")
            assert result["node"]["label"] == "Hub"
            assert len(result["outgoing"]) == 2
            assert len(result["incoming"]) == 1

    def test_neighborhood_unknown_node(self, triple_db: Path):
        with connect(triple_db) as conn:
            result = node_neighborhood(conn, "Unknown")
            assert result["node"] is None


class TestStats:
    def test_stats_empty(self, triple_db: Path):
        with connect(triple_db) as conn:
            stats = triple_stats(conn)
            assert stats["nodes"] == 0
            assert stats["triples"] == 0

    def test_stats_with_data(self, triple_db: Path):
        with connect(triple_db) as conn:
            add_triple(conn, "A", "mentions", "B", provenance="test")
            stats = triple_stats(conn)
            assert stats["nodes"] == 2
            assert stats["triples"] == 1
            assert stats["null_object_triples"] == 0


class TestEmitForFrontmatter:
    def test_standard_keys(self, triple_db: Path):
        with connect(triple_db) as conn:
            fm = {"up": "[[Parent]]", "tags": ["idea", "draft"]}
            count = emit_for_frontmatter(conn, "notes/test.md", fm)
            assert count == 3  # 1 childOf + 2 taggedWith

    def test_predicate_form1_subject(self, triple_db: Path):
        """'outOf [[Milk]]' → file is subject, outOf is predicate, Milk is object."""
        with connect(triple_db) as conn:
            fm = {"predicate": ['outOf [[Milk]]']}
            count = emit_for_frontmatter(conn, "notes/grocery.md", fm)
            assert count == 1
            triples = query_triples(conn, subject="grocery")
            assert len(triples) == 1
            assert triples[0]["predicate_name"] == "outOf"
            assert triples[0]["object_label"] == "Milk"

    def test_predicate_form2_object(self, triple_db: Path):
        """'[[Rob]] outOf' → Rob is subject, outOf is predicate, file is object."""
        with connect(triple_db) as conn:
            fm = {"predicate": ['[[Rob]] outOf']}
            count = emit_for_frontmatter(conn, "notes/store.md", fm)
            assert count == 1
            triples = query_triples(conn, subject="Rob")
            assert len(triples) == 1
            assert triples[0]["predicate_name"] == "outOf"
            assert triples[0]["object_label"] == "store"

    def test_predicate_form3_note_as_predicate(self, triple_db: Path):
        """'[[Rob]] [[Milk]]' → Rob is subject, file IS the predicate, Milk is object."""
        with connect(triple_db) as conn:
            fm = {"predicate": ['[[Rob]] [[Milk]]']}
            count = emit_for_frontmatter(conn, "notes/wantsTo.md", fm)
            assert count == 1
            triples = query_triples(conn, subject="Rob")
            assert len(triples) == 1
            assert triples[0]["predicate_name"] == "wantsTo"
            assert triples[0]["object_label"] == "Milk"

    def test_predicate_string_value(self, triple_db: Path):
        """predicate: as a single string (not list)."""
        with connect(triple_db) as conn:
            fm = {"predicate": 'mentions [[Python]]'}
            count = emit_for_frontmatter(conn, "notes/code.md", fm)
            assert count == 1

    def test_predicate_idempotent(self, triple_db: Path):
        with connect(triple_db) as conn:
            fm = {"predicate": ['outOf [[Milk]]']}
            c1 = emit_for_frontmatter(conn, "notes/grocery.md", fm)
            c2 = emit_for_frontmatter(conn, "notes/grocery.md", fm)
            assert c1 == 1
            assert c2 == 0
