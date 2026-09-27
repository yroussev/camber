"""The research-only licence gate: refuse NC / ND data until the licence is acknowledged.

A catalog entry is ``access == "research_only"`` exactly when its licence is non-commercial (NC) or
no-derivatives (ND) (:func:`camber.datasets._catalog.validate_catalog` enforces it). Such data may
be downloaded and analysed only after an explicit acknowledgement -- ``accept_noncommercial=True``
in the API, ``--accept-noncommercial`` on the CLI. There is deliberately **no environment-variable
bypass**: an acknowledgement is always an explicit act of the person running the command.

Each acceptance is appended to the ``acknowledgements.json`` ledger (append-only; ``remove`` never
trims it) and recorded in the manifest entry (``acknowledged_at`` + ``acknowledged_licence``). An
ingest of research-only data needs an acknowledgement of the *current* licence in the manifest (or
its own ``accept_noncommercial``), and the ingested facility's provenance records
``redistribution: "prohibited"`` so every report built from it carries the banner.
"""

from __future__ import annotations

from .. import __version__
from . import _paths

STATEMENT = "accepted: research / non-commercial use only; no redistribution"


def refusal(entry, action: str = "download") -> PermissionError:
    """The ``PermissionError`` for a research-only entry used without an acknowledgement."""
    return PermissionError(
        f"{entry.id} is licensed {entry.licence}: research / non-commercial use only, and it "
        f"may not be redistributed. Pass accept_noncommercial=True "
        f"(CLI: --accept-noncommercial) to acknowledge the licence and {action} it."
    )


def acknowledged(root: str, entry) -> str | None:
    """When the entry's *current* licence was last acknowledged in this cache (``None``: never)."""
    rec = _paths.read_manifest(root).get(entry.id) or {}
    if (
        rec.get("acknowledged_at")
        and rec.get("acknowledged_licence", entry.licence) == entry.licence
    ):
        return str(rec["acknowledged_at"])
    return None


def acknowledge(root: str, entry, *, subset: str, via: str) -> dict:
    """Record an acceptance in the ledger and the manifest; returns the ledger record.

    ``via`` says which action accepted it (``"fetch"``, ``"ingest --from-dir"``).
    """
    ack = _paths.append_acknowledgement(
        root,
        {
            "dataset_id": entry.id,
            "licence": entry.licence,
            "subset": subset,
            "via": via,
            "camber_version": __version__,
            "statement": STATEMENT,
        },
    )
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
