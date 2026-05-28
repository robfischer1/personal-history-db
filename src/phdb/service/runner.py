"""Service runner — main loop tying scheduler + watcher with PID and signal handling."""

from __future__ import annotations

import logging
import os
import signal
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from phdb.service.config import ServiceConfig
from phdb.service.scheduler import Scheduler
from phdb.service.watcher import WatcherManager

log = logging.getLogger("phdb.service")


class ServiceRunner:
    """Orchestrates directory watchers and scheduled jobs as a single process."""

    def __init__(self, config: ServiceConfig, data_dir: Path) -> None:
        self.config = config
        self.data_dir = data_dir
        self.pid_path = data_dir / config.pid_file
        self.log_path = data_dir / config.log_file
        self.scheduler = Scheduler()
        self.watcher = WatcherManager()
        self._stop_event = threading.Event()

    def _setup_logging(self) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            self.log_path,
            maxBytes=self.config.log_max_bytes,
            backupCount=self.config.log_backup_count,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-5s %(name)s — %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        ))
        root = logging.getLogger("phdb.service")
        root.addHandler(handler)
        root.setLevel(logging.INFO)

        console = logging.StreamHandler(sys.stderr)
        console.setFormatter(logging.Formatter("%(levelname)-5s %(message)s"))
        root.addHandler(console)

    def _write_pid(self) -> None:
        self.pid_path.parent.mkdir(parents=True, exist_ok=True)
        self.pid_path.write_text(str(os.getpid()), encoding="utf-8")

    def _remove_pid(self) -> None:
        import contextlib

        with contextlib.suppress(OSError):
            self.pid_path.unlink(missing_ok=True)

    def _read_pid(self) -> int | None:
        if not self.pid_path.is_file():
            return None
        try:
            return int(self.pid_path.read_text(encoding="utf-8").strip())
        except (ValueError, OSError):
            return None

    def is_running(self) -> bool:
        """Check if another service instance is already running."""
        pid = self._read_pid()
        if pid is None:
            return False
        if sys.platform == "win32":
            # os.kill(pid, 0) is unreliable on Windows — it can raise a SystemError
            # ("returned a result with an exception set") that the POSIX-style
            # handler below misses, crashing `phdb service status`. Probe the
            # process via the Win32 API instead.
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            self._remove_pid()
            return False
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            self._remove_pid()
            return False

    def _signal_handler(self, signum: int, _frame: object) -> None:
        name = signal.Signals(signum).name
        log.info("received %s — shutting down", name)
        self._stop_event.set()

    def start(self) -> int:
        """Start the service. Blocks until stopped. Returns exit code."""
        if self.is_running():
            pid = self._read_pid()
            log.error("service already running (pid %s)", pid)
            return 1

        self._setup_logging()
        self._write_pid()

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        if sys.platform == "win32":
            signal.signal(signal.SIGBREAK, self._signal_handler)

        for job in self.config.watch_jobs:
            self.watcher.add(job)
        for job in self.config.schedule_jobs:
            self.scheduler.add(job)

        watch_count = sum(1 for j in self.config.watch_jobs if j.enabled)
        sched_count = sum(1 for j in self.config.schedule_jobs if j.enabled)
        log.info(
            "phdb service starting — %d watcher(s), %d schedule(s), pid %d",
            watch_count, sched_count, os.getpid(),
        )

        try:
            self.watcher.start()
            self.scheduler.start()
            self._stop_event.wait()
        except KeyboardInterrupt:
            pass
        finally:
            log.info("shutting down...")
            self.scheduler.stop()
            self.watcher.stop()
            self._remove_pid()
            log.info("service stopped")

        return 0

    def stop(self) -> bool:
        """Send stop signal to a running service instance. Returns True if signal sent."""
        pid = self._read_pid()
        if pid is None:
            return False
        try:
            if sys.platform == "win32":
                os.kill(pid, signal.CTRL_BREAK_EVENT)
            else:
                os.kill(pid, signal.SIGTERM)
            return True
        except (OSError, PermissionError):
            self._remove_pid()
            return False

    def status_report(self) -> dict:
        """Build a status dict for display."""
        pid = self._read_pid()
        running = self.is_running()

        report: dict = {
            "running": running,
            "pid": pid if running else None,
            "pid_file": str(self.pid_path),
            "log_file": str(self.log_path),
            "watchers": [],
            "schedules": [],
        }

        for ws in self.watcher.status():
            report["watchers"].append({
                "name": ws.job.name,
                "path": str(ws.job.path),
                "patterns": ws.job.patterns,
                "enabled": ws.job.enabled,
                "triggers": ws.trigger_count,
                "last_trigger": ws.last_trigger.isoformat() if ws.last_trigger else None,
                "last_result": ws.last_result,
            })

        for ss in self.scheduler.status():
            report["schedules"].append({
                "name": ss.job.name,
                "interval": ss.job.interval,
                "enabled": ss.job.enabled,
                "runs": ss.run_count,
                "last_run": ss.last_run.isoformat() if ss.last_run else None,
                "next_run": ss.next_run.isoformat() if ss.next_run else None,
                "last_result": ss.last_result,
            })

        return report
