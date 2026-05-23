"""Tests for Phase 8C — interactive review CLI + unmerge + stats.

Covers:
- ``phdb facet people review`` walks proposals with [a]ccept / [r]eject /
  [d]efer / [s]top answers; updates the queue + DB accordingly.
- ``phdb facet people unmerge <audit_id>`` reverses a merge.
- ``phdb facets stats`` summarizes the audit log + pending queue depths.
- JSONL queue helper: append / load / save round-trip + dedupe.
- ``--rule`` filter narrows the review set.
- ``--limit`` caps the loop length.
- ``--auto-accept-threshold`` skips the prompt for above-threshold items.
- ``--dry-run`` reports what would happen without mutating queue or DB.
- Unknown facet name fails cleanly.
- ``phdb facet places review`` dispatches even though Phase 8B sibling
  hasn't shipped the engine yet (CLI is facet-name driven).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from phdb.cli import cli
from phdb.core.plugin.bus import FacetEmission
from phdb.db import connect
from phdb.facets._coalescence_lib import MergeProposal, apply_merge
from phdb.facets._review_queue import (
    QUEUE_DIRNAME,
    append_pending,
    load_pending,
    save_pending,
)
from phdb.migrations.runner import MigrationRunner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def migrated_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    with connect(db, create=True) as conn:
        MigrationRunner(conn).apply_pending()
    return db


@pytest.fixture
def instance_dir(tmp_path: Path) -> Path:
    d = tmp_path / "instance"
    d.mkdir()
    return d


def _seed_persons(db: Path, ids: list[int]) -> None:
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO source_files (id, source_path, file_kind, source_kind)"
            " VALUES (1, '/test.vcf', 'vcf', 'google-contacts')"
        )
        for pid in ids:
            conn.execute(
                "INSERT INTO persons (id, schema_type, person_key, raw_hash, source_file_id)"
                " VALUES (?, 'Person', ?, ?, 1)",
                (pid, f"k-{pid}", f"h-{pid}"),
            )
        conn.commit()


def _proposal(into: int, ids: list[int], rule: str = "exact_email_local_domain",
              confidence: float = 0.95) -> MergeProposal:
    return MergeProposal(
        into_node_id=into,
        from_emissions=[
            FacetEmission(
                source_table="persons", source_id=i,
                facet_type="Person",
                payload={"id": i, "email": f"alice{i}@example.com"},
            )
            for i in ids
        ],
        rule=rule,
        confidence=confidence,
        payload={"shape": "exact_field", "emission_count": len(ids), "existing_count": 0},
    )


# ---------------------------------------------------------------------------
# Queue helper — load / save / append round-trip
# ---------------------------------------------------------------------------


class TestReviewQueue:
    def test_load_missing_file_returns_empty(self, instance_dir: Path):
        assert load_pending("people", instance_dir) == []

    def test_append_then_load_round_trips(self, instance_dir: Path):
        p = _proposal(-1, [1, 2])
        append_pending("people", instance_dir, p)
        loaded = load_pending("people", instance_dir)
        assert len(loaded) == 1
        assert loaded[0].rule == p.rule
        assert loaded[0].confidence == p.confidence
        assert loaded[0].into_node_id == p.into_node_id
        assert len(loaded[0].from_emissions) == 2

    def test_save_replaces_file(self, instance_dir: Path):
        append_pending("people", instance_dir, _proposal(-1, [1, 2]))
        append_pending("people", instance_dir, _proposal(-1, [3, 4]))
        # Replace with just one.
        save_pending("people", instance_dir, [_proposal(-1, [5, 6])])
        loaded = load_pending("people", instance_dir)
        assert len(loaded) == 1
        assert loaded[0].from_emissions[0].payload["id"] == 5

    def test_append_dedupes_on_load(self, instance_dir: Path):
        # Two appends with same signature -> load returns one.
        p = _proposal(42, [10, 11])
        append_pending("people", instance_dir, p)
        append_pending("people", instance_dir, p)
        loaded = load_pending("people", instance_dir)
        assert len(loaded) == 1

    def test_queue_dir_created_on_first_use(self, instance_dir: Path):
        assert not (instance_dir / QUEUE_DIRNAME).exists()
        append_pending("people", instance_dir, _proposal(-1, [1, 2]))
        assert (instance_dir / QUEUE_DIRNAME).is_dir()

    def test_save_empty_list_truncates(self, instance_dir: Path):
        append_pending("people", instance_dir, _proposal(-1, [1, 2]))
        save_pending("people", instance_dir, [])
        assert load_pending("people", instance_dir) == []

    def test_jsonl_format_one_object_per_line(self, instance_dir: Path):
        append_pending("people", instance_dir, _proposal(-1, [1, 2]))
        append_pending("people", instance_dir, _proposal(-1, [3, 4]))
        path = instance_dir / QUEUE_DIRNAME / "people.jsonl"
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        assert len(lines) == 2
        for line in lines:
            obj = json.loads(line)
            assert "rule" in obj
            assert "from_emissions" in obj


# ---------------------------------------------------------------------------
# Interactive review loop — accept / reject / defer / stop
# ---------------------------------------------------------------------------


class TestReviewLoop:
    def test_accept_reject_defer_stop_dry_run(
        self, migrated_db: Path, instance_dir: Path,
    ):
        """4 proposals, answers "a r d s" should map cleanly in dry-run."""
        for ids in ([10, 11], [20, 21], [30, 31], [40, 41]):
            append_pending("people", instance_dir, _proposal(-1, ids))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facet", "people", "review",
                "--instance-dir", str(instance_dir),
                "--dry-run",
            ],
            input="a\nr\nd\ns\n",
        )
        assert result.exit_code == 0, result.output
        # 1 accept, 1 reject, 1 defer, then stop (deferred the rest).
        assert "accepted=1" in result.output
        assert "rejected=1" in result.output
        # On stop, the current+remaining items default to deferred — so
        # we expect deferred=2 (the explicit defer + the stopped one).
        assert "deferred=2" in result.output
        assert "stopped early" in result.output
        # Dry-run -> queue unchanged.
        loaded = load_pending("people", instance_dir)
        assert len(loaded) == 4

    def test_accept_writes_audit_and_drops_from_queue(
        self, migrated_db: Path, instance_dir: Path,
    ):
        _seed_persons(migrated_db, [10, 11])
        append_pending("people", instance_dir, _proposal(-1, [10, 11]))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facet", "people", "review",
                "--instance-dir", str(instance_dir),
            ],
            input="a\n",
        )
        assert result.exit_code == 0, result.output
        assert "accepted=1" in result.output
        # Audit row created.
        with connect(migrated_db, readonly=True) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM facet_coalescence_log"
            ).fetchone()[0]
        assert n == 1
        # Queue drained.
        assert load_pending("people", instance_dir) == []
        # Merged-away persons row gone.
        with connect(migrated_db, readonly=True) as conn:
            remaining = {r[0] for r in conn.execute(
                "SELECT id FROM persons WHERE id IN (10, 11)"
            ).fetchall()}
        assert remaining == {10}

    def test_reject_drops_proposal_no_audit(
        self, migrated_db: Path, instance_dir: Path,
    ):
        _seed_persons(migrated_db, [10, 11])
        append_pending("people", instance_dir, _proposal(-1, [10, 11]))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facet", "people", "review",
                "--instance-dir", str(instance_dir),
            ],
            input="r\n",
        )
        assert result.exit_code == 0, result.output
        assert "rejected=1" in result.output
        with connect(migrated_db, readonly=True) as conn:
            n = conn.execute(
                "SELECT COUNT(*) FROM facet_coalescence_log"
            ).fetchone()[0]
        assert n == 0
        assert load_pending("people", instance_dir) == []

    def test_defer_keeps_proposal_in_queue(
        self, migrated_db: Path, instance_dir: Path,
    ):
        _seed_persons(migrated_db, [10, 11])
        append_pending("people", instance_dir, _proposal(-1, [10, 11]))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facet", "people", "review",
                "--instance-dir", str(instance_dir),
            ],
            input="d\n",
        )
        assert result.exit_code == 0, result.output
        assert "deferred=1" in result.output
        # Still in queue.
        assert len(load_pending("people", instance_dir)) == 1

    def test_empty_queue_short_circuits(
        self, migrated_db: Path, instance_dir: Path,
    ):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facet", "people", "review",
                "--instance-dir", str(instance_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "No pending proposals" in result.output


# ---------------------------------------------------------------------------
# --rule + --limit filters
# ---------------------------------------------------------------------------


class TestFilters:
    def test_rule_filter_narrows_set(
        self, migrated_db: Path, instance_dir: Path,
    ):
        append_pending(
            "people", instance_dir,
            _proposal(-1, [10, 11], rule="exact_email_local_domain"),
        )
        append_pending(
            "people", instance_dir,
            _proposal(-1, [20, 21], rule="same_first_last", confidence=0.4),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facet", "people", "review",
                "--instance-dir", str(instance_dir),
                "--rule", "same_first_last",
                "--dry-run",
            ],
            input="d\n",
        )
        assert result.exit_code == 0, result.output
        assert "[1/1]" in result.output
        assert "same_first_last" in result.output
        # Other rule not surfaced.
        assert "[1/2]" not in result.output

    def test_limit_caps_loop_length(
        self, migrated_db: Path, instance_dir: Path,
    ):
        for ids in ([10, 11], [20, 21], [30, 31]):
            append_pending("people", instance_dir, _proposal(-1, ids))
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facet", "people", "review",
                "--instance-dir", str(instance_dir),
                "--limit", "2",
                "--dry-run",
            ],
            input="d\nd\n",
        )
        assert result.exit_code == 0, result.output
        # Two prompts means [1/2] and [2/2] both appear.
        assert "[1/2]" in result.output
        assert "[2/2]" in result.output
        # The third proposal didn't get a prompt.
        assert "[3/2]" not in result.output

    def test_limit_preserves_unwalked_items_in_queue(
        self, migrated_db: Path, instance_dir: Path,
    ):
        _seed_persons(migrated_db, [10, 11, 20, 21, 30, 31])
        for ids in ([10, 11], [20, 21], [30, 31]):
            append_pending("people", instance_dir, _proposal(-1, ids))
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facet", "people", "review",
                "--instance-dir", str(instance_dir),
                "--limit", "1",
            ],
            input="a\n",
        )
        assert result.exit_code == 0, result.output
        # First was accepted; the other two should remain in the queue.
        assert len(load_pending("people", instance_dir)) == 2

    def test_auto_accept_threshold_skips_prompt(
        self, migrated_db: Path, instance_dir: Path,
    ):
        _seed_persons(migrated_db, [10, 11])
        append_pending(
            "people", instance_dir,
            _proposal(-1, [10, 11], confidence=0.99),
        )
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facet", "people", "review",
                "--instance-dir", str(instance_dir),
                "--auto-accept-threshold", "0.95",
            ],
            # No input — auto-accept skips the prompt.
        )
        assert result.exit_code == 0, result.output
        assert "auto-accept" in result.output
        assert "accepted=1" in result.output


# ---------------------------------------------------------------------------
# unmerge
# ---------------------------------------------------------------------------


class TestUnmerge:
    def test_unmerge_round_trip(self, migrated_db: Path):
        _seed_persons(migrated_db, [50, 51])
        # Apply a merge directly so we have an audit id to undo.
        with connect(migrated_db) as conn:
            proposal = MergeProposal(
                into_node_id=-1,
                from_emissions=[{"id": 50}, {"id": 51}],
                rule="exact_email_local_domain",
                confidence=0.95,
            )
            apply_merge(
                conn, node_table="persons", proposal=proposal,
                facet_type="Person",
            )
            audit_id = conn.execute(
                "SELECT MAX(id) FROM facet_coalescence_log"
            ).fetchone()[0]

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facet", "people", "unmerge", str(audit_id),
            ],
        )
        assert result.exit_code == 0, result.output
        assert f"audit_id={audit_id}" in result.output
        assert "restored_count" in result.output

        with connect(migrated_db, readonly=True) as conn:
            remaining = {r[0] for r in conn.execute(
                "SELECT id FROM persons WHERE id IN (50, 51)"
            ).fetchall()}
            audits = conn.execute(
                "SELECT COUNT(*) FROM facet_coalescence_log"
            ).fetchone()[0]
        assert remaining == {50, 51}
        assert audits == 0

    def test_unmerge_unknown_id_fails(self, migrated_db: Path):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facet", "people", "unmerge", "99999",
            ],
        )
        assert result.exit_code != 0
        assert "no audit entry" in result.output


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_empty_db(self, migrated_db: Path, instance_dir: Path):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facets", "stats",
                "--instance-dir", str(instance_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "total merges: 0" in result.output

    def test_stats_after_merges(self, migrated_db: Path, instance_dir: Path):
        _seed_persons(migrated_db, [100, 101, 200, 201])
        with connect(migrated_db) as conn:
            for ids, rule, conf in (
                ([100, 101], "exact_email_local_domain", 0.95),
                ([200, 201], "phone_e164", 0.95),
            ):
                proposal = MergeProposal(
                    into_node_id=-1,
                    from_emissions=[{"id": i} for i in ids],
                    rule=rule,
                    confidence=conf,
                )
                apply_merge(
                    conn, node_table="persons", proposal=proposal,
                    facet_type="Person",
                )

        # Seed a pending-review proposal in the JSONL queue.
        append_pending("people", instance_dir, _proposal(-1, [300, 301]))

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facets", "stats",
                "--instance-dir", str(instance_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "total merges: 2" in result.output
        assert "Person" in result.output
        assert "exact_email_local_domain" in result.output
        assert "phone_e164" in result.output
        assert "0.90+" in result.output
        # Pending section reports the queue depth.
        assert "Pending review" in result.output
        # 1 in people queue.
        people_line = [ln for ln in result.output.splitlines() if ln.strip().startswith("people")]
        assert any("1" in ln for ln in people_line), people_line


# ---------------------------------------------------------------------------
# Facet name dispatch (places facet — Phase 8B sibling territory)
# ---------------------------------------------------------------------------


class TestPlacesFacetDispatch:
    def test_places_review_no_pending(
        self, migrated_db: Path, instance_dir: Path,
    ):
        """`phdb facet places review` works even with no Phase 8B engine."""
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "--db", str(migrated_db),
                "facet", "places", "review",
                "--instance-dir", str(instance_dir),
            ],
        )
        assert result.exit_code == 0, result.output
        assert "No pending proposals" in result.output

    def test_places_subgroup_help_lists_subcommands(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["facet", "places", "--help"])
        assert result.exit_code == 0, result.output
        assert "review" in result.output
        assert "unmerge" in result.output


# ---------------------------------------------------------------------------
# Top-level CLI surface
# ---------------------------------------------------------------------------


class TestCliSurface:
    def test_facet_command_in_help(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "facet" in result.output
        assert "facets" in result.output

    def test_facet_help_lists_people_and_places(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["facet", "--help"])
        assert result.exit_code == 0
        assert "people" in result.output
        assert "places" in result.output

    def test_facets_help_lists_stats(self):
        runner = CliRunner()
        result = runner.invoke(cli, ["facets", "--help"])
        assert result.exit_code == 0
        assert "stats" in result.output
