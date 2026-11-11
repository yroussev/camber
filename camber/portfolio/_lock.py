"""Single-writer advisory lock on a portfolio root (``<root>/_lock``).

``fcntl.flock`` on POSIX, ``msvcrt.locking`` on Windows. The kernel drops the lock when the holding
process exits -- including a crash or ``kill -9`` -- so a leftover ``_lock`` file is never a stale
*lock*, only stale *text*: the next writer simply acquires it and overwrites the holder line. The
holder line (``pid``, ``host``, ``since``) exists only to make the refusal message useful.

Caveat: advisory file locks are unreliable on some network filesystems (older NFS, SMB with
oplocks disabled). Keep the portfolio root on a local disk or a filesystem with working
``flock`` semantics; see docs/PORTFOLIO.md.

**A writer on another system (0.112, #325).** ``flock`` excludes only processes of one kernel: a
macOS host and a Docker Desktop container writing one bind-mounted directory (or two machines on
one share) both get it. So, once it holds the ``flock``, a writer also claims ``<root>/_lock.owner``
(an exclusive create, which the filesystem decides for every system that shares it) and writes
there who it is: pid, host, the kernel's boot id (:func:`kernel_id`), and a heartbeat counter it
advances every :data:`CLAIM_BEAT` seconds until it lets go and removes the claim. A writer that
finds a claim waits, like for the ``flock``, and is refused when its wait runs out, naming the
holder and its system. A claim left by a writer that was killed is taken over: at once when its
process is seen to be gone (:func:`process_state`: same kernel and pid namespace, and that pid has
exited or names a process that started at another time), else once its heartbeat has not moved
for :data:`CLAIM_STALE` seconds. Writers older than 0.112 make no claim.

**A writer that lost its claim stops (0.112, #330).** The ``flock`` is not proof either: on a
Docker Desktop bind mount the ``flock`` of a descriptor that created the file excludes nothing,
not even two processes of one container (see #335 below), so a claim of this kernel is no longer
taken over on the strength of the ``flock`` alone. And a writer
stopped for longer than :data:`CLAIM_STALE` (Ctrl-Z, a debugger, a sleeping machine) cannot beat,
so a writer on another system rightly takes its claim over; the stopped one must not carry on
when it resumes. So, immediately before each step that changes a directory -- a partition swap
or delete, a write fence, a registry or manifest write, an append's renames --
:func:`confirm_claims` checks that every claim this process holds still carries its token, and
raises the lock's own error (a :class:`ClaimLost`) when one does not: the writer stops, leaving
the directory as the new holder has it. A writer whose last heartbeat is old beats first, so a
waiter that was about to take a claim over sees it alive. What remains is a writer stopped for
more than :data:`CLAIM_STALE` seconds between that check and the single rename that follows it;
when such a writer lets go and finds its claim taken, it raises the same error, so its command
fails rather than reports success.

**Who takes a stopped writer's claim over (0.112, #335).** On a Docker Desktop bind mount the
``flock`` of the descriptor whose open created the lock file excludes nothing, while that of any
other descriptor excludes every process of the VM (all its containers share its kernel); so
:func:`_open` never locks the creating descriptor. A writer stopped while it holds the lock keeps
it: every writer of the same kernel (another process of the host, or any container of the same
VM) waits for it, as for any live holder, and is refused when its wait runs out, told the holder
is stopped (its heartbeat stood still for :data:`CLAIM_STALE` seconds of the wait). Only a writer
on another system, which the ``flock`` does not reach, takes such a claim over.

A claim is never read empty or partial (#335): it is written whole under a temporary name and
hard-linked into place (the link is the exclusive create), and a heartbeat writes a record of the
same length over it. A reader that still finds one mid-write (a writer before #335) names the
holder by the last whole record it read, and takes the claim over only once it has stayed
unchanged for :data:`CLAIM_STALE` seconds.

The lock is **re-entrant within a process**: a CLI command that holds it can call the registry,
which takes it again, without deadlocking. Other threads of the same process wait on an in-process
lock; other processes are refused (or wait, with ``timeout``).

:func:`exclusive_lock` is the same lock under another name and error, for directories that are not
portfolios: a plain store (:func:`camber.store._lock.store_lock`) and a dataset cache
(:func:`camber.datasets._paths.cache_lock`) take it on ``<dir>/_lock`` (0.103, #124).

**How long a store or cache writer waits (0.104, #135).** :func:`default_lock_timeout` is the wait
the store and cache locks use when no ``timeout`` is passed: a :func:`lock_wait` scope (what the
CLI's ``--lock-timeout SECONDS`` sets), else ``$CAMBER_LOCK_TIMEOUT``, else 30 seconds. ``0``
fails at once. Portfolio admin commands keep their own wait (``--wait``, default 0).
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as _dt
import json
import math
import os
import socket
import sys
import threading
import time

LOCK_FILE = "_lock"
CLAIM_FILE = "_lock.owner"  # 0.112 (#325): the holder's claim, seen by every system sharing it
CLAIM_BEAT = 1.0  # seconds between a holder's heartbeats
CLAIM_STALE = 10.0  # seconds without a heartbeat before a claim of another kernel is stale
LOCK_TIMEOUT_ENV = "CAMBER_LOCK_TIMEOUT"
DEFAULT_LOCK_TIMEOUT = 30.0  # seconds a second store or cache writer waits before it is refused

# The wait set by lock_wait() for this thread / task (None: not set).
_WAIT: contextvars.ContextVar = contextvars.ContextVar("camber_lock_wait", default=None)


def parse_lock_timeout(value, *, source: str = "--lock-timeout") -> float:
    """``value`` as a lock wait in seconds: a finite number >= 0, else ``ValueError`` naming
    ``source`` (the flag or variable it came from)."""
    try:
        seconds = float(str(value).strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        seconds = math.nan
    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError(
            f"{source} must be a number of seconds >= 0 (0 fails at once), not {value!r}"
        )
    return seconds


def default_lock_timeout(fallback: float = DEFAULT_LOCK_TIMEOUT) -> float:
    """The store / cache lock wait when none is passed: the innermost :func:`lock_wait`, else
    ``$CAMBER_LOCK_TIMEOUT`` (``ValueError`` if it is not a number >= 0), else ``fallback``."""
    wait = _WAIT.get()
    if wait is not None:
        return wait
    env = os.environ.get(LOCK_TIMEOUT_ENV, "").strip()
    if env:
        return parse_lock_timeout(env, source=f"${LOCK_TIMEOUT_ENV}")
    return fallback


@contextlib.contextmanager
def lock_wait(seconds):
    """Within the block, store and cache locks taken without an explicit ``timeout`` wait
    ``seconds`` (validated; ``None`` leaves the current default in force). Per thread: a worker
    thread starts without it, so wrap the worker's job in its own scope."""
    if seconds is None:
        yield
        return
    token = _WAIT.set(parse_lock_timeout(seconds))
    try:
        yield
    finally:
        _WAIT.reset(token)


