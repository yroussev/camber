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
#: a placeholder followed by another or by the rest of the path it stood for
_ADJACENT = re.compile(rf"""<path>(?:<path>|[^{_END}])*""")
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
    paths left (a quoted one whole), and adjacent placeholders collapse into one."""
    out = str(text)
    for f in sorted({f for e in extra if e for f in _forms(e)}, key=len, reverse=True):
        out = out.replace(f, "<path>")
    out = _ABS_PATH.sub("<path>", _ROOTED.sub("<path>", out))
    return _ADJACENT.sub("<path>", out)


def redact_value(text, *extra) -> str:
    """:func:`redact` for a string that is a value of its own (an error's ``data``): one that
    starts with a path is taken whole, spaces and all."""
    out = redact(text, *extra)
    return "<path>" if _WHOLE.match(out) else out


def known_roots(ctx=None) -> list:
    """The local directories no client-visible message may name: the call's store, its config
    and the config's directory, the stores and folders that config reads, the dataset data
    directory, and the home, temp and working directories and the Python prefixes."""
    import sys
    import tempfile

    store = getattr(ctx, "store", None)
    config = getattr(ctx, "config", None)
    roots: list = [store, config]
    if config:
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
    from ..datasets._catalog import is_research_only_licence

    block = (meta or {}).get("dataset")
    if not isinstance(block, dict):
        return False
    if block.get("access") == "research_only" or block.get("redistribution") == "prohibited":
        return True
    if block.get("licence") and is_research_only_licence(str(block["licence"])):
        return True
    did = block.get("dataset_id")
    if did:
        try:
            from ..datasets import get

            return bool(get(str(did)).research_only)
        except Exception:  # noqa: BLE001 - an unknown / retired id: the recorded block decides
            return False
    return False


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


def check_facility(ctx, facility_id: str, meta: dict | None = None) -> None:
    """``not_allowed`` outside the allowlist; ``refused_licence`` for research-only data."""
    from . import CapabilityError

    if facility_id not in ctx.allowed_facilities:
        raise CapabilityError(
            "not_allowed",
            f"facility {facility_id!r} is not among the facilities this caller may read",
        )
    if meta is None:
        meta = open_store(ctx).facilities_meta().get(facility_id) or {}
    if research_only(meta):
        raise CapabilityError(
            "refused_licence",
            f"facility {facility_id!r} holds research-only (non-commercial / no-derivatives) "
            "data, which is never served here",
            data={"facility_id": facility_id},
        )


def check_arguments(ctx, arguments: dict) -> None:
    """Apply :func:`check_facility` to every facility ``arguments`` names."""
    named = named_facilities(arguments)
    if not named:
        return
    for fid in named:  # the allowlist first: an unallowed id never reaches the store
        if fid not in ctx.allowed_facilities:
            check_facility(ctx, fid, {})
    meta = open_store(ctx).facilities_meta()
    for fid in named:
        check_facility(ctx, fid, meta.get(fid) or {})


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
    against the allowlist and the licence. A folder-backed config has no store facility: its
    ``facility_id`` (or ``site``) must still be in the allowlist, and it carries no dataset
    licence to check.
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
        from ..store import ParquetStore

        check_facility(ctx, fid, ParquetStore(store_root).facilities_meta().get(fid) or {})
    else:
        fid = str(config.get("facility_id") or config.get("site") or "")
        check_facility(ctx, fid, {})
    gate = dict(config.get("trust_gate") or {})
    floor = float(ctx.min_trust)
    if gate.get("min_trust") is None or float(gate["min_trust"]) < floor:
        gate["min_trust"] = floor
    config["trust_gate"] = gate
    return run_config(config, base_dir=base_dir or ".", persist=False)
