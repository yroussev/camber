"""Versioned M&V baselines and the rebaselining policy (provisional; issue #21 phase 21d).

An M&V saving is only as good as the baseline it is measured against, and a baseline that moves
silently makes every past saving unanswerable. This module keeps the two apart:

* :class:`MVBaselineStore` -- the frozen baselines of one facility's meters, **every version
  kept** (``state/<facility_id>/mv_baselines.json`` inside a portfolio workspace). It wraps
  :class:`~camber.store.modelstore.BaselineStore`: a first baseline is *frozen*
  (:meth:`MVBaselineStore.freeze_version`), a later one supersedes it only through the attributed
  :meth:`MVBaselineStore.rebaseline`, and the superseded versions stay in the record's
  ``history``. Each version carries a **provenance** record: the reason and the trigger ids, who
  accepted it plus the OS user and host, the data window and a sha256 of the fit frame, the model
  (``as_dict``), its fit statistics, regression tests and SEP verdict, the declared method and
  kernel, the adjustment ledger and the CAMBER version.
* :class:`RebaselinePolicy`, :func:`assess_triggers`, :func:`new_baseline_window` and
  :func:`propose_rebaseline` -- *when* a baseline no longer holds and what to do about it. They
  only ever **propose**. CAMBER never rebaselines automatically: an unresolved trigger declines
  the saving after its date (a partial result with a caveat), and moving the baseline is the
  operator's audited ``camber mv rebaseline``.

**Triggers** (issue #21 §2.6):

====  ===========================================================  =====================
T1    a material step change the declared ECMs do not explain        PELT (#21 §2.5)
T2    a declared significant change (the event log)                  operator
T3    a tracked static factor changed beyond its tolerance           operator
T4    the model is invalid, or #20 grades the reporting period       #20 coverage
      ``severe``
T5    the achievement period exceeds 36 months                       SEP 2019 Ed. 2 §4.2
T6    a new ECM with at least 12 months of post-ECM data             BPA 2024 §3.1.8
====  ===========================================================  =====================

On a billing meter T1 is, opt-in, the step scan of :mod:`camber.mandv.billsteps`
(``rebaseline.bill_steps``; 0.95, #74), and :mod:`camber.mandv.billwindow` searches its new window
of whole bills.

**Outcomes** follow the BPA *Regression for M&V Reference Guide* (2024) taxonomy: a static change
is an engineering or sub-meter non-routine adjustment (NRA), a minor process change an indicator
NRA, and a major process change a **rebaseline, then a chain** that keeps cumulative reporting
(:func:`camber.mandv.methods.sequential_chain` across the versions). Where a step counts as
*major* is CAMBER's choice, not a standard's: a level shift of at least ``major_step_frac`` of
the baseline projection (default 0.20). A trigger dated inside an SEP chain's intermediate period
cannot be adjusted at all (both links share that model), so its outcome is a rebaseline or
another intermediate window.

**The new-baseline window.** The latest 12 consecutive months (``min_baseline_days``) that start
at least ``settle_days`` after the trigger, overlap no ECM installation window (each ECM date ±
``settle_days``), miss at most ``max_missing_frac`` of their days, give a model valid under
``require_validity`` and cover the expected conditions (#20 tier not ``severe``). When no such
window exists yet, the rebaseline is **declined** with the number of days still needed.

Every name here is provisional (docs/API-STABILITY.md).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import socket
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ..store.modelstore import BaselineRecord, BaselineStore
from .adjustments import (
    DEFAULT_SETTLE_DAYS,
    VALIDITY,
    EcmSchedule,
    StaticFactorAdjustment,
    adjustment_from_dict,
    check_validity,
    is_material,
)

__all__ = [
    "MV_BASELINES_FILE",
    "MV_SCHEMA",
    "MV_MODEL_TYPES",
    "TRIGGERS",
    "OUTCOMES",
    "mv_kind",
    "mv_model_from_dict",
    "fit_frame_sha256",
    "mv_provenance",
    "version_label",
    "MVBaselineStore",
    "RebaselinePolicy",
    "DeclaredChange",
    "StaticFactorChange",
    "events_from_entry",
    "Trigger",
    "assess_triggers",
    "first_block",
    "event_phrase",
    "window_anchor",
    "BaselineWindow",
    "new_baseline_window",
    "RebaselineProposal",
    "propose_rebaseline",
    "version_segments",
]

#: The file an M&V baseline store lives in, under ``state/<facility_id>/`` in a workspace.
MV_BASELINES_FILE = "mv_baselines.json"
#: The on-disk schema of :data:`MV_BASELINES_FILE` (its ``"schema"`` field).
MV_SCHEMA = 1

#: Trigger ids and what each means (issue #21 §2.6).
TRIGGERS = {
    "T1": "material step change not explained by a declared ECM",
    "T2": "declared significant change",
    "T3": "tracked static factor changed beyond tolerance",
    "T4": "model invalid, or the reporting period is a severe extrapolation",
    "T5": "achievement period exceeds the maximum",
    "T6": "new ECM with at least 12 months of post-ECM data",
}

#: What a trigger calls for (BPA 2024 taxonomy): an NRA of some kind, or a rebaseline + chain.
OUTCOMES = ("nra_indicator", "nra_static", "nra_engineering", "rebaseline")

_BLOCKING = {"T1", "T2", "T3", "T4", "T5"}  # T6 is advisory: a recommendation, not a decline


def _day(x) -> pd.Timestamp:
    ts = pd.Timestamp(x)
    if ts.tzinfo is not None:
        ts = ts.tz_localize(None)
    return ts.normalize()


def _ds(x) -> str:
    return str(_day(x).date())


def _utc_now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- models


def mv_kind(role) -> str:
    """The store ``kind`` of an M&V baseline for an energy ``role`` (``"mv_<role>"``).

    The kind names *what is measured*, not the model form, so a rebaseline that changes the form
    (a 3PC becomes a 5P, or a change-point + driver model) stays on the same versioned record.
    """
    return f"mv_{getattr(role, 'value', role)}"


def mv_model_from_dict(d: dict):
    """Rebuild any M&V model from its ``as_dict`` payload, dispatching on its ``"type"``."""
    from .basetemp import BillingDegreeDayModel
    from .degreeday import DegreeDayModel
    from .models import ChangePointModel
    from .multivariable import ChangePointDriverModel
    from .towt import TOWTModel

    types: dict[str, Any] = {
        "ChangePointModel": ChangePointModel,
        "ChangePointDriverModel": ChangePointDriverModel,
        "TOWTModel": TOWTModel,
        "DegreeDayModel": DegreeDayModel,
        "BillingDegreeDayModel": BillingDegreeDayModel,  # 0.94 (#72)
    }
    typ = types.get(str((d or {}).get("type")))
    if typ is None:
        raise KeyError(f"unknown M&V model type {(d or {}).get('type')!r} (known: {sorted(types)})")
    return typ.from_dict(d)


class _AnyMVModel:
    from_dict = staticmethod(mv_model_from_dict)


#: The ``model_types`` map of :class:`MVBaselineStore`: any kind rebuilds by the model's type.
MV_MODEL_TYPES = {"*": _AnyMVModel}


def fit_frame_sha256(daily: pd.DataFrame, columns=None) -> str:
    """sha256 of a fit frame: one ``date,value,...`` line per row at full float precision.

    Recomputing it over the same window later tells whether the data under a frozen baseline has
    changed since (a re-ingest, a correction) -- a stored model is then no longer reproducible.
    The driver columns of a change-point + driver entry (``drv:...``) are hashed after
    ``columns``; a frame without them hashes exactly as before. ``columns`` defaults to
    ``("oat", "energy")``, and for a bills frame (a ``days`` column; 0.94, #72) to
    ``("oat", "energy", "days", "hdd", "cdd")``.
    """
    from ._mvform import driver_columns

    if columns is None:
        columns = ("oat", "energy")
        if "days" in daily.columns:  # 0.94 (#72): a bills frame also hashes its days and HDD/CDD
            columns = ("oat", "energy", "days", "hdd", "cdd")
    h = hashlib.sha256()
    cols = [c for c in columns if c in daily.columns] + driver_columns(daily)
    for ts, row in zip(daily.index, daily[cols].itertuples(index=False)):
        vals = ",".join(repr(float(v)) for v in row)
        h.update(f"{pd.Timestamp(ts).isoformat()},{vals}\n".encode())
    return h.hexdigest()


def _json_safe(x):
    if isinstance(x, dict):
        return {str(k): _json_safe(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_json_safe(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return float(x) if np.isfinite(x) else None
    if isinstance(x, np.integer):
        return int(x)
    if isinstance(x, (np.bool_,)):
        return bool(x)
    if isinstance(x, (pd.Timestamp, _dt.date)):
        return str(x)
    return x


def _content_sha(prov: dict) -> str:
    body = {k: v for k, v in prov.items() if k not in ("content_sha256", "adjustments")}
    return hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()


def mv_provenance(
    model,
    daily: pd.DataFrame,
    *,
    reason: str,
    accepted_by: str,
    method: str,
    kernel: str,
    validity: str = "g14",
    trigger_ids=(),
    trigger_date=None,
    fit_stats=None,
    regression_tests=None,
    sep_verdict=None,
    coverage=None,
    adjustments=(),
    extra: dict | None = None,
) -> dict:
    """The provenance record of one M&V baseline version (JSON-safe).

    ``daily`` is the fit frame (``oat`` / ``energy`` by day). Records the reason and the trigger
    ids, ``accepted_by`` plus the OS user and host (``os_user`` / ``host``: until CAMBER has
    authentication these are what an audit can check, docs/SECURITY.md), the data window and
    ``fit_frame_sha256``, the model's ``as_dict``, the ``FitStats`` / ``RegressionTests`` / SEP
    verdict, the declared ``method`` and ``kernel``, the ``validity`` regime, the adjustment ledger,
    the CAMBER version and a ``content_sha256`` over everything but the (append-only) ledger.
    """
    from .. import __version__
    from ..portfolio._audit import _actor

    if not str(reason).strip():
        raise ValueError("an M&V baseline needs a reason")
    if not str(accepted_by).strip():
        raise ValueError("an M&V baseline needs accepted_by (who accepted it)")
    check_validity(validity)
    idx = daily.index
    prov = {
        "reason": str(reason),
        "trigger_ids": list(trigger_ids or ()),
        "trigger_date": None if trigger_date is None else _ds(trigger_date),
        "accepted_by": str(accepted_by),
        "os_user": _actor(),
        "host": socket.gethostname(),
        "data_window": [_ds(idx.min()), _ds(idx.max())] if len(idx) else None,
        "n_rows": int(len(daily)),
        "fit_frame_sha256": fit_frame_sha256(daily),
        "model": model.as_dict(),
        "model_type": type(model).__name__,
        "fit_stats": None if fit_stats is None else _json_safe(fit_stats.as_dict()),
        "regression_tests": (
            None if regression_tests is None else _json_safe(regression_tests.as_dict())
        ),
        "sep_validity": None if sep_verdict is None else _json_safe(sep_verdict.as_dict()),
        "coverage": None if coverage is None else _json_safe(coverage),
        "method": str(method),
        "kernel": str(kernel),
        "validity": validity,
        "camber_version": __version__,
        "created_at": _utc_now(),
        **_json_safe(dict(extra or {})),
    }
    prov["adjustments"] = [_ledger_row(a, accepted_by, reason) for a in adjustments or ()]
    prov["content_sha256"] = _content_sha(prov)
    return prov


def _ledger_row(entry, by: str, reason: str, at: str | None = None) -> dict:
    d = entry.as_dict() if hasattr(entry, "as_dict") else dict(entry)
    adjustment_from_dict(d)  # refuse anything the ledger could not read back
    return {
        "entry": _json_safe(d),
        "recorded_by": str(by),
        "reason": str(reason),
        "recorded_at": at or _utc_now(),
    }


def version_label(rec) -> str:
    """``"v<n>"``: the version a record (or its history dict) carries in its provenance."""
    prov = rec.get("provenance") if isinstance(rec, dict) else rec.provenance
    n = (prov or {}).get("version")
    return f"v{int(n)}" if n is not None else "v?"


# --------------------------------------------------------------------------- the store


class MVBaselineStore(BaselineStore):
    """Frozen M&V baselines with **every version kept** (provisional).

    A :class:`~camber.store.modelstore.BaselineStore` whose records live under
    ``"mv_baselines"`` in a file with a ``"schema"`` field (:data:`MV_SCHEMA`), rebuild any M&V
    model (:data:`MV_MODEL_TYPES`) and carry a provenance record (:func:`mv_provenance`) with a
    ``version`` number. Identity is the drift store's: ``(facility_id, equip, kind)`` with ``kind``
    from :func:`mv_kind`. Superseded versions stay in the live record's ``history`` -- past
    reported savings depend on them, so the portfolio retention class ``mv_baselines`` keeps all
    versions indefinitely.
    """

    LIST_KEY = "mv_baselines"
    SCHEMA = MV_SCHEMA

    def __init__(self, path=None, *, facility_id=None, legacy_sites=None, model_types=None):
        super().__init__(
            path,
            facility_id=facility_id,
            legacy_sites=legacy_sites,
            model_types=MV_MODEL_TYPES if model_types is None else model_types,
        )

    # ----------------------------------------------------------------- versions
    def versions(self, site: str, equip: str, kind: str) -> list:
        """Every version of one baseline, oldest first, as :class:`BaselineRecord` s."""
        rec = self.get(site, equip, kind)
        if rec is None:
            return []
        out = [BaselineRecord.from_dict(h) for h in rec.history]
        out.append(rec)
        out.sort(key=lambda r: int((r.provenance or {}).get("version") or 0))
        return out

    def version(self, site: str, equip: str, kind: str, label) -> BaselineRecord | None:
        """One version by label (``"v2"``) or number (``2``); ``None`` if there is none."""
        want = str(label if str(label).startswith("v") else f"v{int(label)}")
        for r in self.versions(site, equip, kind):
            if version_label(r) == want:
                return r
        return None

    def in_force(self, site: str, equip: str, kind: str, date) -> BaselineRecord | None:
        """The latest version whose fit window ended before ``date`` (``None`` if none had)."""
        d = _day(date)
        hit = None
        for r in self.versions(site, equip, kind):
            if r.period_end and _day(r.period_end) < d:
                hit = r
        return hit

    def model_of(self, rec: BaselineRecord):
        """The rebuilt model of one version."""
        return rec.model(model_types=self.model_types)

    # ----------------------------------------------------------------- writes
    def freeze_version(
        self, model, *, site: str, equip: str, kind: str, frozen_at: str, period, provenance: dict
    ) -> BaselineRecord:
        """Freeze version 1 of a baseline. **Refuses to overwrite one** (as ``freeze``)."""
        prov = dict(provenance)
        prov["version"] = 1
        prov["content_sha256"] = _content_sha(prov)
        return self.freeze(
            model,
            site=site,
            equip=equip,
            kind=kind,
            frozen_at=frozen_at,
            period=(_ds(period[0]), _ds(period[1])),
            reason=str(prov.get("reason") or ""),
            accepted_by=str(prov.get("accepted_by") or ""),
            provenance=prov,
        )

    def rebaseline(
        self,
        model,
        *,
        site: str,
        equip: str,
        kind: str,
        at: str,
        period,
        provenance: dict,
    ) -> BaselineRecord:
        """Supersede the live version with a new one -- an attributed **operator decision**.

        The new window must start after the live version's window ends (a baseline never moves
        backwards over data it was already compared against). The superseded version is kept in
        ``history``; the new one records ``version`` = previous + 1 and ``supersedes_version``.
        """
        prev = self.get(site, equip, kind)
        if prev is None:
            raise ValueError(
                f"no frozen M&V baseline for {equip!r}/{kind!r} to rebaseline; freeze one first"
            )
        start, end = _day(period[0]), _day(period[1])
        if prev.period_end and start <= _day(prev.period_end):
            raise ValueError(
                f"the new baseline window {start.date()}..{end.date()} must start after "
                f"{version_label(prev)}'s window ends ({prev.period_end})"
            )
        prov = dict(provenance)
        n = int((prev.provenance or {}).get("version") or len(prev.history) + 1)
        prov["version"] = n + 1
        prov["supersedes_version"] = version_label(prev)
        prov["content_sha256"] = _content_sha(prov)
        return self.accept_new_normal(
            model,
            site=site,
            equip=equip,
            kind=kind,
            accepted_by=str(prov.get("accepted_by") or ""),
            reason=str(prov.get("reason") or ""),
            at=at,
            period=(str(start.date()), str(end.date())),
            provenance=prov,
        )

    def add_adjustments(
        self, entries, *, site: str, equip: str, kind: str, accepted_by: str, reason: str, at=None
    ) -> list:
        """Append accepted ledger entries to the **live** version's provenance (append-only).

        Each entry must be accepted (a proposal is refused, as by
        :func:`~camber.mandv.adjustments.apply_adjustments`) and must round-trip through
        :func:`~camber.mandv.adjustments.adjustment_from_dict`. An entry identical to one already
        recorded is skipped. Returns the rows added.
        """
        if not str(accepted_by).strip() or not str(reason).strip():
            raise ValueError("recording adjustments needs accepted_by and reason")
        rec = self.get(site, equip, kind)
        if rec is None:
            raise ValueError(f"no frozen M&V baseline for {equip!r}/{kind!r} to adjust")
        have = [
            json.dumps(r["entry"], sort_keys=True) for r in rec.provenance.get("adjustments", [])
        ]
        added = []
        for a in entries:
            if getattr(a, "status", "accepted") != "accepted":
                raise ValueError(f"ledger entry {a.reason!r} is a proposal; accept it first")
            row = _ledger_row(a, accepted_by, reason, at)
            key = json.dumps(row["entry"], sort_keys=True)
            if key in have:
                continue
            have.append(key)
            added.append(row)
        if added:
            rec.provenance.setdefault("adjustments", []).extend(added)
        return added

    def ledger(self, rec: BaselineRecord) -> list:
        """The version's recorded adjustments, rebuilt (lossless, fits included)."""
        return [
            adjustment_from_dict(r["entry"]) for r in (rec.provenance or {}).get("adjustments", [])
        ]

    @staticmethod
    def verify(rec: BaselineRecord) -> bool:
        """Whether a version's provenance still matches its ``content_sha256``."""
        prov = rec.provenance or {}
        return bool(prov.get("content_sha256")) and prov["content_sha256"] == _content_sha(prov)


