"""Tests for phdb.skill_graph.practice_events — pure mapping/filtering logic."""

from __future__ import annotations

from phdb.skill_graph.practice_events import (
    CommitInfo,
    default_discipline_mapper,
    filter_and_map,
)


def _commit(sha: str, repo: str = "test-repo", timestamp: str = "2026-05-19T12:00:00Z") -> CommitInfo:
    return CommitInfo(sha=sha, timestamp=timestamp, subject="msg", repo=repo)


def test_filter_keeps_only_rob_authored() -> None:
    commits = [_commit("a"), _commit("b"), _commit("c")]
    authorship = {
        "a": "rob-authored",
        "b": "ai-coauthored",
        "c": "external",
    }
    events = filter_and_map(commits, authorship, default_discipline_mapper)
    assert "Programming" in events
    assert len(events["Programming"]) == 1
    assert events["Programming"][0].sha == "a"


def test_filter_unknown_authorship_is_excluded() -> None:
    """A sha with no entry in the authorship dict is treated as non-rob-authored."""
    commits = [_commit("a")]
    events = filter_and_map(commits, {}, default_discipline_mapper)
    assert events == {}


def test_discipline_mapper_can_assign_multiple() -> None:
    commits = [_commit("a")]
    authorship = {"a": "rob-authored"}

    def multi(_c: CommitInfo) -> list[str]:
        return ["Programming", "Python"]

    events = filter_and_map(commits, authorship, multi)
    assert "Programming" in events
    assert "Python" in events
    assert len(events["Programming"]) == 1
    assert len(events["Python"]) == 1


def test_discipline_mapper_can_skip_via_empty_list() -> None:
    """Returning [] from the mapper drops the commit entirely."""
    commits = [_commit("a")]
    authorship = {"a": "rob-authored"}
    events = filter_and_map(commits, authorship, lambda _c: [])
    assert events == {}


def test_practice_event_carries_metadata() -> None:
    commit = _commit("abc123", repo="vault", timestamp="2026-05-19T10:00:00Z")
    events = filter_and_map([commit], {"abc123": "rob-authored"}, default_discipline_mapper)
    evt = events["Programming"][0]
    assert evt.sha == "abc123"
    assert evt.repo == "vault"
    assert evt.timestamp == "2026-05-19T10:00:00Z"
    assert evt.discipline == "Programming"


def test_default_mapper_returns_programming() -> None:
    assert default_discipline_mapper(_commit("a")) == ["Programming"]


def test_filter_handles_empty_input() -> None:
    assert filter_and_map([], {}, default_discipline_mapper) == {}


def test_filter_groups_multiple_commits_per_discipline() -> None:
    commits = [_commit("a"), _commit("b"), _commit("c")]
    authorship = {
        "a": "rob-authored",
        "b": "rob-authored",
        "c": "rob-authored",
    }
    events = filter_and_map(commits, authorship, default_discipline_mapper)
    assert len(events["Programming"]) == 3
    shas = {e.sha for e in events["Programming"]}
    assert shas == {"a", "b", "c"}
