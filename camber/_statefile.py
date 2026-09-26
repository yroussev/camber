"""JSON state files (fault stores, baseline stores) and the redirect stubs a migration leaves.

``camber portfolio migrate`` moves a legacy, site-keyed state file into the portfolio workspace's
``state/<facility_id>/`` directories and replaces the original with a small **redirect stub**::

    {"camber_redirect": {"kind": "faults", "file": "faults.json",
                         "state_root": "../ws/state", "state_root_abs": "/abs/ws/state",
                         "facilities": ["north-campus-1a2b3c"], "sha256": "...", ...}}

so a config (or a script) that still names the old path keeps working:

- a store opened **for one facility** (``facility_id=...``) reads and writes
  ``<state_root>/<facility_id>/<file>`` -- a path derived from the facility id;
- a store opened with no facility gets a **read-only** merged view of every facility the stub
  lists; saving it is refused (it would silently fork the migrated history).

Private plumbing shared by :mod:`camber.faultlifecycle` and :mod:`camber.store.modelstore`.
"""

from __future__ import annotations

import json
import os

REDIRECT_KEY = "camber_redirect"


def read_json(path: str) -> dict:
    """The JSON object at ``path`` (``{}`` when the file does not exist)."""
    if not path or not os.path.isfile(path):
        return {}
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def write_json(path: str, data: dict) -> None:
    """Atomic write (temp file + ``os.replace``); creates the parent directory.

    The layout (``indent=2``, ASCII-escaped, no trailing newline) is the one the fault and baseline
    stores have always written, so files outside a workspace are byte-for-byte unchanged.
    """
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
    os.replace(tmp, path)


def redirect_of(data: dict):
    """The redirect record of a stub, or ``None`` for an ordinary state file."""
    red = data.get(REDIRECT_KEY) if isinstance(data, dict) else None
    return red if isinstance(red, dict) else None


def _state_root(stub_path: str, red: dict) -> str:
    base = os.path.dirname(os.path.abspath(stub_path))
    rel = red.get("state_root")
    if isinstance(rel, str) and rel:
        cand = os.path.normpath(os.path.join(base, rel))
        if os.path.isdir(cand):
            return cand
    absolute = red.get("state_root_abs")
    if isinstance(absolute, str) and absolute:
        return absolute
    raise ValueError(f"{stub_path} is a migration redirect with no usable state_root")


def redirect_target(stub_path: str, red: dict, facility_id: str) -> str:
    """Where a redirect stub sends ``facility_id``: ``<state_root>/<facility_id>/<file>``."""
    return os.path.join(_state_root(stub_path, red), facility_id, str(red.get("file") or ""))


def load_state(path, *, facility_id=None, list_key: str):
    """Read a state file, following a migration redirect.

    Returns ``(data, write_path, redirect)``: ``write_path`` is where :func:`save_path` would put
    it (``None`` for the read-only merged view of an unbound redirect).
    """
    data = read_json(path)
    red = redirect_of(data)
    if red is None:
        return data, path, None
    if facility_id:
        target = redirect_target(path, red, facility_id)
        return read_json(target), target, red
    merged: list = []
    for fid in red.get("facilities") or []:
        merged.extend(read_json(redirect_target(path, red, fid)).get(list_key) or [])
    return {list_key: merged}, None, red


def save_path(path: str, *, facility_id=None) -> str:
    """The file a save to ``path`` must write: ``path`` itself, or its redirect target.

    Raises ``ValueError`` when ``path`` is a redirect stub and no facility is given -- writing the
    merged view back would fork the migrated history (and destroy the stub).
    """
    if path and os.path.isfile(path):
        try:
            red = redirect_of(read_json(path))
        except (ValueError, OSError):
            red = None
        if red is not None:
            if not facility_id:
                raise ValueError(
                    f"{path} was migrated to the portfolio's state/<facility_id>/ directories "
                    "(`camber portfolio migrate`); open it with facility_id=... to write to it"
                )
            return redirect_target(path, red, facility_id)
    return path


def dedup(items, *, drop="") -> list:
    """``items`` without blanks, ``drop`` and repeats, in first-seen order."""
    out: list = []
    for x in items:
        if x and x != drop and x not in out:
            out.append(x)
    return out
