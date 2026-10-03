"""Decommission an edge device: flush the spool, wait for acknowledgements, retire (0.95, #18).

**Provisional.** Retiring an edge device must not lose data silently. :func:`decommission`

1. drains the device's spool through its sink until every batch is acknowledged (a 2xx from the
   landing) or ``wait`` seconds pass;
2. **refuses** while any batch is still unacknowledged -- unless ``force=True`` with a reason,
   which is itself refused when the facility is under a legal hold. A forced retirement keeps the
   unacknowledged payloads on disk and lists them in the receipt; nothing is deleted;
3. writes a retirement receipt to the spool (``retired.json``), after which the spool takes no
   new batches (``camber edge run`` / ``send-once`` refuse it);
4. records the retirement centrally -- an audit line (OS user, reason) and a note under the
   facility's registry entry (``edge_devices.<device_id>``) -- when the portfolio workspace is
   reachable, or later with :func:`record_retirement` from the receipt the device printed.

It is a dry run unless ``apply=True``, which also needs a ``reason`` and ``yes=True`` or the
typed facility id (``confirm``). The spool lock is held throughout, so a running forwarder cannot
enqueue in the middle; the portfolio lock is held for the registry note. Every step is
restartable: a crash mid-flush leaves a consistent spool (acks are journalled one by one), and a
crash after the receipt but before the registry note is finished by running the command again
(an already-retired spool skips straight to the note, which is recorded once).
"""

from __future__ import annotations

import logging
import platform
import time
from collections.abc import Callable
from dataclasses import dataclass, field

__all__ = ["DecommissionResult", "default_device_id", "decommission", "record_retirement"]

_LOG = logging.getLogger("camber.edge")


@dataclass
class DecommissionResult:
    """The outcome of :func:`decommission` (JSON-ready via ``dataclasses.asdict``)."""

    facility_id: str
    device_id: str
    dry_run: bool
    pending_before: int = 0
    pending_after: int = 0
    forwarded: int = 0
    retired: bool = False
    already_retired: bool = False
    forced: bool = False
    refused: str | None = None
    registry_noted: str | None = None  # "recorded" | "already recorded" | None (no workspace)
    receipt: dict | None = None
    unacknowledged: list = field(default_factory=list)


def default_device_id() -> str:
    """The device id used when the config names none: the machine's node name."""
    return platform.node() or "edge-device"


def _need_reason(reason) -> str:
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("a reason is required to decommission an edge device (it is audited)")
    return reason.strip()


def _held(portfolio, facility_id: str) -> bool:
    return portfolio is not None and facility_id in portfolio.legal_holds()