# --------------------------------------------------------------------------- policy


_POLICY_KEYS = (
    "detect",
    "min_segment_days",
    "settle_days",
    "materiality",
    "major_step_frac",
    "min_baseline_days",
    "max_missing_frac",
    "require_validity",
    "max_achievement_months",
    "ecm_gap_days",
    "events",
    "static_factors",
    "bill_steps",  # 0.95 (#74): the opt-in step test on bills (camber.mandv.billsteps)
)


@dataclass(frozen=True)
class RebaselinePolicy:
    """When a baseline no longer holds, and what a replacement must satisfy (provisional).

    ``schedule`` is the entry's :class:`~camber.mandv.adjustments.EcmSchedule` -- the ECM dates and
    the settle window the confounding guard also uses, so there is one ``settle_days``.
    ``require_validity`` is a :data:`~camber.mandv.adjustments.VALIDITY` regime (the entry's
    ``mv[].validity``). ``materiality`` is the per-day step size below which a detected step is
    ignored (with the ``2 SE`` rule of :func:`~camber.mandv.adjustments.is_material`);
    ``major_step_frac`` is CAMBER's line between an indicator NRA and a rebaseline, as a fraction
    of the baseline's mean daily energy.
    """

    schedule: EcmSchedule = field(default_factory=EcmSchedule)
    min_baseline_days: int = 365
    max_missing_frac: float = 0.10
    materiality: float = 0.0
    major_step_frac: float = 0.20
    require_validity: str = "g14"
    max_achievement_months: int = 36
    ecm_gap_days: int = 365
    detect: bool = True
    min_segment_days: int = 28
    #: 0.95 (#74): the step test on a billing meter (a :class:`~camber.mandv.billsteps.
    #: BillStepRule`), opt-in; ``None`` keeps T1 as before (and out of :meth:`as_dict`)
    bill_steps: Any = None

    def __post_init__(self):
        check_validity(self.require_validity)
        if not 0.0 <= float(self.max_missing_frac) < 1.0:
            raise ValueError("max_missing_frac must be in [0, 1)")
        for k in (
            "min_baseline_days",
            "max_achievement_months",
            "ecm_gap_days",
            "min_segment_days",
        ):
            if int(getattr(self, k)) < 1:
                raise ValueError(f"{k} must be >= 1")
        if float(self.major_step_frac) <= 0 or float(self.materiality) < 0:
            raise ValueError("major_step_frac must be > 0 and materiality >= 0")

    @property
    def settle_days(self) -> int:
        """The settle window after an ECM (the schedule's; one value for the whole entry)."""
        return self.schedule.settle_days

    @classmethod
    def from_entry(cls, entry: dict) -> RebaselinePolicy:
        """Build from an ``mv`` config entry: its ``ecm_dates``, ``settle_days``, ``validity``
        and ``rebaseline`` block.

        The settle window is ``mv[].settle_days`` (one value, shared with the confounding guard).
        A ``rebaseline.settle_days`` that *differs* from it is refused with a clear message rather
        than reconciled silently; an equal one is accepted. Unknown ``rebaseline`` keys are an
        error. ``events`` and ``static_factors`` are read by :func:`events_from_entry`.
        """
        rb = dict(entry.get("rebaseline") or {})
        extra = set(rb) - set(_POLICY_KEYS)
        if extra:
            raise ValueError(f"mv.rebaseline: unknown key(s) {sorted(extra)}")
        settle = entry.get("settle_days", DEFAULT_SETTLE_DAYS)
        if "settle_days" in rb and rb["settle_days"] != settle:
            raise ValueError(
                f"mv.rebaseline.settle_days ({rb['settle_days']}) differs from mv.settle_days "
                f"({settle}): the settle window is one value for the whole entry -- set it once, "
                "as mv.settle_days"
            )
        sched = EcmSchedule.from_dict(
            {"ecm_dates": list(entry.get("ecm_dates") or ()), "settle_days": settle}
        )
        kw = {
            k: rb[k]
            for k in (
                "detect",
                "min_segment_days",
                "materiality",
                "major_step_frac",
                "min_baseline_days",
                "max_missing_frac",
                "max_achievement_months",
                "ecm_gap_days",
            )
            if k in rb
        }
        validity = rb.get("require_validity", entry.get("validity", "g14"))
        if validity not in VALIDITY:
            raise ValueError(f"mv.validity must be one of {VALIDITY}, got {validity!r}")
        if rb.get("bill_steps") is not None:  # 0.95 (#74)
            from .billsteps import BillStepRule

            kw["bill_steps"] = BillStepRule.from_spec(rb["bill_steps"])
        return cls(schedule=sched, require_validity=validity, **kw)

    def as_dict(self) -> dict:
        """Return as a plain JSON-safe dict (``bill_steps`` only when set)."""
        d = asdict(self)
        d["schedule"] = self.schedule.as_dict()
        if self.bill_steps is None:
            d.pop("bill_steps", None)
        else:
            d["bill_steps"] = self.bill_steps.as_dict()
        return d