class LockHeld(RuntimeError):
    """Another process holds a directory's single-writer lock (see :func:`exclusive_lock`)."""


class PortfolioLocked(LockHeld):
    """Another process holds the portfolio's single-writer lock."""


class ClaimLost(LockHeld):
    """This process's claim on a directory was taken over by a writer on another system while it
    held the lock (0.112, #330): it was stopped or stalled for longer than :data:`CLAIM_STALE`.
    Raised as a subclass of the lock's own error too (``StoreLocked``, ``PortfolioLocked``, ...),
    so a caller that handles that error handles this one."""


_LOST_TYPES: dict = {}


def _lost_type(error: type) -> type:
    if issubclass(error, ClaimLost):
        return error
    with _HELD_GUARD:
        t = _LOST_TYPES.get(error)
        if t is None:
            t = _LOST_TYPES[error] = type(
                error.__name__, (error, ClaimLost), {"__module__": error.__module__}
            )
        return t


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class _Held:
    __slots__ = ("fh", "depth", "rlock", "claim", "what", "error")

    def __init__(self):
        self.fh = None
        self.depth = 0
        self.rlock = threading.RLock()
        self.claim = None
        self.what = "directory"
        self.error = LockHeld


_HELD: dict = {}
_HELD_GUARD = threading.Lock()


