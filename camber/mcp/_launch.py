"""``camber mcp``: build the call context from the launch configuration and serve stdio."""

from __future__ import annotations

import logging
import os
import sys
import time
from typing import Any

from ._server import SUPPORTED_VERSIONS, McpServer
from ._stdio import Terminated, _terminated, serve_stdio
from ._workers import MAX_CONCURRENT, MAX_QUEUED, WorkerPool

log = logging.getLogger("camber.mcp")


class LaunchError(ValueError):
    """The launch configuration cannot be served (a usage mistake, reported as one line)."""


def resolve_store(path: str) -> str:
    """The ParquetStore root for ``path``: the store itself, or a portfolio workspace's store."""
    from ..api.server import _store_problem
    from ..portfolio import Portfolio, is_workspace

    if not str(path).strip():  # #304: "" is not the current directory
        raise LaunchError("--store: give the store directory (an empty path names none)")
    if is_workspace(path):
        return Portfolio(path).store_root
    problem = _store_problem(path)
    if problem is not None:
        raise LaunchError(problem.replace("camber serve", "camber mcp --store"))
    return os.path.abspath(path)


def resolve_allowlist(store: str, allow: list | None, allow_all_active: bool) -> frozenset:
    """The facility allowlist: the ``--allow`` ids, or the store's active facilities right now.

    0.111 (#290): both read the facility registry fail-closed (:mod:`camber._servable`). With
    ``--allow-all-active`` a missing or damaged registry means no facility is active (a warning,
    and the server starts with nothing to serve); with ``--allow``, a warning names each facility
    that will be refused: research-only data, or a licence that cannot be established.
    """
    from .._servable import active_facilities, read_registry, refusal, registry_banner
    from ..store import ParquetStore

    if allow_all_active:
        active, warnings = active_facilities(store)
        for w in warnings:
            log.warning("--allow-all-active: %s", w)
        if not active and not warnings:
            raise LaunchError(f"--allow-all-active: the store {store} has no active facility")
        return frozenset(active)
    ids = [str(a).strip() for a in (allow or [])]
    if not ids or not all(ids):
        raise LaunchError(
            "name the facilities to serve with --allow ID, or pass --allow-all-active"
        )
    present = set(ParquetStore(store).facilities())
    for fid in sorted(set(ids) - present):
        log.warning("--allow %s: no such facility in the store (yet)", fid)
    registry = read_registry(store)
    if registry[1] is not None:
        log.warning("%s", registry_banner(registry[1]))
    for fid in sorted(set(ids)):
        why = refusal(fid, store, registry)
        if why is None or (why[0] != "research_only" and (registry[1] or fid not in present)):
            continue  # a registry problem is warned about once; a missing id above
        log.warning("--allow %s: %s; every call naming it is refused", fid, why[1])
    return frozenset(ids)


#: the run config formats ``--config`` reads (``camber run`` reads any other name as JSON)
CONFIG_SUFFIXES = (".json", ".yaml", ".yml")


def _same_dir(a: str, b: str) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def check_config(path: str, store: str, allowed: frozenset) -> str:
    """The facility the run config at ``path`` is for, once it passes the checks ``camber run``
    makes before it reads a trend (:func:`camber.config._preflight_config`), else a
    :class:`LaunchError` saying what is wrong (0.111, #296).

    On top of those: the file is ``.json``, ``.yaml`` or ``.yml``; it names a facility, one of
    ``allowed``; and a store source reads the ``--store`` directory (``store``), whatever path
    spells it (a different store would serve data the operator did not name).
    """
    return _checked_config(path, store, allowed)[0]