@dataclass(frozen=True)
class DeclaredChange:
    """One entry of the event log (T2): a significant change the operator declares.

    ``magnitude`` is BPA's class: ``"static"`` (a static-factor change: engineering or sub-meter
    NRA), ``"minor"`` (an indicator NRA) or ``"major"`` (a rebaseline, then a chain).
    """

    date: str
    description: str
    magnitude: str = "major"
    id: str | None = None

    def __post_init__(self):
        if self.magnitude not in ("static", "minor", "major"):
            raise ValueError(f"magnitude must be static, minor or major, got {self.magnitude!r}")
        object.__setattr__(self, "date", _ds(self.date))
        if not str(self.description).strip():
            raise ValueError("a declared change needs a description")


@dataclass(frozen=True)
class StaticFactorChange:
    """A tracked static factor's value at a date (T3), against the baseline's value."""

    factor: str
    baseline_value: float
    value: float
    date: str
    tolerance: float = 0.05

    def __post_init__(self):
        object.__setattr__(self, "date", _ds(self.date))
        if not float(self.baseline_value) > 0:
            raise ValueError("a static factor's baseline_value must be > 0")
        if float(self.tolerance) < 0:
            raise ValueError("tolerance must be >= 0")

    @property
    def change(self) -> float:
        """The relative change ``value / baseline_value - 1``."""
        return float(self.value) / float(self.baseline_value) - 1.0


