"""The facility lifecycle rules every dataset-catalog writer follows (0.107, #212).

``camber datasets ingest`` / ``remove --purge-store`` (and the API behind them), the open-fdd
ingest and ``camber lab`` all write dataset facilities into a store. Inside a portfolio workspace
those facilities follow the 0.95 lifecycle, and these checks are the one place its rules live:

- **ingest** only into a facility that is ``provisioning`` or ``active``
  (:data:`INGESTABLE_STATES`). A suspended, offboarding or archived facility belongs to its
  lifecycle (``camber facility resume|restore`` first); an archived one would get hot data that
  is in no export bundle. :func:`ingest_blockers` says why an ingest is refused.
- **purge** (``remove --purge-store``, or an ingest dropping a facility its subset no longer
  produces) never deletes a facility under a legal hold, nor one that has left ``provisioning`` /
  ``active``: its data is removed by ``camber facility offboard|archive|purge``, never by a
  catalog shortcut. :func:`purge_blockers` says why a purge is refused.
- **replace** never deletes a held facility's data: a forced (or changed) re-ingest into a
  facility under a legal hold that already has a partition is refused (:func:`replace_blockers`).
- every such write is **audited** in the workspace with a ``reason`` (:func:`require_reason`,
  :func:`audit_ingest`, :func:`audit_remove`).

0.108 (#250-#252) puts the same rules under the primitives every caller ends in:

- **delete** -- the store and registry primitives (``ParquetStore.drop_facility`` / ``prune`` /
  ``drop_partition``, ``FacilityRegistry.remove``) refuse a held facility inside a workspace,
  need a reason and audit (:func:`store_delete_gate`, :func:`delete_blockers`);
  ``drop_facility`` and ``remove`` also refuse a facility that has left ``provisioning`` /
  ``active``, as a purge does (:func:`purge_blockers`): an archived one goes through the
  lifecycle purge;
- **state writes** -- a config run never writes ``state/<fid>/`` (faults, baselines, the manifest)
  of an archived or purged facility (:func:`state_write_blockers`). A legal hold blocks only
  deleting or replacing data, so the non-destructive writes (a faults fold, a baseline freeze or
  accept, which add versions) stay allowed for a held facility that is not archived.

Outside a workspace there are no states to follow and no holds: nothing here refuses anything,
and the store's own guard (:meth:`camber.store.FacilityRegistry._guard_write`: tombstoned ids,
case-variants, archived facilities) is what every write still passes.
"""

from __future__ import annotations

import os

INGESTABLE_STATES = ("provisioning", "active")


def workspace_of(store_root):
    """The :class:`~camber.portfolio.Portfolio` the store at ``store_root`` belongs to, or
    ``None`` for a standalone store."""
    from ..store.facilities import _workspace_of_store

    ws = _workspace_of_store(os.path.abspath(os.fspath(store_root)))
    if ws is None:
        return None
    from ..portfolio import Portfolio  # lazy: the portfolio imports the store

    return Portfolio(ws)


def require_reason(reason, *, what: str) -> str:
    """``reason`` stripped; ``ValueError`` when it is empty (every workspace write is audited)."""
    text = reason.strip() if isinstance(reason, str) else ""
    if not text:
        raise ValueError(
            f"{what} inside a portfolio workspace needs a reason (it is audited; the CLI takes "
            "--reason)"
        )
    return text


def _dataset_id(meta: dict):
    from ._ingest import dataset_meta

    return dataset_meta(meta).get("dataset_id")


def dataset_facilities(known: dict, dataset_id: str, *, base=None) -> list:
    """The registered facilities of ``dataset_id`` in ``known`` (a registry's ``all()``), plus
    the entry's own facility id ``base`` when it is registered (even untagged), sorted."""
    return sorted(
        fid for fid, m in known.items() if _dataset_id(m) == dataset_id or (base and fid == base)
    )


def ingest_blockers(known: dict, fids, *, by: str = "CAMBER") -> list:
    """Why an ingest may not write the facilities ``fids`` (``[]``: it may). ``known`` is the
    workspace registry's ``all()``; an unregistered id is not blocked (it starts afresh). ``by``
    names the writer in the message."""
    out = []
    for fid in sorted(set(fids)):
        if fid not in known:
            continue
        state = known[fid].get("state") or "active"
        if state not in INGESTABLE_STATES:
            out.append(
                f"facility {fid} is {state}: {by} ingests only into provisioning or active "
                "facilities (use `camber facility resume|restore` first)"
            )
    return out


def purge_blockers(pf, dataset_id: str, *, fids=None, by: str = "CAMBER") -> list:
    """Why ``dataset_id``'s facilities (or just ``fids``) may not be purged from the workspace
    ``pf`` (``[]``: they may; always ``[]`` when ``pf`` is ``None``, a standalone store).

    A facility under a legal hold keeps everything, and one that has left ``provisioning`` /
    ``active`` (suspended, offboarding, archived) belongs to its lifecycle. An unreadable
    registry or portfolio document blocks the purge.
    """
    if pf is None:
        return []
    try:
        known = pf.registry.all()
        holds = pf.legal_holds()
    except Exception as exc:  # noqa: BLE001 - an unreadable registry blocks a purge
        return [f"the workspace registry could not be read ({type(exc).__name__})"]
    if fids is None:
        fids = [fid for fid, m in known.items() if _dataset_id(m) == dataset_id]
    out = []
    for fid in sorted(set(fids)):
        state = (known.get(fid) or {}).get("state") or "active"
        if fid in holds:
            out.append(
                f"{fid} is under a legal hold: nothing of it is deleted until the hold is "
                "released (`camber retention release`)"
            )
        elif state not in INGESTABLE_STATES:
            out.append(
                f"{fid} is {state}: its lifecycle owns its data (`camber facility "
                f"resume|restore`, or `offboard|archive|purge`), so {by} does not purge it"
            )
    return out