def _checked_config(path: str, store: str, allowed: frozenset) -> tuple:
    """:func:`check_config`'s ``(facility, loaded config)``."""
    from ..config import _config_facility, _preflight_config, _unknown_keys_note, load_config

    where = f"--config {path}"
    if not str(path).lower().endswith(CONFIG_SUFFIXES):
        raise LaunchError(f"{where}: a run config is a .json, .yaml or .yml file")
    if os.path.isdir(path):
        raise LaunchError(f"{where}: is a directory, not a run config file")
    if not os.path.isfile(path):
        raise LaunchError(f"{where}: no such file")
    try:
        config = load_config(path)
    except Exception as exc:  # noqa: BLE001 - bad JSON / YAML, no PyYAML, a wrong shape ...
        msg = str(exc)
        msg = msg[len(str(path)) + 2 :] if msg.startswith(f"{path}: ") else msg
        raise LaunchError(f"{where}: {msg}") from None
    fid = _config_facility(config)
    if not fid:
        raise LaunchError(
            f"{where}: names no facility (a store source's source.facility_id, else facility_id "
            "or site)"
        )
    base = os.path.dirname(os.path.abspath(path))
    source = config.get("source") or {}
    if isinstance(source, dict) and source.get("kind") == "store":
        cstore = str(source.get("store") or "")
        cstore = os.path.join(base, os.path.expanduser(cstore)) if cstore else ""
        if not cstore or not (_same_dir(cstore, store) or _same_dir(resolve_quiet(cstore), store)):
            raise LaunchError(
                f"{where}: its source.store {source.get('store')!r} is not the --store directory "
                f"{store}; the tools read only --store, so point the config at it"
            )
    if fid not in allowed:
        raise LaunchError(
            f"{where}: is for facility {fid!r}, which is not allowed (name it with --allow; "
            "with --allow-all-active it must be active)"
        )
    try:
        _preflight_config(config, base_dir=base)
    except (KeyError, ValueError, TypeError, OSError) as exc:
        why = exc.args[0] if isinstance(exc, KeyError) and exc.args else exc
        raise LaunchError(f"{where}: {why}") from None
    note = _unknown_keys_note(config)  # #304: a warning, as camber run gives
    if note:
        log.warning("%s: %s", where, note)
    return fid, config


def resolve_quiet(path: str) -> str:
    """:func:`resolve_store` of ``path``, or ``path`` itself when it is no store."""
    try:
        return resolve_store(path)
    except (LaunchError, OSError, ValueError):
        return path


def check_configs(configs, store: str, allowed: frozenset):
    """The absolute ``--config`` paths, each checked with :func:`check_config`: ``None`` for
    none, one path for one, a tuple for several (one per facility; two for the same facility
    are refused)."""
    return _checked_configs(configs, store, allowed)[0]


def _checked_configs(configs, store: str, allowed: frozenset) -> tuple:
    """:func:`check_configs`'s paths, and the ``((path, loaded config), ...)`` snapshot the
    calls read instead of the files (#308: an edit while the server ran bypassed the checks)."""
    if configs is None:
        return None, ()
    paths = [configs] if isinstance(configs, (str, os.PathLike)) else list(configs)
    seen: dict = {}
    snapshot = []
    for p in paths:
        fid, config = _checked_config(os.fspath(p), store, allowed)
        if fid in seen:
            raise LaunchError(
                f"--config {p} and --config {seen[fid]} are both for facility {fid!r}; give "
                "one config per facility"
            )
        seen[fid] = p
        snapshot.append((os.path.abspath(os.fspath(p)), config))
    out = tuple(path for path, _config in snapshot)
    if not out:
        return None, ()
    return (out[0] if len(out) == 1 else out), tuple(snapshot)


def build_context(
    store: str,
    *,
    allow: list | None = None,
    allow_all_active: bool = False,
    config=None,
    min_trust: float = 0.5,
) -> Any:
    """The ``camber.capabilities.CallContext`` every tool call of this launch runs under.

    ``config`` is one run config path or a list of them, one per facility; each is checked at
    startup (:func:`check_config`) and the calls read it as loaded then (#308)."""
    from ..capabilities import CallContext

    if not 0.0 <= float(min_trust) <= 1.0:
        raise LaunchError(f"--min-trust must be from 0 to 1 (got {min_trust})")
    root = resolve_store(store)
    allowed = resolve_allowlist(root, allow, allow_all_active)
    paths, snapshot = _checked_configs(config, root, allowed)
    return CallContext(
        store=root,
        allowed_facilities=allowed,
        config=paths,
        min_trust=float(min_trust),
        caller="mcp",
        # only when there is one: a registry of the same shape may not know the field
        **({"config_snapshot": snapshot} if snapshot else {}),
    )


def refused_facilities(ctx) -> dict:
    """``{facility_id: (kind, message)}`` for the allowed facilities every call refuses
    (research-only data, or a licence that cannot be established), read once at startup."""
    try:
        from .._servable import read_registry, refusal

        registry = read_registry(ctx.store)
        out = {}
        for fid in sorted(ctx.allowed_facilities):
            why = refusal(fid, ctx.store, registry)
            if why is not None:
                out[fid] = why
        return out
    except Exception:  # noqa: BLE001 - log lines only; every call checks again
        return {}