def events_from_entry(entry: dict) -> tuple:
    """``(declared changes, static factors)`` of an ``mv`` entry's ``rebaseline`` block."""
    rb = dict(entry.get("rebaseline") or {})
    ev, sf = rb.get("events", []), rb.get("static_factors", [])
    if not isinstance(ev, list) or not isinstance(sf, list):
        raise ValueError("mv.rebaseline.events and static_factors must be lists")
    try:
        return (
            tuple(DeclaredChange(**dict(e)) for e in ev),
            tuple(StaticFactorChange(**dict(s)) for s in sf),
        )
    except TypeError as e:
        raise ValueError(f"mv.rebaseline: {e}") from None


# --------------------------------------------------------------------------- triggers


@dataclass
class Trigger:
    """One reason the baseline may no longer hold.

    ``id`` is ``T1``..``T6`` (:data:`TRIGGERS`), ``key`` a stable ``"T1:2020-03-16"`` recorded in
    a rebaseline's provenance, ``date`` when it applies from. ``outcome`` is what it calls for
    (:data:`OUTCOMES`). ``blocks`` says whether, unresolved, it declines savings from ``date`` on
    (T6 is advisory). ``resolved_by`` names the ledger entry or later version that resolves it.
    """

    id: str
    date: str
    title: str
    detail: str
    outcome: str
    basis: str
    blocks: bool = True
    resolved: bool = False
    resolved_by: str | None = None
    evidence: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Stable id: ``"<id>:<date>"``."""
        return f"{self.id}:{self.date}"

    def as_dict(self) -> dict:
        """Return as a plain JSON-safe dict (with ``key``)."""
        d = asdict(self)
        d["key"] = self.key
        return _json_safe(d)


def _t(tid, date, detail, outcome, basis, **kw) -> Trigger:
    return Trigger(
        tid, _ds(date), TRIGGERS[tid], detail, outcome, basis, blocks=tid in _BLOCKING, **kw
    )


def _ledger_near(ledger, date, days: int, *, static_factor=None):
    """The first accepted ledger entry starting within ``days`` of ``date`` (optionally the
    static-factor entry for ``static_factor``)."""
    d = _day(date)
    for a in ledger or ():
        if getattr(a, "status", "accepted") != "accepted":
            continue
        if static_factor is not None:
            if isinstance(a, StaticFactorAdjustment) and a.factor == static_factor:
                return a
            continue
        if abs((_day(a.start) - d).days) <= days:
            return a
    return None


def _savings_steps(rep: pd.DataFrame, model, pol: RebaselinePolicy, max_steps: int = 5) -> list:
    """Sustained level shifts in the reporting days' relative deviation from the baseline.

    ``x = energy / projection - 1`` against the **frozen** model -- the savings series of issue
    #21 §2.5, made relative so a change that scales the load (occupancy, a closed wing) is one
    level shift whatever the season. Segmented by PELT (Killick, Fearnhead & Eckley 2012; the
    method of Touzani et al. 2019) with a Gaussian mean-change cost on the rho-inflated variance
    ``s2 * kappa`` and a ``3 ln n`` penalty, re-segmented until stable (at most 3 rounds), with
    at most ``max_steps`` steps and segments of at least ``min_segment_days``. Each step reports
    ``delta_rel`` (the level change), ``delta`` (that change in energy per day at the post-step
    projection), its rho-inflated standard error and ``z``.
    """
    from ._mvform import design_rows
    from .nonroutine import _pelt, _pelt_capped, _segment_ids
    from .stats import lag1_autocorrelation

    pred = np.asarray(model.predict(design_rows(rep, model)), dtype=float)
    ok = np.isfinite(pred) & (pred > 0) & np.isfinite(rep["energy"].to_numpy(float))
    if ok.sum() < 2 * pol.min_segment_days:
        return []
    x = rep["energy"].to_numpy(float)[ok] / pred[ok] - 1.0
    idx = rep.index[ok]
    proj = pred[ok]
    n = len(x)
    pen = 3.0 * float(np.log(n))
    d = np.diff(x)
    scale = (1.4826 * float(np.median(np.abs(d - np.median(d))))) ** 2 / 2.0
    if not scale > 0:
        scale = float(np.var(x, ddof=1))
    if not (np.isfinite(scale) and scale > 1e-18):
        return []  # noise-free (x is relative: 1e-9 is rounding)
    cps = _pelt(x, scale=scale, penalty=pen, min_seg=pol.min_segment_days)
    kappa, s2 = 1.0, scale
    for _ in range(3):
        seg = _segment_ids(n, cps)
        means = np.array([x[seg == k].mean() for k in range(seg.max() + 1)])
        resid = x - means[seg]
        s2 = float(resid.var(ddof=len(means))) if n > len(means) else scale
        rho = lag1_autocorrelation(resid, index=idx)
        kappa = 1.0 if rho is None else (1.0 + rho) / (1.0 - rho)
        # no residual variability (a noise-free series): nothing can be told apart from noise.
        # x is relative, so a floor of 1e-9 is rounding noise.
        found = _pelt_capped(
            x,
            scale=s2 * kappa,
            penalty=pen,
            min_seg=pol.min_segment_days,
            max_steps=max_steps,
            floor=1e-18,
        )
        if found is None:
            return []
        new = found[0]
        if new == cps:
            break
        cps = new
    seg = _segment_ids(n, cps)
    out = []
    for i, c in enumerate(cps):
        a, b = x[seg == i], x[seg == i + 1]
        drel = float(b.mean() - a.mean())
        se_rel = float(np.sqrt(kappa * s2 * (1.0 / len(a) + 1.0 / len(b))))
        level = float(proj[seg == i + 1].mean())
        se = se_rel * level if se_rel > 0 else None
        out.append(
            {
                "date": _day(idx[c]),
                "delta_rel": round(drel, 4),
                "delta": round(drel * level, 4),
                "se": None if se is None else round(se, 4),
                "z": None if not se_rel > 0 else round(drel / se_rel, 2),
            }
        )
    return out


def _months_between(a, b) -> float:
    return (_day(b) - _day(a)).days / (365.25 / 12.0)


def assess_triggers(
    daily: pd.DataFrame,
    model,
    *,
    baseline,
    policy: RebaselinePolicy | None = None,
    as_of=None,
    reporting=None,
    fit_valid: dict | None = None,
    events=(),
    static_factors=(),
    ledger=(),
    next_version_start=None,
    intermediate_period=None,
    extrapolation=None,
) -> list:
    """Evaluate triggers T1-T6 for one baseline version; **read-only**, never adjusts anything.

    ``daily`` holds daily ``oat`` / ``energy`` rows; ``model`` is the version's model and
    ``baseline`` its ``[start, end]`` fit window. ``reporting`` defaults to the day after the
    baseline through ``as_of`` (default: the last row). Steps (T1) are sought over **every day
    since the baseline ended**, not only the reporting days, so a change between the two is seen.
    ``fit_valid`` is ``{"g14": bool, "sep": bool | None}`` of the version (from its provenance).
    ``events`` / ``static_factors`` are :class:`DeclaredChange` / :class:`StaticFactorChange` s;
    ``ledger`` the accepted adjustments already on the version, which resolve the NRA-class
    triggers they cover (dated within ``settle_days``); ``next_version_start`` the window start
    of a later version, which resolves every trigger dated before it (a rebaseline happened).
    ``intermediate_period`` is the SEP chain's, when the declared method chains.

    Returns the triggers in date order, each with ``resolved`` / ``resolved_by``.
    """
    from .coverage import ExtrapolationPolicy, assess_coverage

    pol = policy or RebaselinePolicy()
    sched = pol.schedule
    b1 = _day(baseline[1])
    idx = daily.index
    last = _day(as_of) if as_of is not None else (_day(idx.max()) if len(idx) else b1)
    r0, r1 = (
        (_day(reporting[0]), min(_day(reporting[1]), last))
        if reporting is not None
        else (b1 + pd.Timedelta(days=1), last)
    )
    rep = daily.loc[(idx >= r0) & (idx <= r1 + pd.Timedelta(hours=23))]
    # detection looks at every day since the baseline ended, not only the requested reporting
    # days: a step between the two would otherwise go unseen
    since = daily.loc[(idx > b1 + pd.Timedelta(hours=23)) & (idx <= r1 + pd.Timedelta(hours=23))]
    out: list = []

    # T2 -- the event log
    for ev in events or ():
        if not (b1 < _day(ev.date) <= last):
            continue
        outcome = {"static": "nra_engineering", "minor": "nra_indicator", "major": "rebaseline"}[
            ev.magnitude
        ]
        tr = _t(
            "T2",
            ev.date,
            f"{ev.description} ({ev.magnitude})",
            outcome,
            "event log",
            evidence={"id": ev.id, "magnitude": ev.magnitude},
        )
        out.append(tr)

    # T1 -- detected, material, unexplained steps in the reporting period
    step_caveat = None
    steps: list = []
    bill_rule = pol.bill_steps if ("days" in daily.columns and "start" in daily.columns) else None
    if pol.detect and bill_rule is not None:  # 0.95 (#74): the opt-in scan on bills
        from .billsteps import bill_steps

        try:
            steps = bill_steps(since, model, bill_rule, schedule=sched, events=events)
        except (ValueError, np.linalg.LinAlgError) as e:
            step_caveat = f"bill step scan not run: {e}"
    elif pol.detect and len(since) >= 2 * pol.min_segment_days:
        try:
            steps = _savings_steps(since, model, pol)
        except (ValueError, np.linalg.LinAlgError) as e:
            step_caveat = f"step detection not run: {e}"
    for st in steps:
        if not is_material(st["delta"], st["se"], pol.materiality):
            continue
        if sched.near(st["date"]) is not None:
            continue  # explained: the measure itself
        declared = [
            e for e in events or () if abs((_day(e.date) - st["date"]).days) <= sched.settle_days
        ]
        if declared:
            continue  # the event log already carries it (T2)
        major = abs(st["delta_rel"]) >= pol.major_step_frac
        detail = (
            f"step of {st['delta']:+.4g} per day ({st['delta_rel']:+.0%} of the baseline "
            f"projection), z {st['z']:.1f}"
            if st["z"] is not None
            else f"step of {st['delta']:+.4g} per day ({st['delta_rel']:+.0%})"
        )
        basis = "PELT on the reporting days' deviation from the frozen baseline projection"
        ev_keys: tuple = ("delta", "se", "z", "delta_rel")
        if bill_rule is not None:  # 0.95 (#74)
            detail += f" over {st['n_before']} + {st['n_after']} bills"
            basis = bill_rule.describe()
            ev_keys += ("n_before", "n_after", "detail")
        out.append(
            _t(
                "T1",
                st["date"],
                detail,
                "rebaseline" if major else "nra_indicator",
                basis,
                evidence={k: st[k] for k in ev_keys},
            )
        )

    # T3 -- tracked static factors
    for sf in static_factors or ():
        if not (b1 < _day(sf.date) <= last) or abs(sf.change) <= float(sf.tolerance):
            continue
        out.append(
            _t(
                "T3",
                sf.date,
                f"{sf.factor} changed {sf.change:+.1%} (tolerance {float(sf.tolerance):.0%})",
                "nra_static",
                "tracked static factor",
                evidence={
                    "factor": sf.factor,
                    "baseline_value": sf.baseline_value,
                    "value": sf.value,
                },
            )
        )

    # T4 -- validity of the version, and #20 coverage of the reporting period
    fv = dict(fit_valid or {})
    need = {"g14": ("g14",), "sep": ("sep",), "both": ("g14", "sep")}[pol.require_validity]
    bad = [k for k in need if fv.get(k) is False]
    if bad:
        out.append(
            _t(
                "T4",
                r0,
                f"the baseline model fails {' and '.join(b.upper() for b in bad)} validity",
                "rebaseline",
                f"require_validity={pol.require_validity}",
            )
        )
    if len(rep):
        from ._mvform import design_rows

        cov = assess_coverage(
            model, design_rows(rep, model), policy=extrapolation or ExtrapolationPolicy()
        )
        if cov.tier == "severe":
            out.append(
                _t(
                    "T4",
                    r0,
                    f"the reporting period is a severe extrapolation ({cov.reason})",
                    "rebaseline",
                    "#20 per-point coverage",
                    evidence={"share_points_outside": cov.share_points_outside},
                )
            )

    # T5 -- the achievement period (SEP 2019 Ed. 2 §4.2: at most 36 months after the baseline)
    months = _months_between(b1, r1)
    if months > pol.max_achievement_months:
        at = b1 + pd.DateOffset(months=pol.max_achievement_months)
        out.append(
            _t(
                "T5",
                at,
                f"{months:.0f} months since the baseline ended (max {pol.max_achievement_months})",
                "rebaseline",
                "SEP 2019 Ed. 2 §4.2",
            )
        )

    # T6 -- a new ECM (after the reporting period began) with >= 12 months of post-ECM data
    for ecm in sched.ecm_dates:
        ed = _day(ecm)
        if ed <= r0 + pd.Timedelta(days=sched.settle_days):
            continue  # the measure(s) the reporting period measures
        if (last - ed).days >= pol.ecm_gap_days:
            out.append(
                _t(
                    "T6",
                    ed,
                    f"ECM of {ed.date()} has {(last - ed).days} days of post-ECM data",
                    "rebaseline",
                    "BPA 2024 §3.1.8",
                )
            )

    # SEP chain: nothing dated inside the intermediate period can be adjusted
    if intermediate_period is not None:
        i0, i1 = _day(intermediate_period[0]), _day(intermediate_period[1])
        for tr in out:
            if i0 <= _day(tr.date) <= i1 and tr.outcome != "rebaseline":
                tr.outcome = "rebaseline"
                tr.detail += (
                    f"; dated inside the SEP chain's intermediate period {i0.date()}..{i1.date()}, "
                    "whose model both links share: an NRA cannot restate one link -- choose "
                    "another intermediate window, or rebaseline"
                )

    # resolution: a later version, or an accepted ledger entry for an NRA-class trigger
    for tr in out:
        if next_version_start is not None and _day(tr.date) < _day(next_version_start):
            tr.resolved, tr.resolved_by = (
                True,
                f"rebaselined (a later version starts {_ds(next_version_start)})",
            )
            continue
        if tr.outcome == "nra_static":
            hit = _ledger_near(
                ledger, tr.date, sched.settle_days, static_factor=tr.evidence.get("factor")
            )
        elif tr.outcome.startswith("nra_"):
            hit = _ledger_near(ledger, tr.date, sched.settle_days)
        else:
            hit = None
        if hit is not None:
            tr.resolved = True
            tr.resolved_by = f"ledger: {hit.method} {getattr(hit, 'factor', '') or ''}".strip() + (
                f" from {hit.start} ({hit.reason})"
            )
    out.sort(key=lambda t: (t.date, t.id))
    if step_caveat:
        for tr in out:
            tr.evidence.setdefault("caveat", step_caveat)
    return out


def event_phrase(tr: Trigger) -> str:
    """How a decline names a trigger: a non-routine event (T1-T3) or the trigger itself."""
    if tr.id in ("T1", "T2", "T3"):
        return f"unresolved non-routine event on {tr.date} ({tr.key})"
    return f"unresolved trigger {tr.key} ({tr.title})"


def window_anchor(triggers, rebaseline_triggers) -> Trigger:
    """The trigger a new baseline window must start after.

    The latest of the unresolved rebaseline-class triggers **and** of every other blocking trigger
    dated after the first of them -- even one an NRA on the old version resolves -- so the new
    baseline never straddles a known step. Triggers a later version already resolved are ignored.
    """
    rb = list(rebaseline_triggers)
    first = min(t.date for t in rb)
    later = [
        t
        for t in triggers
        if t.blocks
        and t.date >= first
        and not (t.resolved and str(t.resolved_by or "").startswith("rebaselined"))
    ]
    return max(rb + later, key=lambda t: (t.date, t.id))


def first_block(triggers) -> Trigger | None:
    """The earliest unresolved trigger that declines savings from its date on, if any."""
    live = [t for t in triggers if t.blocks and not t.resolved]
    return min(live, key=lambda t: t.date) if live else None


# --------------------------------------------------------------------------- the new window


@dataclass
class BaselineWindow:
    """A candidate new-baseline window, or why there is none yet (see the module docstring)."""

    ok: bool
    trigger_date: str
    earliest_start: str
    window: list | None = None
    n_days: int | None = None
    missing_frac: float | None = None
    model_kind: str | None = None
    model: dict | None = None
    fit_stats: dict | None = None
    regression_tests: dict | None = None
    sep_validity: dict | None = None
    valid: bool | None = None
    coverage_tier: str | None = None
    fit_frame_sha256: str | None = None
    days_needed: int | None = None
    declined_reason: str | None = None
    reasons: list = field(default_factory=list)
    _fit: object = field(default=None, repr=False, compare=False)

    def as_dict(self) -> dict:
        """Return as a plain JSON-safe dict (the fitted objects stay out)."""
        d = asdict(self)
        d.pop("_fit", None)
        return _json_safe(d)


def _install_windows(sched: EcmSchedule) -> list:
    s = pd.Timedelta(days=sched.settle_days)
    return [(_day(e) - s, _day(e) + s) for e in sched.ecm_dates]


def _overlaps(a0, a1, wins) -> tuple | None:
    for w0, w1 in wins:
        if not (a1 < w0 or a0 > w1):
            return (w0, w1)
    return None


def _fit_window(sub: pd.DataFrame, pol: RebaselinePolicy, expected, extrapolation):
    from . import _mvform
    from .coverage import ExtrapolationPolicy, assess_coverage
    from .stats import (
        cv_rmse_max_for,
        fit_stats,
        logical_signs,
        model_regression_tests,
        sep_validity,
    )

    y = sub["energy"].to_numpy(float)
    model = _mvform.fit(sub)  # the entry's form: the frame carries its driver columns
    if model is None:
        raise ValueError("no change-point model could be fitted")
    T = _mvform.design_rows(sub, model)
    st = fit_stats(
        y,
        model.predict(T),
        _mvform.n_params(model),
        cv_rmse_max=cv_rmse_max_for("daily"),
        time_index=sub.index,
    )
    tests = model_regression_tests(model, T, y, time_index=sub.index)
    vd = sep_validity(tests, signs=logical_signs(model))
    need = {"g14": (st.accept,), "sep": (vd.sep_valid,), "both": (st.accept, vd.sep_valid)}
    valid = all(need[pol.require_validity])
    exp = np.asarray(expected, dtype=float) if expected is not None else T
    ok = np.isfinite(exp) if exp.ndim == 1 else np.all(np.isfinite(exp), axis=1)
    cov = assess_coverage(model, exp[ok], policy=extrapolation or ExtrapolationPolicy())
    return model, st, tests, vd, valid, cov


def new_baseline_window(
    daily: pd.DataFrame,
    *,
    after,
    policy: RebaselinePolicy | None = None,
    as_of=None,
    expected=None,
    extrapolation=None,
    max_fits: int = 12,
    event: str | None = None,
) -> BaselineWindow:
    """The latest valid 12-month window for a new baseline after a trigger dated ``after``.

    Rules (issue #21 §2.6): ``min_baseline_days`` consecutive days, starting at least
    ``settle_days`` after the trigger, overlapping no ECM installation window (ECM date ±
    ``settle_days``), missing at most ``max_missing_frac`` of their days, fitting a model valid
    under ``require_validity``, and covering ``expected`` (the next reporting period's expected
    driver values; default: every driver value in ``daily``, the building's observed climate) at a
    #20 tier short of ``severe``. The latest qualifying window wins; up to ``max_fits`` candidate
    windows are fitted, stepping back 30 days after a failed fit.

    Otherwise the result is declined: ``days_needed`` counts the days still to collect before the
    earliest possible window completes (``declined_reason`` = "unresolved non-routine event on
    DATE; rebaseline needs N more days"; ``event`` replaces the phrase before the semicolon, as
    :func:`event_phrase` words it for a trigger), or ``reasons`` say why the windows available
    failed.
    """
    pol = policy or RebaselinePolicy()
    sched = pol.schedule
    span = pd.Timedelta(days=pol.min_baseline_days - 1)
    trig = _day(after)
    idx = pd.DatetimeIndex(daily.index).normalize()
    last = (
        min(_day(as_of), _day(idx.max()))
        if as_of is not None and len(idx)
        else (_day(idx.max()) if len(idx) else trig)
    )
    installs = _install_windows(sched)
    earliest = trig + pd.Timedelta(days=sched.settle_days)
    for _ in range(len(installs) + 1):  # push the earliest window past install windows
        hit = _overlaps(earliest, earliest + span, installs)
        if hit is None:
            break
        earliest = hit[1] + pd.Timedelta(days=1)
    out = BaselineWindow(ok=False, trigger_date=_ds(trig), earliest_start=_ds(earliest))
    what = event or f"unresolved non-routine event on {trig.date()}"
    if earliest + span > last:
        need = int((earliest + span - last).days)
        out.days_needed = need
        out.declined_reason = (
            f"{what}; rebaseline needs {need} more days (a {pol.min_baseline_days}-day window "
            f"starting {earliest.date()} or later, {sched.settle_days} settle days after it)"
        )
        return out
    days = np.asarray(idx.values, dtype="datetime64[D]")
    fits = 0
    end = last
    tried: list = []
    while end - span >= earliest and fits < max_fits:
        start = end - span
        hit = _overlaps(start, end, installs)
        if hit is not None:
            end = hit[0] - pd.Timedelta(days=1)
            continue
        lo = np.searchsorted(days, np.datetime64(start.date(), "D"), side="left")
        hi = np.searchsorted(days, np.datetime64(end.date(), "D"), side="right")
        n = int(hi - lo)
        miss = 1.0 - n / float(pol.min_baseline_days)
        if miss > pol.max_missing_frac:
            end = end - pd.Timedelta(days=1)
            continue
        fits += 1
        sub = daily.iloc[lo:hi]
        try:
            model, st, tests, vd, valid, cov = _fit_window(sub, pol, expected, extrapolation)
        except (ValueError, np.linalg.LinAlgError) as e:
            tried.append(f"{start.date()}..{end.date()}: {e}")
            end = end - pd.Timedelta(days=30)
            continue
        why = []
        if not valid:
            why.append(f"model not valid under {pol.require_validity}")
        if cov.tier == "severe":
            why.append("does not cover the expected conditions (severe)")
        if why:
            tried.append(f"{start.date()}..{end.date()}: " + "; ".join(why))
            end = end - pd.Timedelta(days=30)
            continue
        out.ok = True
        out.window = [_ds(start), _ds(end)]
        out.n_days = n
        out.missing_frac = round(max(miss, 0.0), 4)
        out.model_kind = getattr(model, "kind", type(model).__name__)
        out.model = model.as_dict()
        out.fit_stats = _json_safe(st.as_dict())
        out.regression_tests = _json_safe(tests.as_dict())
        out.sep_validity = _json_safe(vd.as_dict())
        out.valid = True
        out.coverage_tier = cov.tier
        out.fit_frame_sha256 = fit_frame_sha256(sub)
        out.reasons = tried
        out._fit = (model, st, tests, vd, sub)
        return out
    out.reasons = tried or [
        f"no {pol.min_baseline_days}-day window from {earliest.date()} to {last.date()} has at "
        f"most {pol.max_missing_frac:.0%} missing days outside the ECM installation windows"
    ]
    out.declined_reason = f"{what}; no valid rebaseline window yet: " + "; ".join(out.reasons[:3])
    return out


# --------------------------------------------------------------------------- the proposal


@dataclass
class RebaselineProposal:
    """What :func:`propose_rebaseline` proposes -- **never applied by itself**.

    ``outcome`` is ``"none"`` (no unresolved trigger), ``"nra"`` (only NRA-class triggers: the
    ``nra_specs`` are ledger templates for ``camber mv adjust``), ``"rebaseline"`` (a valid new
    window, ``window``) or ``"declined"`` (a rebaseline is needed and no window qualifies yet:
    ``declined_reason`` / ``days_needed``).
    """

    outcome: str
    as_of: str
    triggers: list
    window: BaselineWindow | None = None
    nra_specs: list = field(default_factory=list)
    declined_reason: str | None = None
    days_needed: int | None = None
    caveats: list = field(default_factory=list)

    def as_dict(self) -> dict:
        """Return as a plain JSON-safe dict."""
        return _json_safe(
            {
                "outcome": self.outcome,
                "as_of": self.as_of,
                "triggers": [t.as_dict() for t in self.triggers],
                "window": None if self.window is None else self.window.as_dict(),
                "nra_specs": list(self.nra_specs),
                "declined_reason": self.declined_reason,
                "days_needed": self.days_needed,
                "caveats": list(self.caveats),
            }
        )


_PROPOSAL_NOTE = (
    "a proposal: nothing is written. Moving the baseline is `camber mv rebaseline` (attributed, "
    "audited, dry-run by default); recording an NRA is `camber mv adjust`"
)


def _nra_spec(tr: Trigger) -> dict:
    if tr.outcome == "nra_static":
        return {
            "kind": "static",
            "method": "proportional",
            "factor": tr.evidence.get("factor"),
            "start": tr.date,
            "reason": tr.detail,
            "baseline_value": tr.evidence.get("baseline_value"),
            "reporting_value": tr.evidence.get("value"),
            "affected_share": None,
            "note": "state affected_share explicitly (no default, #21 decision D8)",
        }
    if tr.outcome == "nra_engineering":
        return {
            "kind": "nra",
            "method": "engineering",
            "start": tr.date,
            "reason": tr.detail,
            "amount": None,
            "se": None,
            "evidence": None,
            "note": "an engineering estimate with its standard error and evidence",
        }
    return {"kind": "nra", "method": "indicator", "start": tr.date, "reason": tr.detail}


def propose_rebaseline(
    daily: pd.DataFrame,
    model,
    *,
    baseline,
    policy: RebaselinePolicy | None = None,
    as_of=None,
    reporting=None,
    fit_valid: dict | None = None,
    events=(),
    static_factors=(),
    ledger=(),
    next_version_start=None,
    intermediate_period=None,
    expected=None,
    extrapolation=None,
) -> RebaselineProposal:
    """Assess the triggers and propose an outcome in BPA's taxonomy (:class:`RebaselineProposal`).

    Arguments are those of :func:`assess_triggers` plus ``expected`` for
    :func:`new_baseline_window`. The new window starts after :func:`window_anchor`: the latest
    unresolved rebaseline-class trigger, or a later blocking trigger, so it never straddles a known
    step. Read-only.
    """
    pol = policy or RebaselinePolicy()
    trig = assess_triggers(
        daily,
        model,
        baseline=baseline,
        policy=pol,
        as_of=as_of,
        reporting=reporting,
        fit_valid=fit_valid,
        events=events,
        static_factors=static_factors,
        ledger=ledger,
        next_version_start=next_version_start,
        intermediate_period=intermediate_period,
        extrapolation=extrapolation,
    )
    last = _ds(as_of) if as_of is not None else (_ds(daily.index.max()) if len(daily) else "")
    caveats = [_PROPOSAL_NOTE]
    live = [t for t in trig if not t.resolved]
    rb = [t for t in live if t.outcome == "rebaseline"]
    nra = [t for t in live if t.outcome.startswith("nra_")]
    specs = [_nra_spec(t) for t in nra]
    if any(t.id == "T6" for t in rb) and len(rb) == sum(t.id == "T6" for t in rb):
        caveats.append(
            "T6 is advisory: savings are not declined, but BPA 2024 §3.1.8 rebaselines once a "
            "new ECM has 12 months of post-ECM data"
        )
    if not rb:
        return RebaselineProposal(
            "nra" if nra else "none", last, trig, nra_specs=specs, caveats=caveats
        )
    anchor = window_anchor(trig, rb)
    win = new_baseline_window(
        daily,
        event=event_phrase(anchor),
        after=anchor.date,
        policy=pol,
        as_of=as_of,
        expected=expected,
        extrapolation=extrapolation,
    )
    if nra:
        caveats.append(
            f"{len(nra)} NRA-class trigger(s) before the rebaseline are absorbed by it if the new "
            "window starts after them; record them with `camber mv adjust` otherwise"
        )
    if not win.ok:
        return RebaselineProposal(
            "declined",
            last,
            trig,
            window=win,
            nra_specs=specs,
            declined_reason=win.declined_reason,
            days_needed=win.days_needed,
            caveats=caveats,
        )
    return RebaselineProposal(
        "rebaseline", last, trig, window=win, nra_specs=specs, caveats=caveats
    )


# --------------------------------------------------------------------------- chaining versions


def version_segments(versions, *, end) -> list:
    """The reporting segment of each version, for a chain across versions.

    ``versions`` are :class:`BaselineRecord` s oldest first (:meth:`MVBaselineStore.versions`).
    Version *k* reports from the day after its window ends until the day before the next
    version's trigger (``provenance["trigger_date"]``, else its window start); the last runs to
    ``end``. Between a trigger and the end of the next window nothing is reported (the ``gap``):
    the old baseline no longer holds and the new one is being fitted there. Returns one dict per
    version: ``version``, ``model_window``, ``period`` (``None`` when empty), ``gap_after``,
    ``trigger_date`` / ``trigger_ids`` of the *next* version.
    """
    out = []
    vs = list(versions)
    stop = _day(end)
    for k, rec in enumerate(vs):
        s = _day(rec.period_end) + pd.Timedelta(days=1)
        nxt = vs[k + 1] if k + 1 < len(vs) else None
        if nxt is not None:
            tdate = (nxt.provenance or {}).get("trigger_date") or nxt.period_start
            e = min(_day(tdate), _day(nxt.period_start)) - pd.Timedelta(days=1)
            gap = [_ds(e + pd.Timedelta(days=1)), _ds(nxt.period_end)]
        else:
            e, gap, tdate = stop, None, None
        e = min(e, stop)
        out.append(
            {
                "version": version_label(rec),
                "model_window": [rec.period_start, rec.period_end],
                "period": [_ds(s), _ds(e)] if e >= s else None,
                "gap_after": gap,
                "next_trigger_date": None if nxt is None else _ds(tdate),
                "next_trigger_ids": []
                if nxt is None
                else list((nxt.provenance or {}).get("trigger_ids") or []),
            }
        )
    return out
