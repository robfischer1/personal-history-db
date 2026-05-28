"""Simple interval scheduler — runs jobs on repeating intervals."""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from phdb.service.config import ScheduleJob

log = logging.getLogger(__name__)


@dataclass
class JobState:
    """Tracks runtime state for a scheduled job."""

    job: ScheduleJob
    last_run: datetime | None = None
    last_result: int | None = None
    last_error: str | None = None
    next_run: datetime | None = None
    run_count: int = 0


def _run_command(command: str) -> tuple[int, str]:
    """Execute a shell command, return (exit_code, stderr_tail)."""
    try:
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=3600,
        )
        if result.returncode != 0:
            stderr_tail = result.stderr[-500:] if result.stderr else ""
            return result.returncode, stderr_tail
        return 0, ""
    except subprocess.TimeoutExpired:
        return -1, "command timed out after 3600s"
    except Exception as e:
        return -2, str(e)


class Scheduler:
    """Runs schedule jobs on their configured intervals in background threads."""

    def __init__(self) -> None:
        self._states: dict[str, JobState] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._stop_event = threading.Event()
        self._lock = threading.Lock()

    def add(self, job: ScheduleJob) -> None:
        self._states[job.name] = JobState(job=job)

    def start(self) -> None:
        """Start all registered schedule jobs."""
        self._stop_event.clear()
        for name, state in self._states.items():
            if not state.job.enabled:
                log.info("schedule/%s: disabled, skipping", name)
                continue
            self._schedule_next(name)

    def stop(self) -> None:
        """Cancel all pending timers."""
        self._stop_event.set()
        with self._lock:
            for name, timer in self._timers.items():
                timer.cancel()
                log.debug("schedule/%s: timer cancelled", name)
            self._timers.clear()

    def status(self) -> list[JobState]:
        """Return current state of all jobs."""
        return list(self._states.values())

    def run_now(self, name: str) -> tuple[int, str]:
        """Execute a named job immediately (blocking). Returns (exit_code, error)."""
        state = self._states.get(name)
        if state is None:
            return -1, f"unknown job: {name}"
        return self._execute(state)

    def _schedule_next(self, name: str) -> None:
        if self._stop_event.is_set():
            return
        state = self._states[name]
        delay = state.job.interval_seconds()
        state.next_run = datetime.now(UTC).replace(microsecond=0) + timedelta(seconds=delay)
        timer = threading.Timer(delay, self._run_job, args=(name,))
        timer.daemon = True
        with self._lock:
            old = self._timers.get(name)
            if old is not None:
                old.cancel()
            self._timers[name] = timer
        timer.start()
        log.info("schedule/%s: next run in %ds", name, delay)

    def _run_job(self, name: str) -> None:
        if self._stop_event.is_set():
            return
        state = self._states.get(name)
        if state is None:
            return
        self._execute(state)
        self._schedule_next(name)

    def _execute(self, state: JobState) -> tuple[int, str]:
        log.info("schedule/%s: running: %s", state.job.name, state.job.command)
        start = time.monotonic()
        code, err = _run_command(state.job.command)
        elapsed = time.monotonic() - start

        state.last_run = datetime.now(UTC)
        state.last_result = code
        state.last_error = err if code != 0 else None
        state.run_count += 1

        if code == 0:
            log.info("schedule/%s: completed in %.1fs", state.job.name, elapsed)
        else:
            log.error(
                "schedule/%s: failed (exit %d) in %.1fs: %s",
                state.job.name, code, elapsed, err,
            )
        return code, err
