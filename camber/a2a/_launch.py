"""``camber a2a``: check the launch configuration, bind, announce, serve until stopped.

The launch model is ``camber mcp``'s, reused: the call context (``--store``, ``--allow`` /
``--allow-all-active``, ``--config``, ``--min-trust``) is :func:`camber.mcp.build_context`, the
skills run in :class:`camber.mcp.WorkerPool` worker processes (``--max-concurrent``,
``--max-queued``), matplotlib writes to a temporary directory of the server's, and ``SIGTERM`` /
``SIGHUP`` stop it like Ctrl-C. Every launch mistake is one line on stderr and exit status 2.
"""

from __future__ import annotations

import dataclasses
import logging
import os
import sys
from typing import Any

from .. import _access
from ..mcp import _launch as mcp_launch
from ..mcp._launch import LaunchError
from ..mcp._stdio import Terminated, _terminated

log = logging.getLogger("camber.a2a")

#: the default port of ``camber a2a``
DEFAULT_PORT = 8770
#: the environment variable that fixes the bearer token (at least 16 characters)
TOKEN_ENV = "CAMBER_A2A_TOKEN"
#: the launch file is ``a2a-<port>.url``, beside ``serve-<port>.url`` and ``lab-<port>.url``
LAUNCH_PREFIX = "a2a"


def _setup_logging() -> None:
    """INFO lines on stderr (the agent's and its worker pool's), unless logging is configured."""
    if logging.getLogger().handlers:
        return
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("camber a2a: %(levelname)s %(message)s"))
    for name in ("camber.a2a", "camber.mcp"):
        logger = logging.getLogger(name)
        if not logger.handlers:
            logger.addHandler(handler)
            logger.setLevel(logging.INFO)


def check_bind(host: str, port: int, allowed_hosts) -> list:
    """The extra ``Host`` names; :class:`LaunchError` for a bind ``camber a2a`` refuses."""
    if not isinstance(port, int) or isinstance(port, bool) or not 0 <= port <= 65535:
        raise LaunchError(f"--port must be from 0 to 65535 (got {port!r})")
    if not str(host or "").strip():
        raise LaunchError("--host: give an address (an empty one names none)")
    extra = [str(h).strip() for h in allowed_hosts or () if str(h).strip()]
    if _access.is_wildcard(host) and not extra:
        raise LaunchError(
            f"refusing to bind every interface ({host}) without a Host allowlist: pass "
            "--allow-host NAME (repeatable) with the names peers use to reach this server"
        )
    return extra


def resolve_token(env=None) -> tuple[str, bool]:
    """``(token, from_env)``: ``$CAMBER_A2A_TOKEN`` when set, else a fresh 256-bit token."""
    env = os.environ if env is None else env
    given = env.get(TOKEN_ENV)
    if given is not None and given != "":
        if len(given) < _access.MIN_TOKEN_LEN or not given.isprintable() or " " in given:
            raise LaunchError(
                f"{TOKEN_ENV} must be at least {_access.MIN_TOKEN_LEN} printable characters "
                "with no space"
            )
        return given, True
    return _access.new_secret(), False


def launch(
    store: str,
    *,
    allow: list | None = None,
    allow_all_active: bool = False,
    config=None,
    min_trust: float = 0.5,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    allowed_hosts: list | None = None,
    max_concurrent: int = 2,
    max_queued: int = 8,
    launch_dir=None,
    out=None,
    _on_ready=None,
) -> int:
    """Run ``camber a2a`` until Ctrl-C, ``SIGTERM`` or ``SIGHUP``; the exit status (2 for a launch
    mistake, 130 for Ctrl-C, 128 + the signal number for the others). ``launch_dir`` overrides
    where the launch file goes; ``_on_ready(httpd)`` (tests) runs once the server is bound."""
    _setup_logging()
    mcp_launch._SIGNALLED.clear()
    previous = mcp_launch._on_signals()
    try:
        return _launch(
            store,
            allow=allow,
            allow_all_active=allow_all_active,
            config=config,
            min_trust=min_trust,
            host=host,
            port=port,
            allowed_hosts=allowed_hosts,
            max_concurrent=max_concurrent,
            max_queued=max_queued,
            launch_dir=launch_dir,
            out=out or sys.stdout,
            on_ready=_on_ready,
        )
    except KeyboardInterrupt:
        log.info("interrupted; exiting")
        return 130
    except Terminated as exc:
        return _terminated(exc)
    finally:
        if not mcp_launch._SIGNALLED:
            mcp_launch._restore_signals(previous)
        mcp_launch.settle_stderr()


def _a2a_words(message: str) -> str:
    return str(message).replace("camber mcp", "camber a2a")


def build(
    store: str,
    *,
    allow: list | None,
    allow_all_active: bool,
    config,
    min_trust: float,
    host: str,
    port: int,
    allowed_hosts: list | None,
    max_concurrent: int,
    max_queued: int,
) -> tuple:
    """The checked launch: ``(context, extra Host names, token, token from env, refused)``;
    :class:`LaunchError` for any mistake."""
    from ..mcp._workers import MAX_CONCURRENT, MAX_QUEUED

    extra = check_bind(host, port, allowed_hosts)
    if not 1 <= int(max_concurrent) <= MAX_CONCURRENT:
        raise LaunchError(f"--max-concurrent must be from 1 to {MAX_CONCURRENT}")
    if not 0 <= int(max_queued) <= MAX_QUEUED:
        raise LaunchError(f"--max-queued must be from 0 to {MAX_QUEUED}")
    token, from_env = resolve_token()
    try:
        ctx = mcp_launch.build_context(
            store,
            allow=allow,
            allow_all_active=allow_all_active,
            config=config,
            min_trust=min_trust,
        )
    except LaunchError as exc:
        raise LaunchError(_a2a_words(str(exc))) from None
    ctx = dataclasses.replace(ctx, caller="a2a")
    refused = mcp_launch.refused_facilities(ctx)
    return ctx, extra, token, from_env, refused


