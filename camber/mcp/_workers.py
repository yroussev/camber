"""Killable worker processes for ``tools/call`` (0.111, #292).

Python cannot stop a thread, and a pandas / Arrow scan never checks a flag, so a call that timed
out, was cancelled or whose client has gone can only be stopped by ending the process it runs in.
:class:`WorkerPool` keeps at most ``max_concurrent`` worker processes, each a fresh interpreter
that builds the capability registry once and then runs :meth:`Registry.call` for one call at a
time -- the same call, under the same context, with the same validation, policy, no-persist scope,
redaction and citation checks as in-process; the worker only carries the outcome back. A worker is
killed (``SIGKILL``) and replaced when its call is cancelled, runs past its time cap, leaves a
handler thread behind, or the server shuts down; the next call finds a warm replacement.

Calls beyond the running ones wait in a FIFO queue of at most ``max_queued``; a call beyond that
is refused at once (``busy``). Each call is a :class:`Job` with a ``state`` (``queued``,
``running``, ``done``, ``cancelled``), the hook a task layer (MCP Tasks, #279) polls and cancels.

The parent and a worker talk over the worker's stdin / stdout: length-prefixed pickles (the parent
only ever unpickles what its own child wrote). Inside the worker, fd 1 points at stderr and fd 0 at
``/dev/null``, so a handler's stray output lands in the log. A worker exits on its own when the
parent closes the pipe or dies, and always through ``os._exit`` (no interpreter teardown under a
running Arrow scan).
"""

from __future__ import annotations

import collections
import json
import logging
import os
import pickle
import select
import signal
import struct
import subprocess
import sys
import threading
import time
from typing import Any

log = logging.getLogger("camber.mcp")

_HEADER = struct.Struct("!Q")
#: the worker's entry point: the parent's import path, then the registry factory
_BOOT = (
    "import sys, json; sys.path[:] = json.loads(sys.argv[1]); "
    "from camber.mcp._workers import _worker_main; _worker_main(sys.argv[2])"
)


class _Gone(Exception):
    """The other end of a worker pipe closed (or the worker died)."""


def _write_frame(fd: int, obj: Any) -> None:
    data = pickle.dumps(obj, protocol=pickle.HIGHEST_PROTOCOL)
    view = memoryview(_HEADER.pack(len(data)) + data)
    while view:
        try:
            n = os.write(fd, view)
        except (BrokenPipeError, OSError) as exc:
            raise _Gone from exc
        view = view[n:]


class _Reader:
    """Frames from one pipe, with an optional deadline (``TimeoutError`` when it passes)."""

    def __init__(self, fd: int):
        self.fd = fd
        self.buf = bytearray()

    def _fill(self, n: int, deadline: float | None) -> None:
        while len(self.buf) < n:
            if deadline is not None:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError
                ready, _, _ = select.select([self.fd], [], [], left)
                if not ready:
                    continue
            try:
                chunk = os.read(self.fd, max(65536, n - len(self.buf)))
            except OSError as exc:
                raise _Gone from exc
            if not chunk:
                raise _Gone
            self.buf += chunk

    def read(self, deadline: float | None = None) -> Any:
        self._fill(_HEADER.size, deadline)
        (size,) = _HEADER.unpack(self.buf[: _HEADER.size])
        self._fill(_HEADER.size + size, deadline)
        data = bytes(self.buf[_HEADER.size : _HEADER.size + size])
        del self.buf[: _HEADER.size + size]
        return pickle.loads(data)


# ---------------------------------------------------------------------------- the worker side


def _load_factory(spec: str):
    module, _, attr = spec.partition(":")
    import importlib

    return getattr(importlib.import_module(module), attr or "default_registry")


def _outcome(registry: Any, name: str, arguments: dict, ctx: Any) -> tuple:
    """``Registry.call`` as a picklable tuple: ``("ok", fields)``, ``("err", code, message,
    data)`` for a CapabilityError (recognised by shape), ``("exc", type name)`` for anything
    else (a registry breaking its contract; the traceback goes to stderr)."""
    try:
        res = registry.call(name, arguments, ctx)
        return ("ok", (res.data, res.text, res.facts, res.banners, res.truncated))
    except Exception as exc:  # noqa: BLE001 - carried to the parent
        code, message = getattr(exc, "code", None), getattr(exc, "message", None)
        if isinstance(code, str) and isinstance(message, str):
            return ("err", code, message, getattr(exc, "data", None))
        import traceback

        traceback.print_exc()
        return ("exc", type(exc).__name__)