def delete_blockers(pf, fids, *, by: str = "CAMBER", what: str = "delete its data") -> list:
    """Why ``by`` may not ``what`` for the facilities ``fids`` of workspace ``pf`` (``[]``: it
    may; always ``[]`` outside a workspace, ``pf is None``). 0.108 (#250): the one legal-hold
    check -- a facility under a hold keeps everything, so every path that deletes or replaces its
    data is refused. An unreadable portfolio document blocks too."""
    if pf is None:
        return []
    try:
        holds = pf.legal_holds()
    except Exception as exc:  # noqa: BLE001 - an unreadable portfolio blocks a deletion
        return [f"the workspace portfolio could not be read ({type(exc).__name__})"]
    return [
        f"{fid} is under a legal hold: {by} does not {what} until the hold is released "
        "(`camber retention release`)"
        for fid in sorted(set(fids))
        if fid in holds
    ]


def replace_blockers(pf, store_root, fids, *, by: str = "CAMBER") -> list:
    """Why an ingest may not replace the data of the facilities ``fids`` in workspace ``pf``
    (``[]``: it may; always ``[]`` outside a workspace). 0.107: a legal hold means nothing held is
    deleted, and replacing a held facility's partition (a forced or changed re-ingest) deletes the
    rows it holds -- so it is refused, by every writer (the datasets path since 0.107, the
    open-fdd ingest since 0.108, #251). A held facility with no partition yet is not blocked:
    there is nothing to delete."""
    have = [f for f in fids if os.path.isdir(os.path.join(store_root, f"facility_id={f}"))]
    return delete_blockers(
        pf, have, by=by, what="replace its data (a re-ingest deletes the rows it holds)"
    )


def store_delete_gate(
    store_root, fids, *, reason, by: str, what: str = "delete its data", states: bool = False
):
    """The workspace rule for a store or registry delete primitive (0.108, #250).

    Outside a workspace: ``(None, reason)``, nothing refused (a plain store keeps its behaviour).
    Inside one: ``reason`` is required (``ValueError``), a facility of ``fids`` under a legal hold
    refuses the call (:class:`~camber.portfolio.LifecycleError`) and ``(portfolio, reason)`` comes
    back, so the caller audits its deletion with that reason. ``states=True`` (removing a whole
    facility) also refuses one that is not ``provisioning`` / ``active``, by the
    :func:`purge_blockers` rule. There is no override flag: release the hold first.
    """
    pf = workspace_of(store_root)
    if pf is None:
        return None, reason
    text = require_reason(reason, what=by)
    if states:
        why = purge_blockers(pf, "", fids=fids, by=by)
    else:
        why = delete_blockers(pf, fids, by=by, what=what)
    if why:
        from ..portfolio import LifecycleError

        raise LifecycleError("; ".join(why) + "; nothing was deleted")
    return pf, text


STATE_FROZEN = ("archived", "purged")


def state_write_blockers(pf, facility_id, *, by: str = "a config run") -> list:
    """Why ``by`` may not write the per-facility state (``state/<fid>/``: faults, baselines, the
    manifest) of ``facility_id`` in workspace ``pf`` (``[]``: it may; ``[]`` outside one).

    0.108 (#252): an archived facility's state lives in its export bundle (a write would never be
    in the bundle, and the next restore could not reconcile it), and a purged one has none -- held
    or not. A legal hold alone does not block these writes: it blocks deleting or replacing data,
    and a faults fold or a baseline freeze adds to the state.
    """
    if pf is None or not facility_id:
        return []
    out = []
    try:
        state = pf.facility(facility_id).get("state") or "active"
    except KeyError:
        state = None
    if state in STATE_FROZEN:
        out.append(
            f"facility {facility_id} is {state}: {by} writes no state for it (its state lives in "
            "its export bundle; `camber facility restore` it first)"
        )
    return out


def audit_ingest(pf, action: str, *, reason: str, dataset_id: str, res, facilities=None) -> None:
    """One ``action`` line per facility of the ingest result ``res`` (or just ``facilities``):
    the lab writes ``lab.ingest``, the CLI and API ``datasets.ingest``."""
    for fid in res.facilities if facilities is None else facilities:
        pf.audit(
            action,
            reason=reason,
            facility_id=fid,
            details={
                "dataset_id": dataset_id,
                "subset": res.subset,
                "rows": res.rows,
                "skipped": res.skipped,
                "content_hash": res.content_hash,
                "corrections": res.corrections,
            },
        )


def audit_remove(pf, prefix: str, *, reason: str, dataset_id: str, res: dict, purge_store: bool):
    """``<prefix>.remove`` for the removal and one ``<prefix>.purge`` per dropped facility."""
    pf.audit(
        f"{prefix}.remove",
        reason=reason,
        details={
            "dataset_id": dataset_id,
            "freed_bytes": res["freed_bytes"],
            "purge_store": purge_store,
            "facilities_dropped": res["facilities_dropped"],
        },
    )
    for fid in res["facilities_dropped"]:
        pf.audit(
            f"{prefix}.purge", reason=reason, facility_id=fid, details={"dataset_id": dataset_id}
        )
