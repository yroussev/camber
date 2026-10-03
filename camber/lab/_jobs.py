"""The lab's job queue: one worker thread, progress, cancel.

Fetch and ingest jobs are long (a download is hundreds of MB; an ingest reads a year of 1-minute
data), so the HTTP request that starts one returns at once with a job id and the page polls
``GET /lab/jobs/<id>``. Exactly **one** worker thread runs jobs, in submission order: two ingests
never race on the same store, and a fetch never competes with another for the disk it was checked
against.

A job's work is a callable ``work(job)``. It reports through :meth:`Job.report` (a message and,
for a download, ``done`` / ``total`` bytes), which is also the cancellation point: after
:meth:`JobQueue.cancel` the next ``report`` raises :class:`JobCancelled`. A queued job is
cancelled before it starts; a running one stops at its next progress report. Cancelling is safe
for the dataset pipeline by construction -- a download stops with its ``.part`` file kept for a
``Range`` resume, and an ingest stops before its staged data is swapped in, so the store keeps
its previous data.
"""

from __future__ import annotations

import collections
import datetime as _dt
import queue
import secrets
import threading
import traceback

QUEUED, RUNNING, DONE, FAILED, CANCELLED = "queued", "running", "done", "failed", "cancelled"
FINISHED = frozenset({DONE, FAILED, CANCELLED})
MAX_KEPT = 200  # finished jobs remembered for the page; the oldest finished ones are dropped
_LOG_LINES = 50


class JobCancelled(Exception):
    """Raised inside a job's work at its next progress report after it was cancelled."""


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat()


class Job:
    """One queued unit of work and its observable state (thread-safe snapshot via :meth:`view`)."""

    def __init__(self, kind: str, params: dict, work):
        self.id = secrets.token_hex(6)
        self.kind = kind
        self.params = dict(params)
        self.work = work
        self.state = QUEUED
        self.message = "queued"
        self.done: int | None = None
        self.total: int | None = None
        self.result = None
        self.error: str | None = None
        self.created_at = _now()
        self.started_at: str | None = None
        self.finished_at: str | None = None
        self.log: collections.deque = collections.deque(maxlen=_LOG_LINES)
        self._cancel = threading.Event()
        self._finished = threading.Event()
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ used by the work

    def report(self, message: str | None = None, done=None, total=None) -> None:
        """Record progress; raises :class:`JobCancelled` once the job has been cancelled."""
        if self._cancel.is_set():
            raise JobCancelled(f"job {self.id} cancelled")
        with self._lock:
            if message is not None and message != self.message:
                self.message = str(message)
                self.log.append(self.message)
            self.done = None if done is None else int(done)
            self.total = None if total is None else int(total)

    @property
    def cancel_requested(self) -> bool:
        """True once :meth:`JobQueue.cancel` was called for this job."""
        return self._cancel.is_set()

    # ------------------------------------------------------------------ observers

    def wait(self, timeout: float | None = None) -> bool:
        """Block until the job is finished (done, failed or cancelled); ``False`` on timeout."""
        return self._finished.wait(timeout)

    def view(self) -> dict:
        """A JSON-friendly snapshot of the job."""
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "params": dict(self.params),
                "state": self.state,
                "message": self.message,
                "done": self.done,
                "total": self.total,
                "result": self.result,
                "error": self.error,
                "cancel_requested": self._cancel.is_set(),
                "created_at": self.created_at,
                "started_at": self.started_at,
                "finished_at": self.finished_at,
                "log": list(self.log),
            }

    def _set(self, **fields) -> None:
        with self._lock:
            for k, v in fields.items():
                setattr(self, k, v)
            if fields.get("state") in FINISHED:
                self.finished_at = _now()
        if fields.get("state") in FINISHED:
            self._finished.set()


class JobQueue:
    """A FIFO of :class:`Job` s run by a single daemon worker thread."""

    def __init__(self, *, name: str = "camber-lab-worker"):
        self._q: queue.Queue = queue.Queue()
        self._jobs: collections.OrderedDict = collections.OrderedDict()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._worker = threading.Thread(target=self._loop, name=name, daemon=True)
        self._worker.start()

    def submit(self, kind: str, params: dict, work) -> Job:
        """Queue ``work(job)``; returns the job (state ``queued``)."""
        job = Job(kind, params, work)
        with self._lock:
            self._jobs[job.id] = job
            self._trim()
        self._q.put(job)
        return job

    def get(self, job_id: str) -> Job | None:
        """The job with ``job_id`` (``None`` if unknown or forgotten)."""
        with self._lock:
            return self._jobs.get(job_id)

    def jobs(self) -> list:
        """Every remembered job, newest first."""
        with self._lock:
            return list(reversed(self._jobs.values()))

    def cancel(self, job_id: str) -> Job | None:
        """Request cancellation. A queued job is cancelled at once; a running one at its next
        progress report; a finished one is left as it is. ``None`` if the job is unknown."""
        job = self.get(job_id)
        if job is None:
            return None
        job._cancel.set()
        with job._lock:
            queued = job.state == QUEUED
        if queued:
            job._set(state=CANCELLED, message="cancelled before it started")
        return job

    def busy(self) -> bool:
        """True while a job is queued or running."""
        with self._lock:
            return any(j.state not in FINISHED for j in self._jobs.values())

    def close(self, timeout: float = 5.0) -> None:
        """Stop the worker after the running job (queued jobs are cancelled)."""
        self._stop.set()
        for job in self.jobs():
            if job.state == QUEUED:
                self.cancel(job.id)
        self._q.put(None)
        self._worker.join(timeout)

    # ------------------------------------------------------------------ internals

    def _trim(self) -> None:
        extra = len(self._jobs) - MAX_KEPT
        if extra <= 0:
            return
        for jid in [j for j, job in self._jobs.items() if job.state in FINISHED][:extra]:
            del self._jobs[jid]

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self._q.get()
            if job is None:
                break
            if job.state != QUEUED:  # cancelled while queued
                continue
            job._set(state=RUNNING, started_at=_now(), message="started")
            try:
                result = job.work(job)
            except JobCancelled:
                job._set(state=CANCELLED, message="cancelled")
            except Exception as exc:  # the job's error is shown on the page, never re-raised
                job._set(
                    state=FAILED,
                    error=f"{type(exc).__name__}: {exc}",
                    message="failed",
                )
                job.log.append(traceback.format_exception_only(type(exc), exc)[-1].strip())
            else:
                job._set(state=DONE, result=result, message="done")
