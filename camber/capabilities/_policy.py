"""The policy checks behind :meth:`camber.capabilities.Registry.call` (0.110, #275).

- **Facility allowlist.** An argument named ``facility_id`` (a string) or ``facility_ids`` (a
  list) is the only way a capability names a facility; :func:`check_arguments` refuses one outside
  ``ctx.allowed_facilities`` with ``not_allowed`` -- whether or not it exists, so a caller cannot
  probe for facility ids it was not granted.
- **Research-only (NC/ND) data.** :func:`research_only` tells it from a facility's registry entry:
  the dataset-catalog block ``camber datasets ingest`` records (``access == "research_only"``,
  ``redistribution == "prohibited"``, or an NC / ND licence id), or -- for a block written before
  the catalog changed the dataset's terms -- the catalog's current entry for its ``dataset_id``.
  A named research-only facility is ``refused_licence``; there is no override.
- **Fail closed** (0.111, #290). :func:`check_facility` decides with
  :func:`camber._servable.refusal`: a facility named for a research-only catalog dataset
  (``ds-<dataset>...``) is refused whatever its registry entry says, and so is every facility
  while the store's facility registry is missing, unreadable or malformed, and a ``ds-`` facility
  the registry has no entry for. Those are ``refused_licence`` too (with
  ``data["reason"] == "licence_unverified"``), and :func:`registry_notice` puts the registry's
  problem on every result.
- **Trust floor.** :func:`run_config_for` raises a config's ``trust_gate.min_trust`` to
  ``ctx.min_trust``.
- **No local paths.** :func:`redact` takes absolute paths out of any message that leaves the
  registry.

Private module -- not public API.
"""

from __future__ import annotations

import os
import re

#: an absolute POSIX path, a home-relative one, or a Windows drive path; it starts at a word
#: boundary that is not part of a unit such as ``kBtu/ft2``
_ABS_PATH = re.compile(r"""(?<![\w.~/\\])(?:~[/\\]|/|[A-Za-z]:[/\\])[^\s'"`,;)\]]*""")


def redact(text, *extra) -> str:
    """``text`` with every absolute path (and each of ``extra``, as given) replaced by
    ``<path>``."""
    out = str(text)
    for p in sorted({str(e) for e in extra if e}, key=len, reverse=True):
        out = out.replace(p, "<path>")
    return _ABS_PATH.sub("<path>", out)


def open_store(ctx):
    """The :class:`~camber.store.ParquetStore` at ``ctx.store``; ``not_found`` when there is no
    store directory there (the message names no path)."""
    from ..store import ParquetStore
    from . import CapabilityError

    if not ctx.store or not os.path.isdir(ctx.store):
        raise CapabilityError("not_found", "the store this caller reads does not exist")
    return ParquetStore(ctx.store)


def research_only(meta: dict) -> bool:
    """Whether a facility's registry entry marks research-only (NC / ND) data."""
    from .._servable import research_only_block

    return research_only_block(meta)


def registry_notice(ctx) -> str | None:
    """The banner every result carries while the store's facility registry cannot be trusted
    (``None`` when it can, or when there is no store)."""
    from .._servable import read_registry, registry_banner

    if not ctx.store or not os.path.isdir(ctx.store):
        return None
    problem = read_registry(ctx.store)[1]
    return registry_banner(problem) if problem is not None else None


def named_facilities(arguments: dict) -> list:
    """The facility ids ``arguments`` names (``facility_id``, ``facility_ids``), in order."""
    out: list = []
    fid = arguments.get("facility_id")
    if isinstance(fid, str):
        out.append(fid)
    many = arguments.get("facility_ids")
    if isinstance(many, (list, tuple)):
        out += [f for f in many if isinstance(f, str)]
    return out


