"""The new-baseline window search for bills (provisional; 0.95, issue #74).

:func:`camber.mandv.rebaseline.new_baseline_window` proposes a rebaseline window of days. A
billing meter's rows are bills, so its window is a run of **whole bills** instead, found by
:func:`new_bill_window` under the same rules and ranked the same way:

* **Candidates cover a full service year.** For each bill, from the latest backwards, the window
  that ends with it and starts at the latest bill still giving at least ``min_baseline_days`` (365)
  of service: the shortest run of whole bills covering a year. A window's dates are its first
  bill's start and its last bill's last day, so no bill straddles an edge.
* **The rules** of the daily search: the window starts at least ``settle_days`` after the trigger
  it answers; it overlaps no ECM installation window (ECM date +- ``settle_days``) and contains no
  declared event (the event log, +- ``settle_days``); it holds at least ``min_bills`` bills; the
  days its bills do not serve (gaps between bills, and bills dropped for too little temperature
  coverage) are at most ``max_missing_frac`` of its span -- the rule
  :func:`camber.mvrun.baseline_window_check` applies to every billing freeze; its model is valid
  under ``require_validity``; and it covers the conditions seen (#20 tier not ``severe``, on every
  bill's rows at the new model's bases).
* **Ranking**, as the daily path: the **latest** qualifying window wins. At most ``max_fits``
  candidates are fitted, and after a failed fit the search steps back one bill (the daily search
  steps back 30 days, about one bill).

The fit itself is the caller's (``fit``): for a config entry it is
:func:`camber.mvbilling.billing_fit_version`, so ``base_f: "auto"`` bases are selected on each
candidate window exactly as ``camber mv rebaseline --period`` would select them.

Every name here is provisional (docs/API-STABILITY.md).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .rebaseline import (
    BaselineWindow,
    RebaselinePolicy,
    _day,
    _ds,
    _install_windows,
    _json_safe,
    _overlaps,
    fit_frame_sha256,
)

__all__ = ["BillWindow", "RANKING", "new_bill_window"]

#: How the candidates are ranked, as a proposal states it.
RANKING = (
    "windows of whole bills covering at least a full service year, ending at each bill from the "
    "latest backwards; the latest window that meets every rule wins, as in the daily search"
)


@dataclass
class BillWindow(BaselineWindow):
    """A :class:`~camber.mandv.rebaseline.BaselineWindow` of whole bills: plus ``n_bills``, the
    ``bases`` the window's model is at (``None`` for a change-point model at the entry's base) and
    the ``ranking`` rule."""

    n_bills: int | None = None
    bases: dict | None = None
    ranking: str = RANKING
    tried: int = 0


def _bases_of(billing: dict | None) -> dict | None:
    if not billing:
        return None
    keys = ("base_f", "heating_base_f", "cooling_base_f", "dd_kind", "bases_fitted", "selected")
    return {k: billing[k] for k in keys if k in billing}


def new_bill_window(
    frame: pd.DataFrame,
    *,
    after,
    fit,
    policy: RebaselinePolicy | None = None,
    as_of=None,
    min_bills: int = 9,
    events=(),
    max_fits: int = 12,
    event: str | None = None,
) -> BillWindow:
    """The latest valid window of whole bills for a new baseline after a trigger dated ``after``.

    ``frame`` is the meter's bills frame (``start``, exclusive ``end``, ``days``; one row per
    usable bill). ``fit(period)`` fits a baseline on ``[first day, last day]`` and returns
    :func:`camber.mvbilling.billing_fit_version`'s dict (``model``, ``st``, ``tests``, ``sep``,
    ``sub``, ``frame``, ``billing``), raising ``ValueError`` when it cannot. ``events`` are the
    declared changes (:class:`~camber.mandv.rebaseline.DeclaredChange`); ``event`` words the
    trigger in a decline, as :func:`~camber.mandv.rebaseline.event_phrase` does. See the module
    docstring for the rules; a decline says how many days are still needed, or why the windows
    available failed.
    """
    from . import _mvform
    from .coverage import assess_coverage

    pol = policy or RebaselinePolicy()
    sched = pol.schedule
    settle = pd.Timedelta(days=sched.settle_days)
    trig = _day(after)
    f = frame.iloc[np.argsort(pd.DatetimeIndex(frame["start"]).asi8, kind="stable")]
    starts = pd.DatetimeIndex(f["start"]).normalize()
    ends = pd.DatetimeIndex(f["end"]).normalize()  # exclusive
    last_day = (ends.max() - pd.Timedelta(days=1)) if len(f) else trig
    last = min(_day(as_of), last_day) if as_of is not None else last_day
    installs = _install_windows(sched)
    # a declared event after the trigger may not fall inside the window (the trigger's own event,
    # and any before it, are behind the earliest start already)
    blocked = installs + [
        (_day(e.date) - settle, _day(e.date) + settle) for e in events or () if _day(e.date) > trig
    ]
    span = pd.Timedelta(days=pol.min_baseline_days - 1)
    earliest = trig + settle
    for _ in range(len(installs) + 1):  # push the earliest window past install windows
        hit = _overlaps(earliest, earliest + span, installs)
        if hit is None:
            break
        earliest = hit[1] + pd.Timedelta(days=1)
    out = BillWindow(ok=False, trigger_date=_ds(trig), earliest_start=_ds(earliest))
    what = event or f"unresolved non-routine event on {trig.date()}"
    if earliest + span > last:
        need = int((earliest + span - last).days)
        out.days_needed = need
        out.declined_reason = (
            f"{what}; rebaseline needs {need} more days (whole bills covering "
            f"{pol.min_baseline_days} days, starting {earliest.date()} or later, "
            f"{sched.settle_days} settle days after it)"
        )
        return out
    days = f["days"].to_numpy(float)
    csum = np.concatenate([[0.0], np.cumsum(days)])
    tried: list = []
    fits = 0
    j = int(np.searchsorted(ends.values, np.datetime64(last + pd.Timedelta(days=1)), "right")) - 1
    while j >= 0 and fits < max_fits:
        # the latest start giving a full service year of whole bills ending with bill j
        reach = ends[j] - pd.Timedelta(days=pol.min_baseline_days)
        i = int(np.searchsorted(starts.values, np.datetime64(reach), "right")) - 1
        if i < 0 or starts[i] < earliest:
            break  # every earlier window starts too early
        w0, w1 = starts[i], ends[j] - pd.Timedelta(days=1)
        label = f"{w0.date()}..{w1.date()}"
        hit = _overlaps(w0, w1, blocked)
        if hit is not None:
            j = int(np.searchsorted(ends.values, np.datetime64(hit[0]), "right")) - 1
            continue
        n_bills = j - i + 1
        n_days = int((w1 - w0).days) + 1
        served = float(csum[j + 1] - csum[i])
        miss = 1.0 - served / float(n_days)
        if n_bills < min_bills:
            tried.append(f"{label}: {n_bills} bills (< {min_bills})")
            j -= 1
            continue
        if miss > pol.max_missing_frac:
            tried.append(f"{label}: {miss:.0%} of its days unserved (> {pol.max_missing_frac:.0%})")
            j -= 1
            continue
        fits += 1
        try:
            res = fit([w0, w1])
        except (ValueError, np.linalg.LinAlgError) as e:
            tried.append(f"{label}: {e}")
            j -= 1
            continue
        st, vd = res["st"], res["sep"]
        verdict = {"g14": (st.accept,), "sep": (vd.sep_valid,), "both": (st.accept, vd.sep_valid)}
        why = []
        if not all(verdict[pol.require_validity]):
            why.append(f"model not valid under {pol.require_validity}")
        model = res["model"]
        cov = assess_coverage(model, _mvform.design_rows(res.get("frame", f), model))
        if cov.tier == "severe":
            why.append("does not cover the conditions seen (severe)")
        if why:
            tried.append(f"{label}: " + "; ".join(why))
            j -= 1
            continue
        out.ok = True
        out.window = [_ds(w0), _ds(w1)]
        out.n_days = int(served)
        out.n_bills = int(n_bills)
        out.missing_frac = round(max(miss, 0.0), 4)
        out.model_kind = getattr(model, "kind", type(model).__name__)
        out.model = model.as_dict()
        out.fit_stats = _json_safe(st.as_dict())
        out.regression_tests = _json_safe(res["tests"].as_dict())
        out.sep_validity = _json_safe(vd.as_dict())
        out.valid = True
        out.coverage_tier = cov.tier
        out.fit_frame_sha256 = fit_frame_sha256(res["sub"])
        out.bases = _bases_of(res.get("billing"))
        out.reasons = tried
        out.tried = fits
        out._fit = res
        return out
    out.tried = fits
    out.reasons = tried or [
        f"no run of whole bills covering {pol.min_baseline_days} days from {earliest.date()} to "
        f"{last.date()} avoids the ECM installation windows and declared events"
    ]
    out.declined_reason = f"{what}; no valid rebaseline window yet: " + "; ".join(out.reasons[:3])
    return out