def _slot(path: str) -> _Held:
    with _HELD_GUARD:
        h = _HELD.get(path)
        if h is None:
            h = _HELD[path] = _Held()
        return h


if sys.platform == "win32":  # pragma: no cover - exercised on Windows CI only
    import msvcrt

    _INFO_OFFSET = 1  # byte 0 is the locked byte; the holder line follows it

    def _try_lock(fh) -> bool:
        fh.seek(0)
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        fh.seek(0)
        with contextlib.suppress(OSError):
            msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)

else:
    import fcntl

    _INFO_OFFSET = 1

    def _try_lock(fh) -> bool:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False

    def _unlock(fh) -> None:
        with contextlib.suppress(OSError):
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)


def read_holder(root: str) -> dict:
    """The holder line last written to ``<root>/_lock`` (``{}`` if none/unreadable)."""
    try:
        with open(os.path.join(root, LOCK_FILE), "rb") as fh:
            fh.seek(_INFO_OFFSET)
            raw = fh.read(4096).decode("utf-8", "replace").strip()
        data = json.loads(raw) if raw else {}
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def probe(root: str):
    """The holder dict if another process holds ``<root>/_lock`` right now, else ``None``."""
    path = os.path.join(root, LOCK_FILE)
    if not os.path.isfile(path):
        return None
    slot = _slot(os.path.realpath(path))
    if slot.depth:  # held by this process
        return read_holder(root) or {"pid": os.getpid()}
    with _open(path) as fh:
        if _try_lock(fh):
            _unlock(fh)
            return None
    return read_holder(root) or {}


def _pid_alive(pid) -> bool:
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError, TypeError):
        return False
    return True


def describe_holder(holder: dict) -> str:
    """``"<pid>@<host> since <ts>"`` for a holder dict (``"an unknown process"`` if empty)."""
    if not holder:
        return "an unknown process"
    return f"{holder.get('pid', '?')}@{holder.get('host', '?')} since {holder.get('since', '?')}"


def _write_holder(fh) -> None:
    info = {"pid": os.getpid(), "host": socket.gethostname(), "since": _utc_now()}
    fh.seek(0)
    fh.truncate()
    fh.write(b"\n" + json.dumps(info, sort_keys=True).encode("utf-8") + b"\n")
    fh.flush()
    with contextlib.suppress(OSError):
        os.fsync(fh.fileno())


def _open(path: str):
    """``path`` opened for its ``flock``, never by the call that created it (0.112, #335): on a
    Docker Desktop bind mount the ``flock`` of a descriptor whose open created the file excludes
    nothing, not even within one container, while that of any other descriptor excludes every
    process of the VM."""
    while True:
        try:
            fd = os.open(path, os.O_RDWR)
            break
        except FileNotFoundError:
            with contextlib.suppress(FileExistsError):
                os.close(os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644))
    return os.fdopen(fd, "r+b")


_KERNEL: list = []


def kernel_id() -> str:
    """This kernel's identity for a lock claim (0.112, #325): its boot id on Linux (a Docker
    Desktop container has the VM's, shared by every container), the boot session on macOS, else
    the host name. Two processes with one id share ``flock``; two with different ids may not."""
    if not _KERNEL:
        kid = None
        try:
            if sys.platform.startswith("linux"):
                with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as fh:
                    kid = "linux:" + fh.read().strip()
            elif sys.platform == "darwin":
                import ctypes

                buf = ctypes.create_string_buffer(64)
                size = ctypes.c_size_t(len(buf))
                libc = ctypes.CDLL(None, use_errno=True)
                if not libc.sysctlbyname(
                    b"kern.bootsessionuuid", buf, ctypes.byref(size), None, ctypes.c_size_t(0)
                ):
                    kid = "darwin:" + buf.value.decode("ascii", "replace")
        except (OSError, AttributeError, ValueError):
            kid = None
        _KERNEL.append(kid if kid and kid.split(":", 1)[1] else "host:" + socket.gethostname())
    return _KERNEL[0]


