"""Tests for WPEF follow-on — write-time bookmark↔web_page triple emission.

Covers brief 100 from the phdb Plugin Architecture plan (Phase 7).
Verifies that running the raindrop plugin against the canonical CSV
fixture emits the expected triples across all four predicates:
``taggedWith``, ``inFolder``, ``mentions``, ``relatesTo``.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from phdb.db import connect
from phdb.formats.bookmark_upserts import (
    _extract_concepts,
    emit_bookmark_triples,
)
from phdb.migrations.runner import MigrationRunner
from phdb.plugins.raindrop import RaindropPlugin
from phdb.records import BookmarkEvent, Provenance
from phdb.settings import IdentitySettings, Settings

FIXTURE_CSV = Path(__file__).parent / "fixtures" / "raindrop" / "raindrop_export.csv"


def _new_plugin() -> RaindropPlugin:
    """Build a RaindropPlugin with the in-tree manifest."""
    from phdb.core.plugin.manifest import load_manifest

    manifest_path = Path("src/phdb/plugins/raindrop/plugin.toml").resolve()
    manifest = load_manifest(manifest_path)
    return RaindropPlugin(manifest)


@pytest.fixture
def setup_db(tmp_path: Path) -> tuple[Path, Settings]:
    db_path = tmp_path / "test.db"
    with connect(db_path, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    settings = Settings(
        db_path=db_path,
        identity=IdentitySettings(owner_names={"test user"}),
    )
    return db_path, settings


def _count_predicate(conn, name: str) -> int:
    """Count triples whose predicate has the given name."""
    return conn.execute(
        "SELECT COUNT(*) FROM triples t"
        " JOIN predicates p ON p.id = t.predicate_id"
        " WHERE p.name = ?",
        (name,),
    ).fetchone()[0]


class TestPredicateSeed:
    """Migration 0026 seeds inFolder; 0012 covers the other three."""

    def test_in_folder_predicate_seeded(self, setup_db: tuple[Path, Settings]) -> None:
        db_path, _ = setup_db
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT name, tier FROM predicates WHERE name = 'inFolder'"
            ).fetchone()
        assert row is not None
        assert row[0] == "inFolder"
        assert row[1] == "knowledge"

    def test_other_three_predicates_already_present(
        self, setup_db: tuple[Path, Settings]
    ) -> None:
        db_path, _ = setup_db
        with connect(db_path) as conn:
            names = {
                r[0] for r in conn.execute(
                    "SELECT name FROM predicates WHERE name IN"
                    " ('taggedWith', 'mentions', 'relatesTo')"
                ).fetchall()
            }
        assert names == {"taggedWith", "mentions", "relatesTo"}

    def test_migration_recorded(self, setup_db: tuple[Path, Settings]) -> None:
        db_path, _ = setup_db
        with connect(db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM schema_migrations"
                " WHERE migration_id = '0026_bookmark_triple_predicates'"
            ).fetchone()
        assert row is not None


class TestConceptExtraction:
    """The mention-concept tokenizer is intentionally simple."""

    def test_strips_stopwords(self) -> None:
        result = _extract_concepts("the quick brown fox jumps")
        # 'the' is stopword; everything else stays
        assert "the" not in result
        assert "quick" in result
        assert "brown" in result
        assert "fox" in result

    def test_dedups(self) -> None:
        result = _extract_concepts("python python python")
        assert result == ["python"]

    def test_min_length(self) -> None:
        # Words shorter than 3 chars are dropped by the regex.
        result = _extract_concepts("a is on python")
        assert "python" in result
        assert "a" not in result
        assert "is" not in result

    def test_handles_empty(self) -> None:
        assert _extract_concepts("") == []
        assert _extract_concepts(None) == []

    def test_lowercases(self) -> None:
        result = _extract_concepts("Python Programming")
        assert "python" in result
        assert "programming" in result
        assert "Python" not in result


class TestRaindropFixtureEmission:
    """End-to-end: run raindrop plugin against canonical fixture, count triples."""

    def test_all_four_predicates_emit(
        self, setup_db: tuple[Path, Settings]
    ) -> None:
        db_path, settings = setup_db
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)

            counts = {
                pred: _count_predicate(conn, pred)
                for pred in ("relatesTo", "taggedWith", "inFolder", "mentions")
            }

        # Every emitted predicate has at least one row from the fixture.
        for pred, count in counts.items():
            assert count > 0, f"predicate {pred!r} emitted 0 triples"

    def test_relates_to_count(
        self, setup_db: tuple[Path, Settings]
    ) -> None:
        """One relatesTo per bookmark (4 bookmarks land — Gmail Root is junk-flagged
        but still inserted with excluded=1, so 4 total)."""
        db_path, settings = setup_db
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            bm_count = conn.execute("SELECT COUNT(*) FROM bookmarks").fetchone()[0]
            relates_to = _count_predicate(conn, "relatesTo")
        assert bm_count == 4
        assert relates_to == 4

    def test_tagged_with_count(
        self, setup_db: tuple[Path, Settings]
    ) -> None:
        """Fixture tags: row 100 = ['python'], row 102 = ['python', 'open-source'].
        Other rows have no tags. Expect 3 taggedWith triples (1 + 2)."""
        db_path, settings = setup_db
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            tagged = _count_predicate(conn, "taggedWith")
        assert tagged == 3

    def test_in_folder_count(
        self, setup_db: tuple[Path, Settings]
    ) -> None:
        """Fixture folders: Tech (rows 100, 103 — same URL, one bookmark row),
        Junk (101), Dev (102), Tech (103 dedupes), Reading (104).
        Expect 4 inFolder triples (one per surviving bookmark row)."""
        db_path, settings = setup_db
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            in_folder = _count_predicate(conn, "inFolder")
            folders = {
                r[0] for r in conn.execute(
                    "SELECT n.label FROM triples t"
                    " JOIN predicates p ON p.id = t.predicate_id"
                    " JOIN nodes n ON n.id = t.object_node_id"
                    " WHERE p.name = 'inFolder'"
                ).fetchall()
            }
        assert in_folder == 4
        assert folders == {"Tech", "Junk", "Dev", "Reading"}

    def test_mentions_emits_from_title_and_note(
        self, setup_db: tuple[Path, Settings]
    ) -> None:
        """Title 'Example Article' + note 'my note' yields concepts
        ['example', 'article', 'note'] (the/an/my stopworded)."""
        db_path, settings = setup_db
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            mentions_concepts = {
                r[0] for r in conn.execute(
                    "SELECT n.label FROM triples t"
                    " JOIN predicates p ON p.id = t.predicate_id"
                    " JOIN nodes n ON n.id = t.object_node_id"
                    " WHERE p.name = 'mentions'"
                ).fetchall()
            }
        # Concept set must include the easy wins; exact set isn't load-bearing
        # because the tokenizer is intentionally simple.
        assert "example" in mentions_concepts
        assert "article" in mentions_concepts
        assert "github" in mentions_concepts
        assert "repo" in mentions_concepts

    def test_provenance_is_raindrop_emitted(
        self, setup_db: tuple[Path, Settings]
    ) -> None:
        db_path, settings = setup_db
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            provenances = {
                r[0] for r in conn.execute(
                    "SELECT DISTINCT t.provenance FROM triples t"
                    " JOIN predicates p ON p.id = t.predicate_id"
                    " WHERE p.name IN ('relatesTo', 'taggedWith',"
                    "                   'inFolder', 'mentions')"
                ).fetchall()
            }
        assert "raindrop-emitted" in provenances

    def test_bookmark_nodes_link_back_to_corpus(
        self, setup_db: tuple[Path, Settings]
    ) -> None:
        """The bookmark-side graph node must carry source_table/source_id
        so triples are traceable back to the bookmarks row."""
        db_path, settings = setup_db
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            orphans = conn.execute(
                "SELECT n.id FROM nodes n"
                " WHERE n.kind = 'bookmark'"
                "   AND (n.source_table IS NULL OR n.source_id IS NULL)"
            ).fetchall()
        assert orphans == []

    def test_web_page_nodes_link_back_to_corpus(
        self, setup_db: tuple[Path, Settings]
    ) -> None:
        db_path, settings = setup_db
        plugin = _new_plugin()
        with connect(db_path) as conn:
            plugin.run(FIXTURE_CSV, conn, settings)
            orphans = conn.execute(
                "SELECT n.id FROM nodes n"
                " WHERE n.kind = 'web_page'"
                "   AND (n.source_table IS NULL OR n.source_id IS NULL)"
            ).fetchall()
        assert orphans == []

    def test_idempotent_emission(
        self, setup_db: tuple[Path, Settings]
    ) -> None:
        """Re-running the ingest doesn't double-count triples
        (add_triple uses INSERT OR IGNORE)."""
        db_path, settings = setup_db
        with connect(db_path) as conn:
            _new_plugin().run(FIXTURE_CSV, conn, settings)
            first = {
                p: _count_predicate(conn, p)
                for p in ("relatesTo", "taggedWith", "inFolder", "mentions")
            }
        with connect(db_path) as conn:
            _new_plugin().run(FIXTURE_CSV, conn, settings)
            second = {
                p: _count_predicate(conn, p)
                for p in ("relatesTo", "taggedWith", "inFolder", "mentions")
            }
        assert first == second


class TestUnitEmitHelper:
    """Direct unit-level test for emit_bookmark_triples (no plugin runner)."""

    def test_direct_call_returns_created_count(
        self, setup_db: tuple[Path, Settings]
    ) -> None:
        db_path, _ = setup_db
        event = BookmarkEvent(
            provenance=Provenance(source_path="t", raw_hash="h"),
            url="https://example.org/x",
            normalized_url="https://example.org/x",
            date_added="2024-01-01",
            instrument="test",
            title="Hello World",
            note="something interesting",
            tags=("alpha", "beta"),
            folder="MyFolder",
        )
        with connect(db_path) as conn:
            # Insert a stub web_page + bookmark row so the FKs are valid.
            from phdb.formats.bookmark_upserts import (
                upsert_bookmark,
                upsert_web_page,
            )

            sf_id = conn.execute(
                "INSERT INTO source_files (source_path, file_kind, source_kind)"
                " VALUES ('test.csv', 'csv', 'test') RETURNING id"
            ).fetchone()[0]
            wp_id = upsert_web_page(
                conn, event.url, event.normalized_url, title=event.title,
                source_file_id=sf_id,
            )
            bm_id = upsert_bookmark(conn, sf_id, event, web_page_id=wp_id)
            conn.commit()

            created = emit_bookmark_triples(
                conn,
                bookmark_id=bm_id, web_page_id=wp_id,
                event=event, provenance="apple_dbs-emitted",
            )

            # 1 relatesTo + 2 taggedWith + 1 inFolder + N mentions (>=2)
            assert created >= 4

            # Provenance must be honored.
            row = conn.execute(
                "SELECT COUNT(*) FROM triples WHERE provenance = 'apple_dbs-emitted'"
            ).fetchone()[0]
            assert row >= 4

    def test_empty_tags_and_folder_only_emit_relates_to(
        self, setup_db: tuple[Path, Settings]
    ) -> None:
        """Safari-style bookmark with no tags, no folder, no note —
        should still emit relatesTo (+ mentions if title is non-empty)."""
        db_path, _ = setup_db
        event = BookmarkEvent(
            provenance=Provenance(source_path="t", raw_hash="h2"),
            url="https://example.org/y",
            normalized_url="https://example.org/y",
            date_added="2024-01-01",
            instrument="safari",
            title=None,
            note=None,
            tags=(),
            folder=None,
        )
        with connect(db_path) as conn:
            from phdb.formats.bookmark_upserts import (
                upsert_bookmark,
                upsert_web_page,
            )

            sf_id = conn.execute(
                "INSERT INTO source_files (source_path, file_kind, source_kind)"
                " VALUES ('test2.csv', 'csv', 'test') RETURNING id"
            ).fetchone()[0]
            wp_id = upsert_web_page(
                conn, event.url, event.normalized_url,
                source_file_id=sf_id,
            )
            bm_id = upsert_bookmark(conn, sf_id, event, web_page_id=wp_id)
            conn.commit()

            created = emit_bookmark_triples(
                conn,
                bookmark_id=bm_id, web_page_id=wp_id,
                event=event, provenance="apple_dbs-emitted",
            )
            # Only relatesTo
            assert created == 1
