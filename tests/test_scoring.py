"""Tests for decay scoring engine."""

import math
import sqlite3
from pathlib import Path

import pytest

from phdb.db import connect
from phdb.migrations.runner import MigrationRunner
from phdb.scoring import (
    DecayConfig,
    TierConfig,
    batch_recompute,
    compute_score,
    decay_factor,
    populate_initial_scores,
    record_engagement,
)


@pytest.fixture
def db_with_chunks(tmp_path: Path) -> Path:
    """Create a DB with migrations applied and some test chunks."""
    db_path = tmp_path / "test.db"
    with connect(db_path) as conn:
        runner = MigrationRunner(conn)
        runner.apply_pending()

        conn.execute(
            "INSERT INTO source_files (id, source_path, source_org, source_kind, file_kind)"
            " VALUES (1, '/test/mail.mbox', 'gmail', 'gmail', 'mbox')"
        )
        conn.execute(
            "INSERT INTO messages (id, source_file_id, schema_type, subject, date_sent, direction)"
            " VALUES (1, 1, 'EmailMessage', 'Test subject', '2024-01-15T10:00:00Z', 'inbound')"
        )
        conn.execute(
            "INSERT INTO messages (id, source_file_id, schema_type, subject, date_sent, direction)"
            " VALUES (2, 1, 'EmailMessage', 'Recent msg', '2026-05-01T08:00:00Z', 'outbound')"
        )
        conn.execute(
            "INSERT INTO chunks (id, source_table, source_id, chunk_index, content, schema_type)"
            " VALUES (1, 'messages', 1, 0, 'Old message content', 'EmailMessage')"
        )
        conn.execute(
            "INSERT INTO chunks (id, source_table, source_id, chunk_index, content, schema_type)"
            " VALUES (2, 'messages', 2, 0, 'Recent message content', 'EmailMessage')"
        )
        conn.commit()
    return db_path


class TestDecayFactor:
    def test_zero_age(self):
        assert decay_factor(0.0, 0.001) == 1.0

    def test_zero_lambda(self):
        assert decay_factor(365.0, 0.0) == 1.0

    def test_half_life(self):
        half_life = 730.0
        lam = math.log(2) / half_life
        result = decay_factor(half_life, lam)
        assert abs(result - 0.5) < 1e-10

    def test_decays_over_time(self):
        lam = math.log(2) / 365.0
        assert decay_factor(100, lam) > decay_factor(200, lam)


class TestComputeScore:
    def test_fresh_content_near_base(self):
        tier = TierConfig("standard", 730, 1.0, 0.05)
        score = compute_score(tier, 0.0, [], 0.1)
        assert score == 1.0

    def test_old_content_at_floor(self):
        tier = TierConfig("standard", 730, 1.0, 0.05)
        score = compute_score(tier, 10000.0, [], 0.1)
        assert score == tier.floor

    def test_engagement_boosts_score(self):
        tier = TierConfig("standard", 730, 1.0, 0.05)
        without = compute_score(tier, 500.0, [], 0.1)
        with_eng = compute_score(tier, 500.0, [0.0], 0.1)
        assert with_eng > without

    def test_evergreen_never_decays(self):
        tier = TierConfig("evergreen", -1, 1.0, 1.0)
        score = compute_score(tier, 99999.0, [], 0.1)
        assert score == 1.0


class TestTierConfig:
    def test_floor_computation(self):
        tier = TierConfig("standard", 730, 1.0, 0.05)
        assert tier.floor == 0.05

    def test_lambda_standard(self):
        tier = TierConfig("standard", 730, 1.0, 0.05)
        expected = math.log(2) / 730
        assert abs(tier.lambda_ - expected) < 1e-12

    def test_lambda_evergreen(self):
        tier = TierConfig("evergreen", -1, 1.0, 1.0)
        assert tier.lambda_ == 0.0


class TestDecayConfig:
    def test_load_default(self):
        config = DecayConfig.load()
        assert "standard" in config.tiers
        assert "evergreen" in config.tiers
        assert config.boost_fraction == 0.1

    def test_resolve_tier_by_source_kind(self):
        config = DecayConfig.load()
        tier = config.resolve_tier("gmail", None)
        assert tier.name == "standard"

    def test_resolve_tier_fast(self):
        config = DecayConfig.load()
        tier = config.resolve_tier("apple-health", None)
        assert tier.name == "fast"

    def test_resolve_tier_override_wins(self):
        config = DecayConfig.load()
        tier = config.resolve_tier("apple-health", None, tier_override="evergreen")
        assert tier.name == "evergreen"


class TestPopulateScores:
    def test_populates_all_chunks(self, db_with_chunks: Path):
        config = DecayConfig.load()
        with connect(db_with_chunks) as conn:
            count = populate_initial_scores(conn, config)
            assert count == 2

            scores = conn.execute(
                "SELECT chunk_id, score, tier FROM chunk_scores ORDER BY chunk_id"
            ).fetchall()
            assert len(scores) == 2
            assert scores[0][2] == "standard"
            assert scores[1][2] == "standard"

    def test_recent_scores_higher_than_old(self, db_with_chunks: Path):
        config = DecayConfig.load()
        with connect(db_with_chunks) as conn:
            populate_initial_scores(conn, config)
            scores = conn.execute(
                "SELECT chunk_id, score FROM chunk_scores ORDER BY chunk_id"
            ).fetchall()
            old_score = scores[0][1]
            recent_score = scores[1][1]
            assert recent_score > old_score

    def test_idempotent(self, db_with_chunks: Path):
        config = DecayConfig.load()
        with connect(db_with_chunks) as conn:
            first = populate_initial_scores(conn, config)
            second = populate_initial_scores(conn, config)
            assert first == 2
            assert second == 0


class TestBatchRecompute:
    def test_recompute_updates_scores(self, db_with_chunks: Path):
        config = DecayConfig.load()
        with connect(db_with_chunks) as conn:
            populate_initial_scores(conn, config)
            count = batch_recompute(conn, config)
            assert count == 2

    def test_engagement_affects_recompute(self, db_with_chunks: Path):
        config = DecayConfig.load()
        with connect(db_with_chunks) as conn:
            populate_initial_scores(conn, config)
            score_before = conn.execute(
                "SELECT score FROM chunk_scores WHERE chunk_id = 1"
            ).fetchone()[0]

            record_engagement(conn, 1, "read", source="test")
            batch_recompute(conn, config)

            score_after = conn.execute(
                "SELECT score FROM chunk_scores WHERE chunk_id = 1"
            ).fetchone()[0]
            assert score_after > score_before


class TestRecordEngagement:
    def test_creates_row(self, db_with_chunks: Path):
        with connect(db_with_chunks) as conn:
            record_engagement(conn, 1, "read", source="mcp")
            rows = conn.execute(
                "SELECT chunk_id, event_type, source FROM engagements"
            ).fetchall()
            assert len(rows) == 1
            assert rows[0][0] == 1
            assert rows[0][1] == "read"
            assert rows[0][2] == "mcp"
