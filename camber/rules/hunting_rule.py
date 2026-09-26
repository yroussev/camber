"""Rule: control hunting / oscillation on a modulating output.

A well-tuned loop settles; a badly-tuned one **hunts** — the valve or damper reverses direction
again and again, never holding. Hunting wastes actuator life, upsets the controlled variable, and
often drives simultaneous heating/cooling downstream. This detects it directly from a modulating
output by counting *direction reversals per hour* beyond a deadband (so slow, legitimate modulation
doesn't trip it). Works on any present modulating role. numpy/pandas; a synthetic fixture proves it.

Three things the rate has to get right, each of which bit on real trend data:

* **Units.** The role pipeline scales every position role to 0-100 %, so the deadband is in
  **percent** (default 5 %); a 0-1 fraction signal is rescaled to percent first
  (:func:`camber.units.normalize_percent`), so the same default works on either.
* **Gaps are not calm time.** The rate divides by the *observed* time -- the sum of the
  sample-to-sample steps no longer than ``gap_factor`` x the median interval -- and a reversal is
  only counted between increments that are both inside one contiguous run. A trend with a
  three-month logger outage no longer reads as three months of stable control.
* **Resolution.** A trend sampled every ``dt`` minutes can show at most one reversal per sample,
  i.e. ``60 / dt`` per hour (4/hr at 15-min data). When that ceiling sits below the warn
  threshold the rule cannot tell a hunting loop from a stable one, so it **declines** (``info`` +
  caveat, ``None`` rate) rather than reporting "stable"; when only the fault threshold is out of
  reach the verdict is capped at ``warn`` and caveated.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..model.roles import Role
from ..units import normalize_percent
from .base import Finding

_MODULATING = (Role.HEAT_VALVE, Role.COOL_VALVE, Role.DAMPER, Role.OA_DAMPER)


def _contiguous(s: pd.Series, gap_factor: float):
    """Sorted, de-duplicated series + a per-step "same run" mask + the observed hours.

    A step (sample ``i-1`` -> ``i``) is *contiguous* when it is no longer than ``gap_factor`` x the
    median positive sample spacing; longer steps are data gaps. Observed hours = the summed length
    of the contiguous steps.
    """
    s = s[~s.index.duplicated(keep="last")].sort_index()
    idx = pd.DatetimeIndex(s.index)
    steps = np.diff(idx.asi8) / 1e9  # seconds
    pos = steps[steps > 0]
    if pos.size == 0:
        return s, np.zeros(0, dtype=bool), 0.0, None
    median = float(np.median(pos))
    ok = (steps > 0) & (steps <= gap_factor * median)
    return s, ok, float(steps[ok].sum()) / 3600.0, median


def reversals_per_hour(signal, deadband: float, *, gap_factor: float = 3.0):
    """Direction reversals per *observed* hour of a modulating signal, ignoring moves within
    ``deadband`` (in the signal's own units).

    Returns ``(rate_per_hour, n_reversals)``. A reversal is a sign change between consecutive
    significant (> deadband) increments within one contiguous run of samples -- the signature of a
    loop that can't hold a position. Steps longer than ``gap_factor`` x the median interval are
    data gaps: they break the run and their duration is not counted as observed (calm) time.
    """
    s = pd.Series(signal).dropna()
    if len(s) < 3:
        return 0.0, 0
    s, ok, hours, _ = _contiguous(s, gap_factor)
    if len(s) < 3:
        return 0.0, 0
    d = np.diff(s.to_numpy(dtype=float))
    # a run id per step: a gap step starts a new run and is itself not an increment we trust
    run = np.cumsum(~ok)
    sig = ok & (np.abs(d) > deadband)
    signs, runs = np.sign(d[sig]), run[sig]
    if signs.size < 2:
        return 0.0, 0
    reversals = int(np.sum((signs[1:] != signs[:-1]) & (runs[1:] == runs[:-1])))
    return (reversals / hours if hours > 0 else 0.0), reversals


def max_resolvable_per_hour(signal, *, gap_factor: float = 3.0) -> float | None:
    """The highest reversal rate a trend's sampling can show: one per sample, ``3600 / dt``/hr.

    ``dt`` is the median positive sample spacing; ``None`` when it can't be determined.
    """
    s = pd.Series(signal).dropna()
    if len(s) < 2:
        return None
    _, _, _, median = _contiguous(s, gap_factor)
    return None if not median else 3600.0 / median


class ControlHunting:
    """Flags a modulating output that reverses direction excessively (unstable/hunting loop).

    ``deadband`` is in percent of stroke (a 0-1 signal is rescaled to percent first).
    """

    name = "control_hunting"
    roles_required = ()
    roles_optional = _MODULATING

    def __init__(
        self,
        *,
        warn_per_hr: float = 6.0,
        fault_per_hr: float = 12.0,
        deadband: float = 5.0,
        gap_factor: float = 3.0,
    ):
        self.warn_per_hr = warn_per_hr
        self.fault_per_hr = fault_per_hr
        self.deadband = deadband
        self.gap_factor = gap_factor

    def _signals(self, frame):
        return [r for r in _MODULATING if r in frame.columns and frame[r].notna().sum() >= 3]

    def _worst(self, frame):
        worst_role, worst_rate, worst_rev, rates, ceilings = None, 0.0, 0, {}, {}
        for role in self._signals(frame):
            sig = normalize_percent(frame[role])
            rate, rev = reversals_per_hour(sig, self.deadband, gap_factor=self.gap_factor)
            key = getattr(role, "value", str(role))
            rates[key] = round(rate, 2)
            ceilings[key] = max_resolvable_per_hour(sig, gap_factor=self.gap_factor)
            if worst_role is None or rate > worst_rate:
                worst_role, worst_rate, worst_rev = role, rate, rev
        return worst_role, worst_rate, worst_rev, rates, ceilings

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run over an equipment role-frame; return a Finding on the worst-hunting output."""
        if not self._signals(frame):
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: no modulating output present",
            )
        role, rate, rev, rates, ceilings = self._worst(frame)
        rn = getattr(role, "value", str(role)) if role is not None else ""
        known = [c for c in ceilings.values() if c is not None]
        ceiling = min(known) if known else None
        metrics = {
            "worst_signal": rn,
            "reversals_per_hr": round(rate, 2),
            "reversals": rev,
            "per_signal_rate": rates,
            "deadband": self.deadband,
            "max_resolvable_per_hr": None if ceiling is None else round(ceiling, 2),
        }
        caveats: list = []
        if ceiling is None or ceiling < self.warn_per_hr:
            # The sampling can't show a rate as high as the warn threshold: "stable" would be an
            # untested negative. Decline, but say how close to the ceiling the signal ran.
            share = rate / ceiling if ceiling else None
            metrics["reversals_per_hr"] = None
            metrics["per_signal_rate"] = {k: None for k in rates}
            metrics["reversal_share_of_max"] = None if share is None else round(share, 2)
            dt_min = None if ceiling is None else 60.0 / ceiling
            caveats.append(
                "hunting not evaluated: the trend's sampling "
                + (f"(~{dt_min:.0f}-min) " if dt_min is not None else "")
                + f"can show at most {0 if ceiling is None else ceiling:.1f} reversals/hr, below "
                f"the {self.warn_per_hr:g}/hr warn threshold -- a hunting loop is aliased away; "
                "re-trend at 1-5 min to judge"
                + (
                    f" (observed reversals on {share:.0%} of the resolvable maximum)"
                    if share is not None
                    else ""
                )
            )
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics=metrics,
                summary=f"{equip}: hunting not evaluated (sampling too coarse to resolve it)",
                caveats=caveats,
            )
        sev = "fault" if rate >= self.fault_per_hr else "warn" if rate >= self.warn_per_hr else "ok"
        if ceiling < self.fault_per_hr:
            caveats.append(
                f"sampling can show at most {ceiling:.1f} reversals/hr, below the "
                f"{self.fault_per_hr:g}/hr fault threshold: severity capped at warn"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics=metrics,
            summary=(
                f"{equip}: {rn} reverses {rate:.1f}x/hr (hunting)"
                if sev != "ok"
                else f"{equip}: modulating outputs stable ({rate:.1f}x/hr)"
            ),
            caveats=caveats,
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: the worst-hunting signal's trend (the oscillation is the evidence)."""
        from ..charts.evidence import Evidence

        role = self._worst(frame)[0] if self._signals(frame) else None
        if role is None:
            return None
        return Evidence(
            renderer="multitrend",
            roles=[role],
            title=f"{equip}: {getattr(role, 'value', role)} hunting",
        )