def _log_templates(ctx, refused: dict | None = None) -> None:
    """One stderr line per allowed catalog-dataset facility no ``--config`` names: the tools run
    it with the dataset's packaged config template (#296). A facility in ``refused`` is never
    served, so it gets no such line, and a ``--config`` for one is warned about (#314)."""
    refused = refused or {}
    try:
        from ..capabilities._common import dataset_template
        from ..capabilities._policy import config_paths, read_config
        from ..config import _config_facility

        named = set()
        for p in config_paths(ctx):
            fid = _config_facility(read_config(ctx, p))
            named.add(fid)
            if fid in refused:
                log.warning(
                    "--config %s: is for facility %s, which is not served (%s); every call "
                    "naming it is refused",
                    p,
                    fid,
                    refused[fid][1],
                )
        for fid in sorted(ctx.allowed_facilities - named - set(refused)):
            _cfg, dataset_id = dataset_template(ctx, fid)
            if dataset_id:
                log.info(
                    "facility %s: no --config names it; the tools use the config template of "
                    "catalog dataset %s",
                    fid,
                    dataset_id,
                )
    except Exception:  # noqa: BLE001 - a log line only
        pass


#: the matplotlib directory of one server: ``camber-mcp-mpl-<server pid>-<random>``
_MPL_PREFIX = "camber-mcp-mpl-"
#: a directory named without a process id (0.111 development builds) is swept once this old
_MPL_LEGACY_AGE = 86400.0


def _pid_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:  # e.g. another user's process: running
        return True
    return True


