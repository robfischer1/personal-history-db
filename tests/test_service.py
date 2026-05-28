"""Tests for phdb.service — config loading, scheduler, watcher setup."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest


@pytest.fixture()
def service_toml(tmp_path: Path) -> Path:
    p = tmp_path / "service.toml"
    p.write_text(textwrap.dedent("""\
        [service]
        log_file = "test.log"
        pid_file = "test.pid"

        [service.watch.docs]
        path = "{watch_dir}"
        patterns = ["*.md", "*.txt"]
        ignore_dirs = [".git"]
        debounce = 3
        command = "echo changed {{path}}"

        [service.schedule.hourly-job]
        interval = "1h"
        command = "echo hourly"

        [service.schedule.daily-job]
        interval = "24h"
        at = "03:00"
        command = "echo daily"
        enabled = false
    """.format(watch_dir=str(tmp_path).replace("\\", "\\\\"))),
        encoding="utf-8",
    )
    return p


class TestServiceConfig:
    def test_load_basic(self, service_toml: Path) -> None:
        from phdb.service.config import ServiceConfig

        config = ServiceConfig.load(service_toml)
        assert config.log_file == "test.log"
        assert config.pid_file == "test.pid"

    def test_watch_jobs_parsed(self, service_toml: Path) -> None:
        from phdb.service.config import ServiceConfig

        config = ServiceConfig.load(service_toml)
        assert len(config.watch_jobs) == 1
        wj = config.watch_jobs[0]
        assert wj.name == "docs"
        assert wj.patterns == ["*.md", "*.txt"]
        assert wj.ignore_dirs == [".git"]
        assert wj.debounce == 3
        assert wj.enabled is True

    def test_schedule_jobs_parsed(self, service_toml: Path) -> None:
        from phdb.service.config import ServiceConfig

        config = ServiceConfig.load(service_toml)
        assert len(config.schedule_jobs) == 2

        hourly = next(j for j in config.schedule_jobs if j.name == "hourly-job")
        assert hourly.interval == "1h"
        assert hourly.enabled is True

        daily = next(j for j in config.schedule_jobs if j.name == "daily-job")
        assert daily.interval == "24h"
        assert daily.at == "03:00"
        assert daily.enabled is False

    def test_interval_seconds(self) -> None:
        from phdb.service.config import ScheduleJob

        assert ScheduleJob(name="a", interval="15m").interval_seconds() == 900
        assert ScheduleJob(name="b", interval="1h").interval_seconds() == 3600
        assert ScheduleJob(name="c", interval="24h").interval_seconds() == 86400
        assert ScheduleJob(name="d", interval="daily").interval_seconds() == 86400
        assert ScheduleJob(name="e", interval="weekly").interval_seconds() == 604800
        assert ScheduleJob(name="f", interval="30s").interval_seconds() == 30

    def test_interval_seconds_invalid(self) -> None:
        from phdb.service.config import ScheduleJob

        with pytest.raises(ValueError, match="Cannot parse interval"):
            ScheduleJob(name="bad", interval="every tuesday").interval_seconds()

    def test_validate_missing_path(self, tmp_path: Path) -> None:
        from phdb.service.config import ServiceConfig

        toml = tmp_path / "svc.toml"
        toml.write_text(textwrap.dedent("""\
            [service.watch.bad]
            path = "/nonexistent/path/xyz"
            command = "echo hi"
        """), encoding="utf-8")
        config = ServiceConfig.load(toml)
        warnings = config.validate()
        assert any("does not exist" in w for w in warnings)

    def test_validate_missing_command(self, tmp_path: Path) -> None:
        from phdb.service.config import ServiceConfig

        toml = tmp_path / "svc.toml"
        toml.write_text(textwrap.dedent("""\
            [service.schedule.empty]
            interval = "1h"
        """), encoding="utf-8")
        config = ServiceConfig.load(toml)
        warnings = config.validate()
        assert any("no command" in w for w in warnings)


class TestScheduler:
    def test_run_now_echo(self) -> None:
        from phdb.service.config import ScheduleJob
        from phdb.service.scheduler import Scheduler

        job = ScheduleJob(name="test", interval="1h", command="echo hello")
        scheduler = Scheduler()
        scheduler.add(job)
        code, err = scheduler.run_now("test")
        assert code == 0

    def test_run_now_unknown(self) -> None:
        from phdb.service.scheduler import Scheduler

        scheduler = Scheduler()
        code, err = scheduler.run_now("nonexistent")
        assert code == -1
        assert "unknown" in err

    def test_run_now_failing_command(self) -> None:
        from phdb.service.config import ScheduleJob
        from phdb.service.scheduler import Scheduler

        job = ScheduleJob(name="fail", interval="1h", command="exit 1")
        scheduler = Scheduler()
        scheduler.add(job)
        code, _err = scheduler.run_now("fail")
        assert code != 0


class TestWatcherManager:
    def test_start_without_watchdog(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import phdb.service.watcher as wmod

        monkeypatch.setattr(wmod, "HAS_WATCHDOG", False)
        from phdb.service.watcher import WatcherManager

        mgr = WatcherManager()
        mgr.start()
        mgr.stop()

    def test_matches_pattern(self, tmp_path: Path) -> None:
        from phdb.service.config import WatchJob
        from phdb.service.watcher import WatcherState, _DebouncedHandler

        import threading

        job = WatchJob(
            name="test",
            path=tmp_path,
            patterns=["*.md"],
            ignore_dirs=[".git"],
            command="echo test",
        )
        handler = _DebouncedHandler(WatcherState(job=job), threading.Event())
        assert handler._matches(str(tmp_path / "note.md")) is True
        assert handler._matches(str(tmp_path / "note.txt")) is False
        assert handler._matches(str(tmp_path / ".git" / "index.md")) is False


class TestServiceRunner:
    def test_pid_lifecycle(self, tmp_path: Path) -> None:
        from phdb.service.config import ServiceConfig
        from phdb.service.runner import ServiceRunner

        config = ServiceConfig()
        runner = ServiceRunner(config, tmp_path)

        assert not runner.is_running()
        runner._write_pid()
        assert runner.pid_path.exists()
        runner._remove_pid()
        assert not runner.pid_path.exists()
