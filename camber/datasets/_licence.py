"""The research-only licence gate: refuse NC / ND data until the licence is acknowledged.

A catalog entry is ``access == "research_only"`` when its licence is non-commercial (NC) or
no-derivatives (ND), or when it states an ``access_reason`` for holding an open-licence dataset
there (:func:`camber.datasets._catalog.validate_catalog` enforces both). Such data may
be downloaded and analysed only after an explicit acknowledgement -- ``accept_noncommercial=True``
in the API, ``--accept-noncommercial`` on the CLI. There is deliberately **no environment-variable
bypass**: an acknowledgement is always an explicit act of the person running the command.

Each acceptance is appended to the ``acknowledgements.json`` ledger (append-only; ``remove`` never
trims it) and recorded in the manifest entry (``acknowledged_at`` + ``acknowledged_licence``). An
ingest of research-only data needs an acknowledgement of the *current* licence in the manifest (or
its own ``accept_noncommercial``), and the ingested facility's provenance records
``redistribution: "prohibited"`` so every report built from it carries the banner.

**Where an acknowledgement goes (0.103, #124).** An acknowledgement is the act of the person
running the command. Against a cache they can write, it goes to that cache's ledger and manifest,
under the cache lock, as before. Against a **read-only** cache (a classroom's shared cache) it goes
to the user's own ledger, ``acknowledgements.json`` in :func:`._paths.user_state_dir`, with the
cache's path in the record; and only that ledger counts there. The read-only cache's own
manifest records the acknowledgement of whoever filled it, which does not stand in for this
user's.
"""

from __future__ import annotations

import os

from .. import __version__
from . import _paths

STATEMENT = "accepted: research / non-commercial use only; no redistribution"


def refusal(entry, action: str = "download") -> PermissionError:
    """The ``PermissionError`` for a research-only entry used without an acknowledgement."""
    why = (
        f"is held research-only by CAMBER although its licence is {entry.licence} "
        f"({entry.access_reason})"
        if getattr(entry, "access_reason", "")
        else f"is licensed {entry.licence}"
    )
    return PermissionError(
        f"{entry.id} {why}: research / non-commercial use only, and it may not be "
        f"redistributed. Pass accept_noncommercial=True (CLI: --accept-noncommercial) to "
        f"acknowledge the terms and {action} it."
    )


def _cache_key(root: str) -> str:
    return os.path.realpath(os.path.abspath(root))


def _user_acknowledged(root: str, entry) -> str | None:
    key = _cache_key(root)
    for rec in reversed(_paths.read_acknowledgements(_paths.user_state_dir())):
        if (
            isinstance(rec, dict)
            and rec.get("dataset_id") == entry.id
            and rec.get("licence") == entry.licence
            and rec.get("cache") == key
            and rec.get("accepted_at")
        ):
            return str(rec["accepted_at"])
    return None


def acknowledged(root: str, entry) -> str | None:
    """When the entry's *current* licence was last acknowledged in this cache (``None``: never).

    For a read-only cache: by this user, in their own ledger (see the module docstring).
    """
    if not _paths.cache_writable(root):
        return _user_acknowledged(root, entry)
    rec = _paths.read_manifest(root).get(entry.id) or {}
    if (
        rec.get("acknowledged_at")
        and rec.get("acknowledged_licence", entry.licence) == entry.licence
    ):
        return str(rec["acknowledged_at"])
    return None


def acknowledge(root: str, entry, *, subset: str, via: str) -> dict:
    """Record an acceptance in the ledger and the manifest; returns the ledger record.

    ``via`` says which action accepted it (``"fetch"``, ``"ingest --from-dir"``). A read-only
    cache's acknowledgement goes to the user's own ledger instead (see the module docstring).
    """
    record = {
        "dataset_id": entry.id,
        "licence": entry.licence,
        "subset": subset,
        "via": via,
        "camber_version": __version__,
        "statement": STATEMENT,
    }
    if not _paths.cache_writable(root):
        state = _paths.user_state_dir()
        with _paths.cache_lock(state):  # the user's own processes, one at a time
            return _paths.append_acknowledgement(state, {**record, "cache": _cache_key(root)})
    with _paths.cache_lock(root):
        ack = _paths.append_acknowledgement(root, record)
        manifest = _paths.read_manifest(root)
        rec = manifest.get(entry.id) or {}
        rec.update(
            {
                "acknowledged_at": ack["accepted_at"],
                "acknowledged_licence": entry.licence,
                "access": entry.access,
                "licence": entry.licence,
            }
        )
        manifest[entry.id] = rec
        _paths.write_manifest(root, manifest)
    return ack


def require(root: str, entry, *, accept_noncommercial: bool, subset: str, via: str, action: str):
    """The gate: ``None`` for an open entry; else the acceptance time, recording a new one when
    ``accept_noncommercial`` is given, or ``PermissionError`` when there is none."""
    if not entry.research_only:
        return None
    if accept_noncommercial:
        return acknowledge(root, entry, subset=subset, via=via)["accepted_at"]
    when = acknowledged(root, entry)
    if when is None:
        raise refusal(entry, action)
    return when