def sweep_matplotlib_dirs(tmpdir: str | None = None) -> int:
    """Remove the matplotlib directories that servers killed without a chance to clean up
    (``SIGKILL``) left in ``tmpdir``: this user's, whose server process is gone. The number
    removed (0.111, #309)."""
    import re
    import shutil
    import stat
    import tempfile

    tmpdir = tmpdir or tempfile.gettempdir()
    pattern = re.compile(re.escape(_MPL_PREFIX) + r"(?:(\d+)-)?[A-Za-z0-9_]+\Z")
    removed = 0
    try:
        names = os.listdir(tmpdir)
    except OSError:
        return 0
    uid = os.getuid() if hasattr(os, "getuid") else None
    for name in names:
        m = pattern.match(name)
        if m is None:
            continue
        path = os.path.join(tmpdir, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISDIR(st.st_mode) or (uid is not None and st.st_uid != uid):
            continue
        if m.group(1) is not None:
            if int(m.group(1)) == os.getpid() or _pid_running(int(m.group(1))):
                continue
        elif time.time() - st.st_mtime < _MPL_LEGACY_AGE:
            continue
        shutil.rmtree(path, ignore_errors=True)
        removed += not os.path.exists(path)
    return removed


def isolate_matplotlib() -> str | None:
    """Point matplotlib's config and font cache at a fresh temporary directory for this server and
    its worker processes, unless ``MPLCONFIGDIR`` is already set; the directory (to remove at
    exit), or ``None``.

    0.111 (#304): rcx_report draws its charts with matplotlib, which otherwise writes its font
    cache under ``$HOME`` on first use: the only file a read-only server would leave behind.
    #309: the name carries this process's id, and the directories of servers that were killed
    (``SIGKILL``, which no process can clean up after) are swept first."""
    import tempfile

    if os.environ.get("MPLCONFIGDIR"):
        return None
    try:
        sweep_matplotlib_dirs()
        path = tempfile.mkdtemp(prefix=f"{_MPL_PREFIX}{os.getpid()}-")
    except OSError:
        return None
    os.environ["MPLCONFIGDIR"] = path
    return path


def _on_signals():
    """Make ``SIGTERM`` and ``SIGHUP`` stop the session like Ctrl-C does (a log line, every call
    stopped, the temporary directory removed; #309): raise :class:`Terminated` in this thread,
    once. The previous handlers, to restore, or ``None`` off the main thread."""
    import signal
    import threading

    if threading.current_thread() is not threading.main_thread():
        return None
    wanted = [getattr(signal, n) for n in ("SIGTERM", "SIGHUP") if hasattr(signal, n)]

    def handler(signum, _frame):
        for sig in wanted:  # cleanup runs once; a second signal must not interrupt it
            signal.signal(sig, signal.SIG_IGN)
        raise Terminated(signum)

    previous = {}
    for sig in wanted:
        try:
            previous[sig] = signal.signal(sig, handler)
        except (OSError, ValueError):
            pass
    return previous


def _restore_signals(previous) -> None:
    import signal

    for sig, h in (previous or {}).items():
        try:
            signal.signal(sig, h if h is not None else signal.SIG_DFL)
        except (OSError, ValueError):
            pass


def _setup_logging() -> None:
    """INFO lines on stderr, unless the host process already configured logging."""
    logger = logging.getLogger("camber.mcp")
    if not logger.handlers and not logging.getLogger().handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("camber mcp: %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)


def launch(
    store: str,
    *,
    allow: list | None = None,
    allow_all_active: bool = False,
    config=None,
    min_trust: float = 0.5,
    max_concurrent: int = 2,
    max_queued: int = 8,
) -> int:
    """Run ``camber mcp`` until the client closes stdin; the exit status.

    Tool calls run in at most ``max_concurrent`` worker processes, with up to ``max_queued`` more
    waiting (#292); see :class:`WorkerPool`.
    """
    _setup_logging()
    try:
        import camber.capabilities as capabilities
    except ImportError as exc:
        print(
            f"error: camber mcp needs the capability registry (camber.capabilities): {exc}",
            file=sys.stderr,
        )
        return 1
    try:
        ctx = build_context(
            store,
            allow=allow,
            allow_all_active=allow_all_active,
            config=config,
            min_trust=min_trust,
        )
        if not 1 <= int(max_concurrent) <= MAX_CONCURRENT:
            raise LaunchError(f"--max-concurrent must be from 1 to {MAX_CONCURRENT}")
        if not 0 <= int(max_queued) <= MAX_QUEUED:
            raise LaunchError(f"--max-queued must be from 0 to {MAX_QUEUED}")
    except LaunchError as exc:  # a usage mistake: exit 2, like the argument parser's (#296)
        print(f"error: {exc}", file=sys.stderr)
        return 2
    refused = refused_facilities(ctx)
    if allow_all_active and refused:  # --allow warns per id in resolve_allowlist
        log.warning(
            "--allow-all-active: %d active facilit%s not served, and every call naming %s is "
            "refused: %s",
            len(refused),
            "y is" if len(refused) == 1 else "ies are",
            "it" if len(refused) == 1 else "them",
            "; ".join(why[1] for why in refused.values()),
        )
    _log_templates(ctx, refused)
    previous = _on_signals()
    mpl_dir = None
    try:
        mpl_dir = isolate_matplotlib()  # before any worker starts: they inherit it
        return _serve(capabilities, ctx, max_concurrent, max_queued, len(refused))
    except Terminated as exc:  # before the session started
        return _terminated(exc)
    finally:
        try:
            if mpl_dir is not None:
                import shutil

                shutil.rmtree(mpl_dir, ignore_errors=True)
        finally:
            _restore_signals(previous)
            settle_stderr()


def settle_stderr() -> None:
    """Flush stderr; when that fails (a full disk, a closed pipe), send what is left of the log
    to the null device, so that the interpreter's own flush at exit does not turn a clean exit
    into status 120 (#314)."""
    try:
        sys.stderr.flush()
    except (OSError, ValueError):
        try:
            sys.stderr = open(os.devnull, "w")  # noqa: SIM115 - kept open until exit
        except OSError:
            pass


def _serve(capabilities, ctx, max_concurrent: int, max_queued: int, n_refused: int = 0) -> int:
    server = McpServer(capabilities.default_registry(), ctx)
    # the workers build the same registry: the module this process imported it from
    workers = WorkerPool(
        f"{capabilities.__name__}:default_registry",
        max_concurrent=max_concurrent,
        max_queued=max_queued,
    )
    log.info(
        "store %s; %d facilit%s allowed%s; %d tools, %d prompts; protocol %s; "
        "%d concurrent call(s), %d queued; reading stdin",
        ctx.store,
        len(ctx.allowed_facilities),
        "y" if len(ctx.allowed_facilities) == 1 else "ies",
        f", {n_refused} of them not served (every call refused)" if n_refused else "",
        len(server._tools),
        len(server._prompts),
        ", ".join(SUPPORTED_VERSIONS),
        workers.max_concurrent,
        workers.max_queued,
    )
    return serve_stdio(server, workers=workers)
