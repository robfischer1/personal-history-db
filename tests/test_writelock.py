"""Tests for the cross-process write lock."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from phdb.writelock import WriteLockError, _lock_path, write_lock


def test_acquires_and_releases(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    db.touch()
    with write_lock(db):
        assert _lock_path(db).exists()
    assert not _lock_path(db).exists()


def test_lockfile_exists_while_held(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    db.touch()
    with write_lock(db):
        assert _lock_path(db).exists()


def test_context_manager_cleans_up_on_exception(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    db.touch()
    with pytest.raises(ValueError, match="boom"), write_lock(db):
        raise ValueError("boom")
    assert not _lock_path(db).exists()


def test_stale_lock_dead_pid_auto_cleaned(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    db.touch()
    lock = _lock_path(db)
    lock.write_text("999999999\n2020-01-01T00:00:00Z\n", encoding="utf-8")
    with write_lock(db):
        pass
    assert not lock.exists()


def test_force_breaks_stale_lock(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    db.touch()
    lock = _lock_path(db)
    lock.write_text("999999999\n2020-01-01T00:00:00Z\n", encoding="utf-8")
    with write_lock(db, force=True):
        pass
    assert not lock.exists()


def test_live_pid_raises(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    db.touch()
    lock = _lock_path(db)
    lock.write_text(f"{os.getpid()}\n2020-01-01T00:00:00Z\n", encoding="utf-8")
    with pytest.raises(WriteLockError, match="Write lock held"), write_lock(db):
        pass
    lock.unlink()