def _abandoned_threads() -> bool:
    """Whether a handler thread is still running (a timed-out call left it behind)."""
    me = threading.current_thread()
    return any(
        t is not me and t.is_alive() and t.name.startswith("capability-")
        for t in threading.enumerate()
    )


def _watch_parent(parent: int) -> None:
    while True:
        time.sleep(0.5)
        if os.getppid() != parent:
            os._exit(0)


def _worker_main(factory: str) -> None:  # pragma: no cover - runs in the worker process
    """The worker process: build the registry, then answer one call at a time until EOF."""
    try:
        signal.signal(signal.SIGINT, signal.SIG_IGN)  # Ctrl-C is the parent's to handle
        requests, replies = os.dup(0), os.dup(1)
        devnull = os.open(os.devnull, os.O_RDONLY)
        os.dup2(devnull, 0)
        os.close(devnull)
        os.dup2(2, 1)
        sys.stdout = sys.stderr
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("camber mcp worker: %(levelname)s %(message)s"))
        logging.getLogger("camber").addHandler(handler)
        threading.Thread(target=_watch_parent, args=(os.getppid(),), daemon=True).start()
        registry = _load_factory(factory)()
        _write_frame(replies, ("ready", os.getpid()))
        reader = _Reader(requests)
        while True:
            job_id, name, arguments, ctx = reader.read()
            outcome = _outcome(registry, name, arguments, ctx)
            try:
                _write_frame(replies, (job_id, outcome, _abandoned_threads()))
            except _Gone:
                raise
            except Exception:  # noqa: BLE001 - a result that does not pickle
                log.exception("the result of %s could not be sent", name)
                _write_frame(replies, (job_id, ("exc", "UnpicklingError"), True))
    except _Gone:
        os._exit(0)
    except BaseException:  # noqa: BLE001 - never a teardown under running scans
        import traceback

        traceback.print_exc()
        os._exit(1)
    os._exit(0)


# ---------------------------------------------------------------------------- the parent side


class _RemoteResult:
    """A ``CapabilityResult`` as a worker returned it (the attributes the server reads)."""

    __slots__ = ("data", "text", "facts", "banners", "truncated")

    def __init__(self, data, text, facts, banners, truncated):
        self.data, self.text, self.facts = data, text, facts
        self.banners, self.truncated = banners, truncated


