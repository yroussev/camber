"""``camber mcp``: build the call context from the launch configuration and serve stdio."""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

from ._server import SUPPORTED_VERSIONS, McpServer
from ._stdio import serve_stdio

log = logging.getLogger("camber.mcp")


class LaunchError(ValueError):
    """The launch configuration cannot be served (a usage mistake, reported as one line)."""


def resolve_store(path: str) -> str:
    """The ParquetStore root for ``path``: the store itself, or a portfolio workspace's store."""
    from ..api.server import _store_problem
    from ..portfolio import Portfolio, is_workspace

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


def build_context(
    store: str,
    *,
    allow: list | None = None,
    allow_all_active: bool = False,
    config: str | None = None,
    min_trust: float = 0.5,
) -> Any:
    """The ``camber.capabilities.CallContext`` every tool call of this launch runs under."""
    from ..capabilities import CallContext

    if not 0.0 <= float(min_trust) <= 1.0:
        raise LaunchError(f"--min-trust must be from 0 to 1 (got {min_trust})")
    root = resolve_store(store)
    if config is not None and not os.path.isfile(config):
        raise LaunchError(f"--config {config}: no such file")
    return CallContext(
        store=root,
        allowed_facilities=resolve_allowlist(root, allow, allow_all_active),
        config=os.path.abspath(config) if config is not None else None,
        min_trust=float(min_trust),
        caller="mcp",
    )


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
    config: str | None = None,
    min_trust: float = 0.5,
) -> int:
    """Run ``camber mcp`` until the client closes stdin; the exit status."""
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
    except LaunchError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    server = McpServer(capabilities.default_registry(), ctx)
    log.info(
        "store %s; %d facilit%s allowed; %d tools, %d prompts; protocol %s; reading stdin",
        ctx.store,
        len(ctx.allowed_facilities),
        "y" if len(ctx.allowed_facilities) == 1 else "ies",
        len(server._tools),
        len(server._prompts),
        ", ".join(SUPPORTED_VERSIONS),
    )
    return serve_stdio(server)