_PIDNS: list = []


def pid_namespace():
    """This process's pid namespace (``"pid:[4026531836]"`` on Linux; ``""`` on a system with one
    per kernel), or ``None`` when it cannot be read. Two containers on one Docker Desktop VM share
    its kernel id but not their pid namespaces: a pid means nothing across them (0.112, #330)."""
    if not _PIDNS:
        if sys.platform.startswith("linux"):
            try:
                ns = os.readlink("/proc/self/ns/pid")
            except OSError:
                ns = None
        else:
            ns = ""
        _PIDNS.append(ns)
    return _PIDNS[0]


def process_identity() -> dict:
    """This process as a record another process can test with :func:`process_state`: its pid,
    kernel id, pid namespace and start time (0.112, #330)."""
    from ..store._fence import _started  # lazy: the store package imports the portfolio

    pid = os.getpid()
    return {
        "pid": pid,
        "kernel": kernel_id(),
        "pidns": pid_namespace(),
        "started": _started(pid),
    }


def process_state(rec):
    """Whether the process a :func:`process_identity` record names is running: ``True`` or
    ``False`` when this process can tell, ``None`` when it cannot -- the record is of another
    kernel or pid namespace (another machine, the host of a container, another container), or
    lacks those fields (0.112, #330). A pid of this namespace that started at another time than
    the record says is another process: the recorded one is gone (pid 1 of a new container, a
    reused pid). Unsure counts as running."""
    if not isinstance(rec, dict) or os.name == "nt":
        return None
    ns = pid_namespace()
    if ns is None or rec.get("kernel") != kernel_id() or rec.get("pidns") != ns:
        return None
    from ..store._fence import _started  # lazy: the store package imports the portfolio
    from ..store._leftovers import _process_alive

    pid = rec.get("pid")
    if isinstance(pid, bool) or not isinstance(pid, int) or not 0 < pid <= 2**31 - 1:
        return False  # names no process
    if not _process_alive(pid):
        return False
    started = rec.get("started")
    if isinstance(started, str) and started:
        now = _started(pid)
        return now is None or now == started
    return pid != os.getpid()  # with no start time, this process is not the one recorded