class RemoteError(Exception):
    """A ``CapabilityError`` from a worker, or one the pool raises itself (``busy``, ``timeout``,
    ``internal``): the shape the server recognises (``code``, ``message``, ``data``)."""

    def __init__(self, code: str, message: str, data: Any = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data


def _decode(outcome: tuple) -> Any:
    kind = outcome[0]
    if kind == "ok":
        return _RemoteResult(*outcome[1])
    if kind == "err":
        return RemoteError(outcome[1], outcome[2], outcome[3])
    return RuntimeError(f"the capability call raised {outcome[1]}")


class _Worker:
    """One worker process and its two pipes."""

    def __init__(self, factory: str):
        self.proc = subprocess.Popen(
            [sys.executable, "-I", "-c", _BOOT, json.dumps(sys.path), factory],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            close_fds=True,
            bufsize=0,
        )
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.requests, self.replies = self.proc.stdin, self.proc.stdout
        self.reader = _Reader(self.replies.fileno())
        self.killed = False

    def wait_ready(self, start_seconds: float) -> None:
        """Wait for the worker's registry to load; killed and raised when it does not."""
        try:
            hello = self.reader.read(time.monotonic() + start_seconds)
        except (TimeoutError, _Gone, pickle.UnpicklingError, EOFError):
            self.kill()
            raise
        if not (isinstance(hello, tuple) and hello[0] == "ready"):
            self.kill()
            raise _Gone

    @property
    def pid(self) -> int:
        return self.proc.pid

    def call(self, job_id: int, call: Any, deadline: float | None) -> tuple:
        _write_frame(self.requests.fileno(), (job_id, call.name, call.arguments, call.context))
        while True:
            reply = self.reader.read(deadline)
            if isinstance(reply, tuple) and reply and reply[0] == job_id:
                return reply

    def signal_kill(self) -> None:
        """Send SIGKILL (any thread; the owner reaps)."""
        try:
            self.proc.kill()
        except OSError:
            pass
        self.killed = True

    def kill(self) -> None:
        """SIGKILL, reap and close the pipes."""
        self.signal_kill()
        try:
            self.proc.wait(5)
        except subprocess.TimeoutExpired:  # pragma: no cover - SIGKILL is not ignorable
            log.error("worker %d did not exit after SIGKILL", self.proc.pid)
        for f in (self.requests, self.replies):
            try:
                f.close()
            except OSError:
                pass


class Job:
    """One call in a :class:`WorkerPool`: ``state`` is ``queued``, ``running``, ``done`` or
    ``cancelled``; ``outcome`` (a result or an exception) is set when it is ``done``."""

    def __init__(self, call: Any, on_done):
        self.call = call
        self.on_done = on_done
        self.state = "queued"
        self.outcome: Any = None
        self.submitted = time.monotonic()
        self.started: float | None = None
        self._slot: _Slot | None = None


class _Slot:
    def __init__(self, index: int):
        self.index = index
        self.worker: _Worker | None = None
        self.starting: _Worker | None = None
        self.job: Job | None = None
        self.thread: threading.Thread | None = None


_CANCELLED = object()


class WorkerPool:
    """At most ``max_concurrent`` killable worker processes running ``tools/call`` (#292).

    ``registry_factory`` is ``"module:function"``: the worker imports it and calls it once for
    its registry (``camber.capabilities:default_registry`` for ``camber mcp``). A call that runs
    ``grace_seconds`` past its context's ``caps.max_seconds`` without an answer is a ``timeout``
    (the registry's own cap normally answers first); either way its worker is killed. Up to
    ``max_queued`` calls wait for a free worker; :meth:`submit` refuses more (``None``).

    :func:`camber.mcp.serve` starts the pool and closes it at the end of the session.
    """

    def __init__(
        self,
        registry_factory: str,
        *,
        max_concurrent: int = 2,
        max_queued: int = 8,
        grace_seconds: float = 5.0,
        start_seconds: float = 60.0,
    ):
        if int(max_concurrent) < 1:
            raise ValueError("max_concurrent must be at least 1")
        if int(max_queued) < 0:
            raise ValueError("max_queued must be 0 or more")
        self.registry_factory = registry_factory
        self.max_concurrent = int(max_concurrent)
        self.max_queued = int(max_queued)
        self.grace_seconds = float(grace_seconds)
        self.start_seconds = float(start_seconds)
        self._cond = threading.Condition()
        self._queue: collections.deque = collections.deque()
        self._slots = [_Slot(i) for i in range(self.max_concurrent)]
        self._running = 0
        self._closing = False
        self._started = False
        self._next_id = 0

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Start the slot threads; the first slot starts its worker at once (warm first call)."""
        with self._cond:
            if self._started:
                return
            self._started = True
        for slot in self._slots:
            slot.thread = threading.Thread(
                target=self._slot_loop, args=(slot,), name=f"mcp-slot-{slot.index}", daemon=True
            )
            slot.thread.start()

    def submit(self, call: Any, on_done) -> Job | None:
        """Queue ``call``; ``on_done(job)`` runs (in a pool thread) when it has an outcome, never
        for a cancelled job. ``None`` when the pool is full or closed (the caller says ``busy``)."""
        with self._cond:
            if self._closing or self._running + len(self._queue) >= (
                self.max_concurrent + self.max_queued
            ):
                return None
            job = Job(call, on_done)
            self._queue.append(job)
            self._cond.notify()
        return job

    def cancel(self, job: Job) -> bool:
        """Stop ``job``: dropped from the queue, or its worker killed. ``True`` when it was
        still queued or running; its ``on_done`` is never called."""
        with self._cond:
            if job.state == "queued":
                try:
                    self._queue.remove(job)
                except ValueError:
                    pass
                job.state = "cancelled"
                return True
            if job.state != "running":
                return False
            job.state = "cancelled"
            worker = job._slot.worker if job._slot is not None else None
        if worker is not None:
            worker.signal_kill()
        return True

    def busy_error(self) -> RemoteError:
        """The ``busy`` refusal for a call :meth:`submit` did not take."""
        return RemoteError(
            "busy",
            f"the server is already running {self.max_concurrent} call(s) with "
            f"{self.max_queued} more waiting; retry shortly",
            {
                "retryable": True,
                "max_concurrent": self.max_concurrent,
                "max_queued": self.max_queued,
            },
        )

    def close(self, timeout: float = 2.0) -> int:
        """Kill every worker, drop the queue and stop the slot threads; the number of calls that
        were stopped (running or queued)."""
        with self._cond:
            if self._closing:
                return 0
            self._closing = True
            stopped = len(self._queue)
            for job in self._queue:
                job.state = "cancelled"
            self._queue.clear()
            workers = []
            for slot in self._slots:
                if slot.job is not None:
                    stopped += 1
                    slot.job.state = "cancelled"
                workers += [w for w in (slot.worker, slot.starting) if w is not None]
            self._cond.notify_all()
        for w in workers:
            w.signal_kill()
        end = time.monotonic() + timeout
        for slot in self._slots:
            if slot.thread is not None:
                slot.thread.join(max(0.0, end - time.monotonic()))
        return stopped

    def worker_pids(self) -> list[int]:
        """The process ids of the live workers (for tests and diagnostics)."""
        with self._cond:
            return [s.worker.pid for s in self._slots if s.worker is not None]

    # ------------------------------------------------------------------ slots

    def _spawn(self, slot: _Slot) -> _Worker:
        worker = _Worker(self.registry_factory)
        with self._cond:
            closing = self._closing
            if not closing:
                slot.starting = worker  # close() kills it while it loads
        if closing:
            worker.kill()
            raise _Gone
        try:
            worker.wait_ready(self.start_seconds)
        except Exception:
            if self._closing:
                raise _Gone from None
            raise
        finally:
            with self._cond:
                slot.starting = None
        with self._cond:
            closing = self._closing
            if not closing:
                slot.worker = worker
        if closing:
            worker.kill()
            raise _Gone
        log.debug("worker %d started (slot %d)", worker.pid, slot.index)
        return worker

    def _retire(self, slot: _Slot) -> None:
        with self._cond:
            worker, slot.worker = slot.worker, None
        if worker is not None:
            worker.kill()

    def _slot_loop(self, slot: _Slot) -> None:
        try:
            if slot.index == 0:
                self._warm(slot)
            while True:
                with self._cond:
                    while not self._queue and not self._closing:
                        self._cond.wait()
                    if self._closing:
                        return
                    job = self._queue.popleft()
                    job.state, job._slot, slot.job = "running", slot, job
                    job.started = time.monotonic()
                    self._running += 1
                outcome = _CANCELLED
                try:
                    outcome = self._run(slot, job)
                finally:
                    with self._cond:
                        self._running -= 1
                        slot.job = None
                        deliver = outcome is not _CANCELLED and job.state == "running"
                        if deliver:
                            job.state, job.outcome = "done", outcome
                if deliver:
                    try:
                        job.on_done(job)
                    except Exception:  # noqa: BLE001 - one bad reply must not stop the slot
                        log.exception("could not deliver the outcome of a call")
                if slot.worker is not None and slot.worker.killed:
                    self._retire(slot)  # killed by a cancel or by close()
                if slot.worker is None:
                    self._warm(slot)  # the replacement starts now, not on the next call
        finally:
            self._retire(slot)

    def _warm(self, slot: _Slot) -> None:
        if self._closing:
            return
        try:
            self._spawn(slot)
        except _Gone:
            pass
        except Exception:  # noqa: BLE001 - retried by the next call
            log.exception("a worker process did not start")

    def _deadline(self, call: Any) -> float | None:
        cap = getattr(getattr(call.context, "caps", None), "max_seconds", None)
        if not isinstance(cap, (int, float)):
            return None
        return time.monotonic() + float(cap) + self.grace_seconds

    def _run(self, slot: _Slot, job: Job) -> Any:
        call = job.call
        worker = slot.worker
        if worker is None:
            try:
                worker = self._spawn(slot)
            except _Gone:
                return _CANCELLED
            except Exception:  # noqa: BLE001
                log.exception("a worker process did not start")
                return RemoteError("internal", "the worker process could not start")
        if job.state != "running":
            return _CANCELLED  # cancelled while its worker started
        with self._cond:
            self._next_id += 1
            job_id = self._next_id
        try:
            _, outcome, abandoned = worker.call(job_id, call, self._deadline(call))
        except TimeoutError:
            self._retire(slot)
            cap = call.context.caps.max_seconds
            log.info("tool %s: no answer %gs past its time cap; worker killed", call.name, cap)
            # the words of the registry's own time cap
            return RemoteError(
                "timeout",
                f"{call.name!r} ran longer than {cap:g} s and was abandoned",
                {"max_seconds": cap},
            )
        except (_Gone, pickle.UnpicklingError, EOFError):
            rc = worker.proc.poll()
            self._retire(slot)
            if job.state != "running" or self._closing:
                return _CANCELLED
            log.error("the worker running tool %s ended unexpectedly (status %s)", call.name, rc)
            return RemoteError("internal", "the worker process ended unexpectedly")
        except Exception:  # noqa: BLE001 - e.g. a context that does not pickle
            log.exception("tool %s could not be handed to a worker", call.name)
            self._retire(slot)
            return RemoteError("internal", "the call could not be handed to a worker")
        if abandoned:
            log.info("tool %s left a handler thread running; worker replaced", call.name)
            self._retire(slot)
        return _decode(outcome)
