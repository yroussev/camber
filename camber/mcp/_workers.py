"""Killable worker processes for ``tools/call`` (0.111, #292).

Python cannot stop a thread, and a pandas / Arrow scan never checks a flag, so a call that timed
out, was cancelled or whose client has gone can only be stopped by ending the process it runs in.
:class:`WorkerPool` keeps at most ``max_concurrent`` worker processes, each a fresh interpreter
that builds the capability registry once and then runs :meth:`Registry.call` for one call at a
time -- the same call, under the same context, with the same validation, policy, no-persist scope,
redaction and citation checks as in-process; the worker only carries the outcome back. A worker is
killed (``SIGKILL``, with its whole process group: matplotlib's font scan runs child processes)
and replaced when its call is cancelled, runs past its time cap, leaves a handler thread behind,
grew past ``recycle_bytes`` of memory, or the server shuts down; the next call finds a warm
replacement. A worker found dead while idle is replaced before a call is given to it (0.111, #309).
A worker that dies while starting does not lose the call it was meant for: a fresh one is tried,
then the call answers ``unavailable`` (#316).

Calls beyond the running ones wait in a FIFO queue of at most ``max_queued``; a call beyond that
is refused at once (``busy``). Each call is a :class:`Job` with a ``state`` (``queued``,
``running``, ``done``, ``cancelled``), the hook a task layer (MCP Tasks, #279) polls and cancels.

The parent and a worker talk over the worker's stdin / stdout: length-prefixed pickles (the parent
only ever unpickles what its own child wrote). Inside the worker, fd 1 points at stderr and fd 0 at
``/dev/null``, so a handler's stray output lands in the log. A worker exits on its own when the
parent closes the pipe or dies (killing its own process group first), and always through
``os._exit`` (no interpreter teardown under a running Arrow scan).
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
#: the largest ``max_concurrent`` / ``max_queued`` a pool accepts (#309): each slot is a thread
#: and a worker process of about 110 MB, each queued call a message held in memory
MAX_CONCURRENT = 64
MAX_QUEUED = 1024
#: a worker is replaced after a call that took its peak memory past this many bytes (#309) and
#: past twice its steady state, the peak after its first call (#316)
RECYCLE_BYTES = 1 << 30
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


def _peak_rss() -> int:
    """This process's peak resident memory in bytes (0 when unknown)."""
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except (ImportError, OSError, ValueError):
        return 0
    return int(peak) if sys.platform == "darwin" else int(peak) * 1024  # Linux: KiB


def _process_tree(root: int, *, session: bool = False) -> list:
    """``root`` and every process descended from it, children first found by one ``ps`` (#309):
    a process group is not enough, since macOS's ``system_profiler`` (which matplotlib's font
    scan runs) starts its helper in a group of its own. With ``session``, also every process in
    the session ``root`` leads: those whose parent chain was cut when ``root`` died (#316)."""
    try:
        out = subprocess.run(
            ["ps", "-axo", "pid=,ppid="], capture_output=True, text=True, timeout=5
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return [root]
    children: dict = collections.defaultdict(list)
    for line in out.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].isdigit() and parts[1].isdigit():
            children[int(parts[1])].append(int(parts[0]))
    tree, todo = [], [root]
    while todo:
        pid = todo.pop()
        if pid not in tree:
            tree.append(pid)
            todo += children.get(pid, [])
    if session:
        for pid in sorted({p for kids in children.values() for p in kids} - set(tree)):
            try:
                if os.getsid(pid) == root:
                    tree.append(pid)
            except OSError:  # gone, or another user's
                pass
    return tree


def _kill_tree(root: int, *, group: bool) -> None:
    """SIGKILL ``root``'s descendants, then (``group``) its process group and session, which
    ``root`` leads; every pid is collected before any is killed, so none is reparented out of
    reach. ``root`` may have ended already: what it started is found by its session (#316)."""
    for pid in _process_tree(root, session=group)[1:]:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    if group:
        try:
            os.killpg(root, signal.SIGKILL)
        except OSError:
            pass


def _exit_group(status: int) -> None:  # pragma: no cover - runs in the worker process
    """End this worker and every process it started (it leads its own process group)."""
    if os.name == "posix":
        _kill_tree(os.getpid(), group=os.getpgrp() == os.getpid())
    os._exit(status)