def decommission(
    spool,
    sink,
    *,
    facility_id: str,
    device_id: str | None = None,
    reason=None,
    apply: bool = False,
    yes: bool = False,
    confirm=None,
    force: bool = False,
    wait: float = 60.0,
    portfolio=None,
    _sleep: Callable[[float], None] = time.sleep,
    _monotonic: Callable[[], float] = time.monotonic,
) -> DecommissionResult:
    """Flush ``spool`` through ``sink``, then retire the device (see the module docstring).

    ``portfolio`` (a :class:`~camber.portfolio.Portfolio`, optional) is where the retirement is
    recorded and legal holds are checked; without one the receipt is returned for
    :func:`record_retirement` to record centrally. Raises ``ValueError`` for a missing reason or
    confirmation, or a forced retirement under a legal hold.
    """
    device_id = device_id or default_device_id()
    res = DecommissionResult(facility_id=facility_id, device_id=device_id, dry_run=not apply)
    if force:
        _need_reason(reason)
        if _held(portfolio, facility_id):
            raise ValueError(
                f"facility {facility_id!r} is under a legal hold: a forced decommission would "
                "abandon unacknowledged data; flush it or release the hold first"
            )
    if apply:
        reason = _need_reason(reason)
        if not yes and confirm != facility_id:
            raise ValueError(
                "decommission retires the device: pass yes=True (--yes) or "
                "confirm=<facility_id> (--confirm, the typed facility id)"
            )
    existing = spool.retirement()
    if existing is not None:
        res.already_retired = True
        res.retired = True
        res.receipt = existing
        res.forced = bool(existing.get("forced"))
        res.pending_before = res.pending_after = spool.depth()[0]
        if existing.get("facility_id") not in (None, facility_id):
            raise ValueError(
                f"this spool was retired for facility {existing.get('facility_id')!r}, "
                f"not {facility_id!r}"
            )
        if apply and portfolio is not None:
            res.registry_noted = record_retirement(portfolio, existing, reason=reason)["noted"]
        return res
    res.pending_before = spool.depth()[0]
    if not apply:
        res.pending_after = res.pending_before
        res.unacknowledged = [_brief(e) for e in spool.pending()]
        if res.pending_before and not force:
            res.refused = (
                f"{res.pending_before} batch(es) are not acknowledged yet; --apply flushes them "
                "first and refuses if any stay unacknowledged"
            )
        return res

    with spool.lock():
        deadline = _monotonic() + max(0.0, wait)
        while True:
            dr = spool.drain(sink, _sleep=_sleep)
            res.forwarded += dr.forwarded
            if dr.remaining == 0 or _monotonic() >= deadline:
                break
        pend = spool.pending()
        res.pending_after = len(pend)
        res.unacknowledged = [_brief(e) for e in pend]
        if pend and not force:
            res.refused = (
                f"{len(pend)} batch(es) still unacknowledged after flushing; the landing did "
                "not confirm them. Fix the link and retry, or pass --force with a reason to "
                "retire anyway (the payloads stay on disk)"
            )
            return res
        from ..portfolio._audit import audit_record

        receipt = audit_record("edge.decommission", facility_id=facility_id, reason=reason)
        receipt.update(
            {
                "device_id": device_id,
                "retired_at": receipt["ts"],
                "forced": bool(pend),
                "unacknowledged": res.unacknowledged,
                "forwarded_during_flush": res.forwarded,
            }
        )
        spool.retire(receipt)
        _LOG.warning(
            "edge.decommission facility=%s device=%s actor=%s forced=%s unacknowledged=%d "
            "reason=%r",
            facility_id,
            device_id,
            receipt.get("actor"),
            bool(pend),
            len(pend),
            reason,
        )
        res.receipt = spool.retirement()
        res.retired = True
        res.forced = bool(pend)
    if portfolio is not None:
        res.registry_noted = record_retirement(portfolio, res.receipt, reason=reason)["noted"]
    return res


def _brief(e) -> dict:
    return {"seq": e.seq, "key": e.key, "bytes": e.bytes, "enqueued": e.enqueued_ts}


def record_retirement(portfolio, receipt: dict, *, reason) -> dict:
    """Record a device's retirement centrally: one audit line and a registry note (idempotent).

    The note goes under the facility's registry entry as ``edge_devices.<device_id>`` =
    ``{"state": "retired", "retired_at", "retired_by", "reason", "forced", "unacknowledged"}``.
    A facility with no registry entry (or a retired id) gets the audit line only. Recording the
    same receipt twice changes nothing (``{"noted": "already recorded"}``). The audit line is
    written before the note, so a crash between them is repaired by recording again.
    """
    reason = _need_reason(reason)
    fid = receipt.get("facility_id")
    dev = receipt.get("device_id")
    if not fid or not dev or not receipt.get("retired_at"):
        raise ValueError("not a retirement receipt (needs facility_id, device_id, retired_at)")
    note = {
        "state": "retired",
        "retired_at": receipt["retired_at"],
        "retired_by": receipt.get("actor"),
        "reason": receipt.get("reason") or reason,
        "forced": bool(receipt.get("forced")),
        "unacknowledged": len(receipt.get("unacknowledged") or []),
    }
    with portfolio.lock():
        entry = portfolio.registry.get(fid)
        devices = dict(entry.get("edge_devices") or {}) if entry else {}
        if entry and (devices.get(dev) or {}).get("retired_at") == note["retired_at"]:
            return {"noted": "already recorded", "facility_id": fid, "device_id": dev}
        if not entry:
            portfolio.audit(
                "edge.decommission",
                reason=reason,
                facility_id=fid,
                state=None,
                details={"device_id": dev, **{k: v for k, v in note.items() if k != "reason"}},
            )
            return {"noted": "audit only (no registry entry)", "facility_id": fid, "device_id": dev}
        # audited (edge.decommission) before the registry note, under the same lock
        portfolio.note_edge_device(fid, dev, note, reason=reason, action="edge.decommission")
    return {"noted": "recorded", "facility_id": fid, "device_id": dev}