def _launch(store, *, out, launch_dir, on_ready, **kw) -> int:
    try:
        import camber.capabilities as capabilities
    except ImportError as exc:
        print(f"error: camber a2a needs the capability registry: {exc}", file=sys.stderr)
        return 1
    try:
        ctx, extra, token, from_env, refused = build(store, **kw)
    except LaunchError as exc:  # a usage mistake: exit 2, like the argument parser's
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if refused:
        log.warning(
            "%d allowed facilit%s not served (every task naming %s is rejected): %s",
            len(refused),
            "y is" if len(refused) == 1 else "ies are",
            "it" if len(refused) == 1 else "them",
            "; ".join(why[1] for why in refused.values()),
        )
    mpl_dir = None
    try:
        mpl_dir = mcp_launch.isolate_matplotlib()  # before any worker starts: they inherit it
        return _serve(
            capabilities, ctx, extra, token, from_env, refused, out, launch_dir, on_ready, **kw
        )
    finally:
        mcp_launch.release_matplotlib(mpl_dir)


def _serve(
    capabilities, ctx, extra, token, from_env, refused, out, launch_dir, on_ready, **kw
) -> int:
    from ..mcp._workers import WorkerPool
    from ._server import A2AServer, make_server

    host, port = kw["host"], kw["port"]
    registry = capabilities.default_registry()
    pool = WorkerPool(
        f"{capabilities.__name__}:default_registry",
        max_concurrent=kw["max_concurrent"],
        max_queued=kw["max_queued"],
    )
    served = sorted(ctx.allowed_facilities - set(refused))

    def agent(bound_port: int) -> Any:
        return A2AServer(
            registry,
            ctx,
            pool,
            token=token,
            bind_host=host,
            port=bound_port,
            allowed_hosts=extra,
            served=served,
        )

    try:
        httpd = make_server(host, port, agent)
    except OSError as exc:
        print(f"error: cannot bind {host}:{port}: {exc}", file=sys.stderr)
        return 2
    path = None
    a2a = httpd.agent  # type: ignore[attr-defined]
    try:
        pool.start()
        path = _announce(a2a, token, from_env, out, launch_dir)
        log.info(
            "store %s; %d facilit%s served; skills %s; %d concurrent task(s), %d queued",
            ctx.store,
            len(served),
            "y" if len(served) == 1 else "ies",
            ", ".join(s.id for s in _skills()),
            pool.max_concurrent,
            pool.max_queued,
        )
        mcp_launch._raise_if_signalled()
        if on_ready is not None:
            on_ready(httpd)
        httpd.serve_forever()
        return 0
    finally:
        stopped = pool.close()
        a2a.tasks.cancel_all()
        httpd.server_close()
        if stopped:
            log.info("%d task(s) stopped", stopped)
        if path is not None:
            _access.remove_launch_file(path, _launch_url(a2a, token))


def _skills():
    from ._protocol import SKILLS

    return SKILLS


def _launch_url(a2a, token: str) -> str:
    from urllib.parse import quote

    from ._protocol import RPC_PATH

    base = a2a.card()["supportedInterfaces"][0]["url"]
    assert base.endswith(RPC_PATH)
    return f"{base}?{_access.TOKEN_PARAM}={quote(token, safe='')}"


def _announce(a2a, token: str, from_env: bool, out, launch_dir) -> str | None:
    """Print where the card and the endpoint are; save the endpoint and the token (a generated
    one) in the launch file only this user can read. The launch file's path, or ``None``."""
    from ._protocol import CARD_PATH

    card = a2a.card()
    rpc_url = card["supportedInterfaces"][0]["url"]
    base = rpc_url[: -len("/a2a")]
    print(f"camber a2a: agent card {base}{CARD_PATH}", file=out)
    print(f"camber a2a: JSON-RPC endpoint {rpc_url} (A2A 1.0; Authorization: Bearer)", file=out)
    path = None
    if from_env:
        print(f"camber a2a: the bearer token is the one in {TOKEN_ENV}", file=out)
    else:
        try:
            path = _access.write_launch_file(
                _launch_url(a2a, token), a2a.hosts.port, launch_dir, prefix=LAUNCH_PREFIX
            )
        except OSError as exc:
            print(f"warning: launch file not written: {exc}", file=sys.stderr)
            print(f"camber a2a: bearer token (keep it private): {token}", file=out)
        else:
            print(
                f"camber a2a: the endpoint and this run's bearer token (its token= value) are "
                f"saved, readable by you only, in {path}",
                file=out,
            )
    print(f"camber a2a: allowed Host values: {a2a.hosts.describe()}", file=out)
    if not a2a.loopback:
        print(
            "warning: bound to a non-loopback address: the agent card needs the token too, and "
            "the traffic is plain HTTP; put a TLS reverse proxy in front (docs/A2A.md)",
            file=sys.stderr,
        )
    if a2a.hosts.any:
        print(
            "warning: --allow-host '*' turns the Host check off (DNS rebinding is possible).",
            file=sys.stderr,
        )
    print("camber a2a: Ctrl-C to stop", file=out, flush=True)
    return path