class _Claim:
    """This process's claim on ``<root>/_lock.owner`` and its heartbeat thread. Made by
    :meth:`place`, which a reader never sees empty or partial (0.112, #335)."""

    def __init__(self, path: str):
        self.path, self.fd = path, -1  # the claim's descriptor once placed
        self.info = {
            **process_identity(),  # pid, kernel, pid namespace, start time (#330)
            "host": socket.gethostname(),
            "since": _utc_now(),
            "token": os.urandom(8).hex(),
        }
        self.n = 0
        self.last = time.monotonic()  # when the last heartbeat was written
        self.lost = None  # the record that replaced this claim, once it is found taken over
        self.wlock = threading.Lock()
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None

    def place(self) -> bool:
        """Put the claim in place, whole, unless one exists; whether it did (0.112, #335).

        The record is written to a temporary file first and hard-linked to the claim's name:
        the link is the exclusive create (it fails when a claim exists), and the claim appears
        with its record in it, so a reader never finds it empty. Where the filesystem has no hard
        links, an exclusive create followed by the write."""
        tmp = f"{self.path}.tmp-{os.getpid()}-{os.urandom(4).hex()}"
        self.fd = os.open(tmp, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o644)
        try:
            self._write()
            try:
                os.link(tmp, self.path)
            except FileExistsError:
                return self._discard()
            except OSError:  # no hard links here: the claim is briefly empty to a reader
                self._discard()
                self._create()
        except BaseException:
            if self.fd >= 0:
                self._discard()
            raise
        finally:
            with contextlib.suppress(OSError):
                os.remove(tmp)
        if self.fd < 0:
            return False
        self.thread = threading.Thread(target=self._beat, name="camber-lock-claim", daemon=True)
        self.thread.start()
        return True

    def _discard(self) -> bool:
        os.close(self.fd)
        self.fd = -1
        return False

    def _create(self) -> None:
        try:
            self.fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            return
        try:
            self._write()
        except BaseException:
            self._discard()
            with contextlib.suppress(OSError):
                os.remove(self.path)
            raise

    def _write(self) -> None:
        # the same length every time, written over the old record: never empty to a reader
        rec = json.dumps({**self.info, "beat": f"{self.n:012d}"}, sort_keys=True) + "\n"
        os.lseek(self.fd, 0, os.SEEK_SET)
        os.write(self.fd, rec.encode("utf-8"))
        with contextlib.suppress(OSError):
            os.fsync(self.fd)
        self.last = time.monotonic()

    def _beat(self) -> None:
        while not self.stop.wait(CLAIM_BEAT):
            with self.wlock, contextlib.suppress(OSError):
                self.n += 1
                self._write()

    def owned(self):
        """``None`` while this claim is still the one in the file, else the record that replaced
        it (``{}`` when there is none): see :func:`confirm_claims`."""
        if self.lost is not None:
            return self.lost
        with self.wlock:
            if time.monotonic() - self.last > 2 * CLAIM_BEAT:  # stopped or starved: beat now,
                with contextlib.suppress(OSError):  # so a waiter about to take over sees it alive
                    self.n += 1
                    self._write()
        for _ in range(4):  # a takeover that puts a live claim back leaves it absent briefly
            raw, rec = _read_claim(self.path)
            if raw and rec is not None:
                break
            time.sleep(0.05)
        if rec is not None and rec.get("token") == self.info["token"]:
            return None
        self.lost = rec or {}
        return self.lost

    def release(self) -> bool:
        """Stop the heartbeat and remove the claim if it is still this one; whether it was."""
        lost = self.owned()
        self.stop.set()
        if self.thread is not None:
            self.thread.join()
        os.close(self.fd)
        if lost is None:
            with contextlib.suppress(OSError):
                os.remove(self.path)
        return lost is None


