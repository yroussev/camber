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
import unicodedata

#: characters that end an unquoted path
_END = r"""\s'"`,;)\]}<>"""
#: where a path starts: a slash followed by a path character (so " / " between words is not a
#: path), ``~/``, a Windows drive or a UNC share
_START = rf"""(?:~[/\\]|/(?=[^{_END}/])|[A-Za-z]:[/\\]|(?:\\\\)+(?=\w))"""
#: what follows a closing quote, so an apostrophe inside a quoted path does not end it
_CLOSE = r"""(?=[\s,.;:)\]}]|$)"""
#: an absolute path inside quotes (spaces and commas included), else up to the first space,
#: comma or quote; never inside a word, a unit such as ``kBtu/ft2`` or after a placeholder
_ABS_PATH = re.compile(
    rf"""(?<=')(?:{_START}).*?(?='{_CLOSE})"""
    rf"""|(?<=")(?:{_START}).*?(?="{_CLOSE})"""
    rf"""|(?<![\w.~/\\>])(?:{_START})[^{_END}]*"""
)
#: a placeholder followed by another or by the rest of the path it stood for -- across a space
#: when the next word goes on with a separator (``<path>/John Smith/My Drive`` is one path,
#: 0.111 #291); a last word with no separator after it cannot be told from the sentence
_ADJACENT = re.compile(rf"""<path>(?:<path>|[^{_END}]|\s+(?=[^\s{_END[2:]}/\\]+[/\\]))*""")
#: a value that is one path (or starts with one), the rest of the line included
_WHOLE = re.compile(rf"""\s*(?:<path>|{_START})[^\n]*\Z""")
#: the rest of a quoted path after the placeholder of its root
_ROOTED = re.compile(rf"""(?<=')<path>.*?(?='{_CLOSE})|(?<=")<path>.*?(?="{_CLOSE})""")


def _forms(path) -> set:
    """The spellings of ``path`` a message may carry: as given (when it has a separator),
    absolute, resolved, with ``~`` expanded, in both Unicode normal forms, and with backslashes
    doubled the way ``repr`` and JSON write them. A root of fewer than three characters ("/",
    "C:") is left to the pattern."""
    try:
        p = os.fspath(path)
    except TypeError:
        return set()
    if not isinstance(p, str) or not p.strip():
        return set()
    out = {p} if ("/" in p or "\\" in p) else set()
    for f in (os.path.abspath, os.path.realpath):
        try:
            out.add(f(os.path.expanduser(p)))
        except (OSError, ValueError):
            pass
    out |= {unicodedata.normalize(n, s) for s in list(out) for n in ("NFC", "NFD")}
    out |= {s.replace("\\", "\\\\") for s in out if "\\" in s}
    out = {s.rstrip("/\\") for s in out}
    return {s for s in out if len(s) > 2}


def redact(text, *extra) -> str:
    """``text`` with every local path replaced by ``<path>``.

    Each of ``extra`` (known roots: see :func:`known_roots`) goes first, by exact prefix in
    every spelling of :func:`_forms`, with the rest of the path after it: to the closing quote
    when it is quoted, so a space or a comma does not end it. A pattern then takes the absolute
    paths left (a quoted one whole), and adjacent placeholders collapse into one. A quoted span
    of :func:`camber.capabilities._sanitize.name` (a value from the data) gets the known roots
    only, never the pattern, and keeps its marks."""
    from ._sanitize import SPAN

    out = str(text)
    for f in sorted({f for e in extra if e for f in _forms(e)}, key=len, reverse=True):
        out = out.replace(f, "<path>")
    # a quoted span is a value from the data (0.111, #307): a slash in it is a name, not a path
    # of this machine, so only the known roots above come out of it
    parts = SPAN.split(out)
    for i in range(0, len(parts), 2):
        parts[i] = _ABS_PATH.sub("<path>", _ROOTED.sub("<path>", parts[i]))
    return "".join(_ADJACENT.sub("<path>", p) for p in parts)


def redact_value(text, *extra) -> str:
    """:func:`redact` for a string that is a value of its own (an error's ``data``): one that
    starts with a path is taken whole, spaces and all."""
    out = redact(text, *extra)
    return "<path>" if _WHOLE.match(out) else out


def config_paths(ctx) -> list:
    """The run config paths of ``ctx``: ``ctx.config`` is one path or (0.111, #296) a sequence
    of them, one per facility."""
    config = getattr(ctx, "config", None)
    if not config:
        return []
    if isinstance(config, (str, os.PathLike)):
        return [os.fspath(config)]
    return [os.fspath(c) for c in config if c]


def known_roots(ctx=None) -> list:
    """The local directories no client-visible message may name: the call's store, its config
    and the config's directory, the stores and folders that config reads, the dataset data
    directory, and the home, temp and working directories and the Python prefixes."""
    import sys
    import tempfile

    store = getattr(ctx, "store", None)
    roots: list = [store]
    for config in config_paths(ctx):
        roots.append(config)
        base = os.path.dirname(os.path.abspath(os.path.expanduser(str(config))))
        roots.append(base)
        try:
            from ..config import load_config

            source = load_config(str(config)).get("source") or {}
            paths = [source.get("store"), source.get("folder"), *(source.get("folders") or [])]
        except Exception:  # noqa: BLE001 - an unreadable config names no further path
            paths = []
        for p in paths:
            if isinstance(p, str) and p:
                roots += [p, os.path.join(base, os.path.expanduser(p))]
    try:
        from ..datasets._paths import data_dir

        roots.append(data_dir())
    except Exception:  # noqa: BLE001
        pass
    try:
        roots.append(os.getcwd())
    except OSError:
        pass
    roots += [os.path.expanduser("~"), tempfile.gettempdir(), sys.prefix, sys.base_prefix]
    return [r for r in roots if r]


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
    from ..config import load_config, run_config
    from . import CapabilityError

    if config is None:
        paths = config_paths(ctx)
        if len(paths) != 1:
            raise CapabilityError(
                "invalid_params",
                "this caller has no run config" if not paths else "this caller has several",
            )
        if not os.path.isfile(paths[0]):
            raise CapabilityError("not_found", "the run config this caller names does not exist")
        config = load_config(paths[0])
        base_dir = base_dir or os.path.dirname(os.path.abspath(paths[0]))
    return run_config(_checked(ctx, config, base_dir), base_dir=base_dir or ".", persist=False)


def _checked(ctx, config: dict, base_dir: str | None) -> dict:
    """``config`` checked the way :func:`run_config_for` runs it: its facility allowed and not
    research-only, and its trust floor raised to ``ctx.min_trust``."""
    from ..config import _check_config_shape

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
    return config


def run_frames_for(ctx, config: dict, *, base_dir: str):
    """The equipment frames a :func:`run_config_for` run of ``config`` reads -- every role at the
    run's resample, the shared points merged -- with nothing run (0.111, #311), as a run-like
    namespace: ``frame_for(equip)`` (``None`` for equipment the run does not discover),
    ``refs``, ``resample``, ``config`` and ``base_dir``. The same checks apply."""
    from types import SimpleNamespace

    from ..config import _frame_resolver, _prepare

    base_dir = base_dir or "."
    config = _checked(ctx, config, base_dir)
    prep = _prepare(config, base_dir)
    return SimpleNamespace(
        frame_for=_frame_resolver(prep),
        refs=list(prep.refs),
        resample=prep.resample,
        config=config,
        base_dir=base_dir,
        findings=[],
        registry=None,
    )