def check_facility(ctx, facility_id: str, *, store_root=None, registry=None) -> None:
    """``not_allowed`` outside the allowlist; ``refused_licence`` for research-only data or a
    facility whose licence cannot be established (see :func:`camber._servable.refusal`).

    ``store_root`` is the store the facility is read from (default ``ctx.store``; ``False``: none,
    a folder-backed config); ``registry`` is that store's :func:`~camber._servable.read_registry`.
    """
    from .._servable import refusal
    from . import CapabilityError

    if facility_id not in ctx.allowed_facilities:
        raise CapabilityError(
            "not_allowed",
            f"facility {facility_id!r} is not among the facilities this caller may read",
        )
    if store_root is None:
        open_store(ctx)  # not_found when there is no store
        store_root = ctx.store
    why = refusal(facility_id, store_root or None, registry)
    if why is None:
        return
    kind, message = why
    if kind == "research_only":
        raise CapabilityError(
            "refused_licence",
            f"{message}, which is never served here",
            data={"facility_id": facility_id},
        )
    raise CapabilityError(
        "refused_licence",
        f"{message}; it is not served until that is repaired",
        data={"facility_id": facility_id, "reason": "licence_unverified"},
    )


def check_arguments(ctx, arguments: dict) -> None:
    """Apply :func:`check_facility` to every facility ``arguments`` names."""
    from .._servable import read_registry

    named = named_facilities(arguments)
    if not named:
        return
    for fid in named:  # the allowlist first: an unallowed id never reaches the store
        if fid not in ctx.allowed_facilities:
            check_facility(ctx, fid, store_root=False)
    open_store(ctx)
    registry = read_registry(ctx.store)
    for fid in named:
        check_facility(ctx, fid, store_root=ctx.store, registry=registry)


def outside_allowlist(data, allowed) -> str | None:
    """The first ``facility_id`` value anywhere in ``data`` that is not in ``allowed``."""
    stack = [data]
    while stack:
        x = stack.pop()
        if isinstance(x, dict):
            fid = x.get("facility_id")
            if isinstance(fid, str) and fid not in allowed:
                return fid
            stack.extend(x.values())
        elif isinstance(x, (list, tuple)):
            stack.extend(x)
    return None


def run_config_for(ctx, config: dict | None = None, *, base_dir: str | None = None):
    """Run ``config`` (default: ``ctx.config``, loaded) the registry's way: no-persist, the trust
    floor applied, and refused unless its facility is allowed and not research-only.

    The helper a run or report capability calls; returns the
    :class:`~camber.config.RunResult`. A store-backed config's ``source.facility_id`` is checked
    against the allowlist and the licence, in the store it names. A folder-backed config has no
    store facility: its ``facility_id`` (or ``site``) must still be in the allowlist, and it is
    refused when it is named for a research-only catalog dataset.
    """
    from ..config import _check_config_shape, load_config, run_config
    from . import CapabilityError

    if config is None:
        if not ctx.config:
            raise CapabilityError("invalid_params", "this caller has no run config")
        if not os.path.isfile(ctx.config):
            raise CapabilityError("not_found", "the run config this caller names does not exist")
        config = load_config(ctx.config)
        base_dir = base_dir or os.path.dirname(os.path.abspath(ctx.config))
    config = dict(_check_config_shape(config))
    source = config.get("source") or {}
    if source.get("kind") == "store":
        fid = str(source.get("facility_id") or "")
        store_root = source.get("store") or ""
        if not os.path.isabs(store_root):
            store_root = os.path.join(base_dir or ".", store_root)
        check_facility(ctx, fid, store_root=store_root)
    else:
        fid = str(config.get("facility_id") or config.get("site") or "")
        check_facility(ctx, fid, store_root=False)
    gate = dict(config.get("trust_gate") or {})
    floor = float(ctx.min_trust)
    if gate.get("min_trust") is None or float(gate["min_trust"]) < floor:
        gate["min_trust"] = floor
    config["trust_gate"] = gate
    return run_config(config, base_dir=base_dir or ".", persist=False)