def _read_claim(path: str) -> tuple:
    """``(raw bytes, record)`` of a claim file; ``(None, None)`` when there is none, and the
    record ``None`` when it does not parse (``b""`` when it cannot be read)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read(4096)
    except FileNotFoundError:
        return None, None
    except OSError:
        return b"", None
    try:
        rec = json.loads(raw.decode("utf-8"))
    except ValueError:
        return raw, None
    return raw, rec if isinstance(rec, dict) else None


def _try_claim(root: str):
    """A new :class:`_Claim` on ``<root>/_lock.owner``, or ``None`` when one exists."""
    claim = _Claim(os.path.join(root, CLAIM_FILE))
    return claim if claim.place() else None


def _take_over(path: str, raw: bytes) -> None:
    """Remove the stale claim ``raw`` at ``path``; a claim made meanwhile is put back."""
    aside = f"{path}.stale-{os.getpid()}-{os.urandom(4).hex()}"
    try:
        os.rename(path, aside)
    except OSError:
        return
    try:
        if _read_claim(aside)[0] != raw:  # a new holder claimed it just now: its claim stays
            with contextlib.suppress(OSError):
                os.link(aside, path)  # never over a claim made since
    finally:
        with contextlib.suppress(OSError):
            os.remove(aside)


def _claim(root: str, deadline: float, poll: float, refuse):
    """Claim ``<root>/_lock.owner`` (the caller holds the ``flock``), waiting for a live holder
    on another system until ``deadline``; ``refuse(holder, note)`` raises when the wait runs
    out. See the module docstring."""
    path = os.path.join(root, CLAIM_FILE)
    seen, since = None, time.monotonic()
    last = None  # the last whole record read: a claim read mid-write still names its holder
    while True:
        claim = _try_claim(root)
        if claim is not None:
            return claim
        raw, rec = _read_claim(path)
        if raw is None:
            last = None
            continue  # released just now
        if rec is not None:
            last = rec
        now = time.monotonic()
        if raw != seen:
            seen, since = raw, now  # its heartbeat moved: alive
        # an empty or partial claim (one of a writer before #335, read mid-write) is live and
        # being written: never gone, and stale only once it has not changed for CLAIM_STALE
        # gone: its process is seen to have exited (this kernel and pid namespace, #330), or it
        # is this process, which holds no claim here at this depth; on Windows, a claim of this
        # machine, whose msvcrt lock the caller holds
        state = process_state(rec)
        gone = (
            state is False
            or (state is True and rec.get("pid") == os.getpid())
            or (os.name == "nt" and rec is not None and rec.get("kernel") == kernel_id())
        )
        if gone or now - since >= CLAIM_STALE:
            _take_over(path, raw)  # a writer killed while it held the lock
            seen = None
            continue
        if now >= deadline and rec is None and last is not None:
            state = process_state(last)  # what to name it by: the last whole record
        if now >= deadline and state is True:  # #330: a flock that excluded nothing
            refuse(
                last,
                " on this system, whose lock this filesystem does not enforce (as on a Docker"
                " Desktop bind mount); its claim stands while that process runs",
            )
        if now >= deadline and last is None:  # #335: never a whole record to name it by
            refuse(
                {},
                " whose claim was being written (its record was empty or partial when read); a"
                f" claim that stays so is taken over after {CLAIM_STALE:g} s",
            )
        if now >= deadline:
            refuse(
                last or {},
                f" on another system sharing this directory (kernel {(last or {}).get('kernel')}:"
                " a container, a VM or another machine); a claim whose writer was killed there is"
                f" taken over after {CLAIM_STALE:g} s without its heartbeat",
            )
        time.sleep(poll)


class _Watch:
    """How long the claim at ``path`` has gone unchanged while this process watched it (0.112,
    #335): a holder of this kernel's ``flock`` whose heartbeat stands still is stopped."""

    def __init__(self, path: str):
        self.path, self.raw, self.since = path, None, time.monotonic()

    def frozen(self) -> float:
        """Seconds the claim has stood still (``0`` with no claim of this kernel: one of 0.111
        or older, or another system's, which holds no lock here)."""
        raw, rec = _read_claim(self.path)
        now = time.monotonic()
        if not raw or rec is None or rec.get("kernel") != kernel_id() or raw != self.raw:
            self.raw, self.since = raw, now
            return 0.0
        return now - self.since


def _stopped_note(frozen: float) -> str:
    return (
        f" -- a writer that is alive but stopped (Ctrl-Z, `docker pause`, a debugger): its"
        f" heartbeat has not moved for {frozen:.0f} s. It keeps its system's lock until it"
        " resumes or exits, so every other writer of that system (the same host, or any"
        " container of the same Docker VM) waits for it; only a writer on another system takes"
        " its claim over. Resume it (`fg`, `docker unpause`) or end it"
    )


def _lost_message(what: str, path: str, rec: dict, *, after: bool = False) -> str:
    who = (
        f"{describe_holder(rec)} (kernel {rec.get('kernel')})"
        if rec
        else "a writer that has since let go"
    )
    if after:
        then = (
            "a step it took after its last check may have overlapped that writer's: check the"
            " directory (`camber store verify` for a store) and run the command again"
        )
    else:
        then = (
            "it stopped before changing anything more, so the directory is as that writer left"
            " it; run the command again"
        )
    return (
        f"{what}: this writer lost its claim on {path} -- it was stopped or stalled for over"
        f" {CLAIM_STALE:g} s (Ctrl-Z, a debugger, a sleeping machine) and {who} took it over;"
        f" {then}"
    )


def confirm_claims() -> None:
    """Check, immediately before a step that changes a locked directory, that every claim this
    process holds is still its own (0.112, #330); raise the lock's own error (also a
    :class:`ClaimLost`) naming the new holder when one is not. A no-op for a process that holds
    no lock. See the module docstring."""
    with _HELD_GUARD:
        slots = [s for s in _HELD.values() if s.claim is not None]
    for slot in slots:
        claim = slot.claim
        if claim is None:
            continue
        lost = claim.owned()
        if lost is not None:
            raise _lost_type(slot.error)(_lost_message(slot.what, claim.path, lost))


def portfolio_lock(root: str, *, timeout: float = 0.0, poll: float = 0.1):
    """Hold ``<root>/_lock`` for the ``with`` block; raise :class:`PortfolioLocked` if taken.

    ``timeout`` seconds to wait for another process to release it (``0`` = fail at once). A holder
    line left behind by a dead process is stale text, not a held lock (see the module docstring);
    it is overwritten on acquire.
    """
    return exclusive_lock(root, timeout=timeout, poll=poll)


@contextlib.contextmanager
def exclusive_lock(
    root: str,
    *,
    timeout: float = 0.0,
    poll: float = 0.1,
    what: str = "portfolio",
    error: type = PortfolioLocked,
    hint: str = "",
):
    """Hold ``<root>/_lock`` for the ``with`` block; raise ``error`` if another process keeps it.

    The single-writer lock behind :func:`portfolio_lock`, for any directory: ``what`` names it in
    the refusal (``"<what> is locked by <pid>@<host> since <ts>"``) and ``hint`` is appended to
    that message. ``root`` must exist. Re-entrant within a process; see the module docstring.
    """
    path = os.path.realpath(os.path.join(root, LOCK_FILE))
    slot = _slot(path)
    deadline = time.monotonic() + max(0.0, timeout)
    got = slot.rlock.acquire(timeout=timeout) if timeout > 0 else slot.rlock.acquire(False)
    if not got:
        raise error(  # pragma: no cover - another thread of this process holds it
            f"{what} is locked by {describe_holder(read_holder(root))} (this process){hint}"
        )
    try:
        if slot.depth == 0:
            fh = _open(path)
            beat = _Watch(os.path.join(os.path.dirname(path), CLAIM_FILE))
            while not _try_lock(fh):
                frozen = beat.frozen()
                if time.monotonic() >= deadline:
                    fh.close()
                    holder = read_holder(root)
                    note = ""
                    if holder and holder.get("host") == socket.gethostname():
                        if not _pid_alive(holder.get("pid")):  # pragma: no cover - racy
                            note = " (that pid has exited; retry -- the lock is released)"
                    if not note and frozen >= CLAIM_STALE:  # #335
                        note = _stopped_note(frozen)
                    raise error(f"{what} is locked by {describe_holder(holder)}{note}{hint}")
                time.sleep(poll)

            def refuse(holder, note):
                raise error(f"{what} is locked by {describe_holder(holder)}{note}{hint}")

            try:
                slot.claim = _claim(os.path.dirname(path), deadline, poll, refuse)  # #325
            except BaseException:
                _unlock(fh)
                fh.close()
                raise
            _write_holder(fh)
            slot.fh, slot.what, slot.error = fh, what, error
        slot.depth += 1
        failed = None
        try:
            yield
        except BaseException as exc:
            failed = exc
            raise
        finally:
            slot.depth -= 1
            if slot.depth == 0 and slot.fh is not None:
                fh, slot.fh = slot.fh, None
                claim, slot.claim = slot.claim, None
                try:
                    if claim is not None and not claim.release():
                        # #330: the claim was taken over while this writer held the lock
                        lost = _lost_type(error)
                        if failed is None:
                            raise lost(_lost_message(what, claim.path, claim.lost, after=True))
                        if not isinstance(
                            failed, (ClaimLost, KeyboardInterrupt, SystemExit, GeneratorExit)
                        ):
                            # e.g. its staging removed by the new holder: say why, not ENOENT
                            raise lost(_lost_message(what, claim.path, claim.lost)) from failed
                finally:
                    _unlock(fh)
                    fh.close()
    finally:
        slot.rlock.release()