def _watch_parent(parent: int) -> None:
    while True:
        time.sleep(0.5)
        if os.getppid() != parent:
            _exit_group(0)


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
            _write_frame(replies, ("taken", job_id))  # the parent may retry a call not taken
            outcome = _outcome(registry, name, arguments, ctx)
            try:
                _write_frame(replies, (job_id, outcome, _abandoned_threads(), _peak_rss()))
            except _Gone:
                raise
            except Exception:  # noqa: BLE001 - a result that does not pickle
                log.exception("the result of %s could not be sent", name)
                _write_frame(replies, (job_id, ("exc", "UnpicklingError"), True, 0))
    except _Gone:
        _exit_group(0)
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
            # its own process group, killed whole (#309): matplotlib's font scan starts
            # fc-list / system_profiler, which would otherwise outlive a killed worker
            start_new_session=os.name == "posix",
        )
        assert self.proc.stdin is not None and self.proc.stdout is not None
        self.requests, self.replies = self.proc.stdin, self.proc.stdout
        self.reader = _Reader(self.replies.fileno())
        self.killed = False
        self.taken = False  # whether the worker acknowledged the call last sent
        self.steady: int | None = None  # its peak memory after its first call (#316)

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

    def alive(self) -> bool:
        """Whether the process is still running (reaps it when it is not; the owner's thread)."""
        return self.proc.poll() is None

    def send(self, job_id: int, call: Any) -> None:
        """Hand ``call`` to the worker; ``_Gone`` when it is no longer there to take it."""
        self.taken = False
        _write_frame(self.requests.fileno(), (job_id, call.name, call.arguments, call.context))

    def receive(self, job_id: int, deadline: float | None) -> tuple:
        while True:
            reply = self.reader.read(deadline)
            if reply == ("taken", job_id):
                self.taken = True
            elif isinstance(reply, tuple) and reply and reply[0] == job_id:
                return reply

    def signal_kill(self) -> None:
        """SIGKILL the worker and every process it started (any thread; the owner reaps), also
        when the worker itself was already killed from outside (#316)."""
        if os.name == "posix" and not self.killed:
            _kill_tree(self.proc.pid, group=True)
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
    ``max_queued`` calls wait for a free worker; :meth:`submit` refuses more (``None``). The time
    cap counts from when a call reaches a worker, not from when it was queued. A worker is
    replaced after a call that took its peak memory past ``recycle_bytes`` and past twice its
    steady state, the peak after its first call (``None``: never; #316: a facility whose load
    alone passes ``recycle_bytes`` no longer reloads on every call).

    :func:`camber.mcp.serve` starts the pool and closes it at the end of the session.
    """

    def __init__(
        self,
        registry_factory: str,
        *,
        max_concurrent: int = 2,
        max_queued: int = 8,
        grace_seconds: float = 1.0,
        start_seconds: float = 60.0,
        recycle_bytes: int | None = RECYCLE_BYTES,
    ):
        if not 1 <= int(max_concurrent) <= MAX_CONCURRENT:
            raise ValueError(f"max_concurrent must be from 1 to {MAX_CONCURRENT}")
        if not 0 <= int(max_queued) <= MAX_QUEUED:
            raise ValueError(f"max_queued must be from 0 to {MAX_QUEUED}")
        self.registry_factory = registry_factory
        self.max_concurrent = int(max_concurrent)
        self.max_queued = int(max_queued)
        self.grace_seconds = float(grace_seconds)
        self.start_seconds = float(start_seconds)
        self.recycle_bytes = None if recycle_bytes is None else int(recycle_bytes)
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
                        if not self._cond.wait(1.0):
                            break  # a look at the idle worker, below
                    if self._closing:
                        return
                    job = self._queue.popleft() if self._queue else None
                    if job is not None:
                        job.state, job._slot, slot.job = "running", slot, job
                        job.started = time.monotonic()
                        self._running += 1
                if job is None:
                    self._replace_if_dead(slot)
                    continue
                waited = job.started - job.submitted
                if waited >= 1.0:
                    log.info("tool %s waited %.1fs for a free worker", job.call.name, waited)
                outcome = _CANCELLED
                try:
                    outcome = self._run(slot, job)
                except Exception:  # noqa: BLE001 - every call taken is answered (#316)
                    log.exception("tool %s: the worker pool failed", job.call.name)
                    self._retire(slot)
                    outcome = RemoteError("internal", "the call could not be run in a worker")
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

    def _replace_if_dead(self, slot: _Slot) -> bool:
        """Replace the slot's worker when it ended on its own while idle (a crash, or the OS
        killing it for memory, #309); whether it had."""
        worker = slot.worker
        if worker is None or worker.killed or worker.alive():
            return False
        log.warning(
            "worker %d ended while idle (status %s); starting a replacement",
            worker.pid,
            worker.proc.returncode,
        )
        self._retire(slot)
        self._warm(slot)
        return True

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
        with self._cond:
            self._next_id += 1
            job_id = self._next_id
        for attempt in (1, 2):  # a worker that died before it took the call: once more
            if slot.worker is not None and not slot.worker.killed and not slot.worker.alive():
                self._replace_if_dead(slot)
            worker = slot.worker
            if worker is None:
                try:
                    worker = self._spawn(slot)
                except _Gone:
                    if job.state != "running" or self._closing:
                        return _CANCELLED
                    # #316: it died while starting (the system stopping processes for memory),
                    # which is no cancellation: the call is answered all the same
                    if attempt == 2:
                        return self._worker_lost(call, None)
                    log.warning(
                        "a worker ended while starting for tool %s; trying a fresh one", call.name
                    )
                    continue
                except Exception:  # noqa: BLE001
                    log.exception("a worker process did not start")
                    return RemoteError("internal", "the worker process could not start")
            if job.state != "running":
                return _CANCELLED  # cancelled while its worker started
            try:
                worker.send(job_id, call)
                _, outcome, abandoned, peak = worker.receive(job_id, self._deadline(call))
                break
            except TimeoutError:
                self._retire(slot)
                cap = call.context.caps.max_seconds
                log.info(
                    "tool %s: no answer within its %gs time cap; worker killed", call.name, cap
                )
                return _timeout(call.name, cap)
            except (_Gone, pickle.UnpicklingError, EOFError):
                self._retire(slot)
                if job.state != "running" or self._closing:
                    return _CANCELLED
                if worker.taken or attempt == 2:
                    return self._worker_lost(call, worker)
                log.warning(
                    "worker %d ended (status %s) before it took tool %s; trying a fresh one",
                    worker.pid,
                    worker.proc.returncode,
                    call.name,
                )
            except Exception:  # noqa: BLE001 - e.g. a context that does not pickle
                log.exception("tool %s could not be handed to a worker", call.name)
                self._retire(slot)
                return RemoteError("internal", "the call could not be handed to a worker")
        assert worker is not None  # the loop ends only by answering or by ``break``
        if abandoned:
            log.info("tool %s left a handler thread running; worker replaced", call.name)
            self._retire(slot)
            if outcome[0] == "err" and outcome[1] == "timeout":  # stopped now, not abandoned
                return _timeout(call.name, call.context.caps.max_seconds)
        elif self._outgrown(worker, peak):
            log.info(
                "tool %s: its worker grew to %d MB (%d MB after its first call); replaced to "
                "release the memory",
                call.name,
                peak >> 20,
                (worker.steady or 0) >> 20,
            )
            self._retire(slot)
        if worker.steady is None:
            worker.steady = peak
        return _decode(outcome)

    def _outgrown(self, worker: _Worker, peak: int) -> bool:
        """Whether a call took ``worker`` well past its steady state (#316): the memory a call
        of the usual size needs, kept for the next one, is not worth a reload; a much larger
        call's is released."""
        if self.recycle_bytes is None or worker.steady is None:
            return False
        return peak > max(self.recycle_bytes, 2 * worker.steady)

    @staticmethod
    def _worker_lost(call: Any, worker: _Worker | None) -> RemoteError:
        """The tool error for a call whose worker died under it (#309), or whose workers died
        while starting (``None``, #316): retryable."""
        if worker is None:
            log.error("the workers started for tool %s ended before they were ready", call.name)
        else:
            log.error(
                "the worker running tool %s ended unexpectedly (status %s)",
                call.name,
                worker.proc.returncode,
            )
        return RemoteError(
            "unavailable",
            f"the worker process running {call.name!r} ended unexpectedly (the system may have "
            "stopped it for lack of memory); retry the call, and if it fails again, narrow it "
            "(a shorter window, fewer items or a smaller limit)",
            {"retryable": True},
        )


def _timeout(name: str, cap: float) -> RemoteError:
    """The ``timeout`` of a call whose worker was killed at its time cap."""
    return RemoteError(
        "timeout", f"{name!r} ran longer than {cap:g} s and was stopped", {"max_seconds": cap}
    )
