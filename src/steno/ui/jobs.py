"""Background jobs (finalize, summary, to-do). One at a time; never on the HTTP thread."""

from __future__ import annotations

import itertools
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from steno.ui.events import EventBus

log = logging.getLogger(__name__)

#: ``runner(session_dir, params, progress) -> message``; ``progress(stage, done, total)``.
Progress = Callable[[str, int, int], None]
Runner = Callable[[Path, dict[str, Any], Progress], str]

KEEP_JOBS = 30
PUBLISH_INTERVAL_S = 0.2


class JobBusy(RuntimeError):
    """Another job is still running."""


@dataclass
class Job:
    id: str
    kind: str
    session: str
    params: dict[str, Any]
    state: str = "running"  # running | done | error
    stage: str = "starting"
    done: int = 0
    total: int = 0
    message: str = ""
    error: str = ""
    started: float = field(default_factory=time.time)
    finished: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "kind": self.kind, "session": self.session, "params": self.params,
            "state": self.state, "stage": self.stage, "done": self.done, "total": self.total,
            "message": self.message, "error": self.error, "started": self.started,
            "finished": self.finished,
        }


class JobManager:
    def __init__(self, bus: EventBus, runners: dict[str, Runner]):
        self._bus = bus
        self._runners = runners
        self._jobs: list[Job] = []
        self._counter = itertools.count(1)
        self._lock = threading.Lock()

    @property
    def kinds(self) -> list[str]:
        return sorted(self._runners)

    def running(self) -> Job | None:
        with self._lock:
            return next((j for j in self._jobs if j.state == "running"), None)

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [j.as_dict() for j in reversed(self._jobs)]

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return next((j for j in self._jobs if j.id == job_id), None)

    def submit(self, kind: str, session_id: str, session_dir: Path, params: dict[str, Any]) -> Job:
        if kind not in self._runners:
            raise KeyError(kind)
        with self._lock:
            if any(j.state == "running" for j in self._jobs):
                raise JobBusy("another job is still running")
            job = Job(f"j{next(self._counter)}", kind, session_id, dict(params))
            self._jobs.append(job)
            del self._jobs[:-KEEP_JOBS]
        threading.Thread(
            target=self._run, args=(job, session_dir), name=f"job-{job.id}", daemon=True
        ).start()
        self._publish(job)
        return job

    def _publish(self, job: Job) -> None:
        self._bus.publish("job", job.as_dict())

    def _run(self, job: Job, session_dir: Path) -> None:
        last = [0.0]

        def progress(stage: str, done: int = 0, total: int = 0) -> None:
            changed = stage != job.stage
            job.stage, job.done, job.total = stage, done, total
            now = time.monotonic()
            if changed or now - last[0] >= PUBLISH_INTERVAL_S:
                last[0] = now
                self._publish(job)

        try:
            job.message = self._runners[job.kind](session_dir, job.params, progress)
            job.state = "done"
        except Exception as exc:  # shown in the UI, never swallowed
            log.exception("Job %s (%s) failed", job.id, job.kind)
            text = str(exc).strip()
            job.error = text if isinstance(exc, ValueError | RuntimeError) and text else (
                f"{type(exc).__name__}: {text}" if text else type(exc).__name__)
            job.state = "error"
        finally:
            job.finished = time.time()
            self._publish(job)
