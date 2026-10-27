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
  i.e. ``60 / dt`` per hour (4/hr at 15-min data), and in practice a little less: ``n`` samples
  hold at most ``n - 2`` reversals in ``n - 1`` intervals, and a real oscillation sampled near
  that limit aliases to fewer. So a threshold counts as reachable only when the ceiling exceeds it
  by ``resolution_margin`` (default 1.25: at 10-min data the 6/hr ceiling *is* the warn threshold,
  which no trend can realistically show). When the warn threshold is out of reach the rule cannot
  tell a hunting loop from a stable one, so it **declines** (``info`` + caveat, ``None`` rate)
  rather than reporting "stable"; when only the fault threshold is out of reach the verdict is
  capped at ``warn`` and caveated (0.106, #182). The check is **per signal** (0.108, #236): a
  coarsely logged damper is not evaluated on its own, with a caveat naming it, and no longer hides
  a hunting valve sampled every minute; the rule declines only when no signal can be evaluated.
* **Fan-off time is not control time (0.106, #181).** With the fan stopped the loop is not
  controlling, and an hour of a shut valve would dilute the rate. Only steps between two fan-on
  samples count (fan status, else fan speed, else airflow -- :func:`camber.schedules.fan_on_mask`),
  so fan-off stretches are neither observed time nor reversals; a unit that trends no fan signal
  is judged ungated and the finding says so (``fan_gate`` metric and a caveat).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..model.roles import Role
from ..schedules import FAN_GATE_NONE, fan_on_mask
from ..units import normalize_percent
from .base import Finding

_MODULATING = (Role.HEAT_VALVE, Role.COOL_VALVE, Role.DAMPER, Role.OA_DAMPER)


def _contiguous(s: pd.Series, gap_factor: float, gate=None):
    """Sorted, de-duplicated series + a per-step "same run" mask + the observed hours.

    A step (sample ``i-1`` -> ``i``) is *contiguous* when it is no longer than ``gap_factor`` x the
    median positive sample spacing; longer steps are data gaps. With a ``gate`` (a boolean Series,
    e.g. fan-on) a step also needs both of its samples inside the gate: a step out of it breaks the
    run like a gap. Observed hours = the summed length of the contiguous steps. The median spacing
    is the trend's own, gate or not.
    """
    s = s[~s.index.duplicated(keep="last")].sort_index()
    idx = pd.DatetimeIndex(s.index)
    steps = np.diff(idx.asi8) / 1e9  # seconds
    pos = steps[steps > 0]
    if pos.size == 0:
        return s, np.zeros(0, dtype=bool), 0.0, None
    median = float(np.median(pos))
    ok = (steps > 0) & (steps <= gap_factor * median)
    if gate is not None:
        g = pd.Series(gate)
        g = g[~g.index.duplicated(keep="last")]
        on = g.reindex(s.index).fillna(False).astype(bool).to_numpy()
        ok &= on[1:] & on[:-1]
    return s, ok, float(steps[ok].sum()) / 3600.0, median


def reversals_per_hour(signal, deadband: float, *, gap_factor: float = 3.0, gate=None):
    """Direction reversals per *observed* hour of a modulating signal, ignoring moves within
    ``deadband`` (in the signal's own units).

    Returns ``(rate_per_hour, n_reversals)``. A reversal is a sign change between consecutive
    significant (> deadband) increments within one contiguous run of samples -- the signature of a
    loop that can't hold a position. Steps longer than ``gap_factor`` x the median interval are
    data gaps: they break the run and their duration is not counted as observed (calm) time. With
    a ``gate`` (boolean Series on the signal's index, e.g. fan-on) only steps between two gated
    samples count, so the time outside it is neither observed time nor reversals.
    """
    s = pd.Series(signal).dropna()
    if len(s) < 3:
        return 0.0, 0
    s, ok, hours, _ = _contiguous(s, gap_factor, gate)
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


def _reversal_mask(signal, deadband: float, *, gap_factor: float = 3.0, gate=None) -> pd.Series:
    """The samples at which :func:`reversals_per_hour` counts a reversal (0.111, #298).

    True at the first sample of each significant increment that reverses the previous one's
    direction within one contiguous run, so the count of True samples is the reversal count.
    """
    s = pd.Series(signal).dropna()
    out = pd.Series(False, index=s.index)
    if len(s) < 3:
        return out
    s, ok, _hours, _ = _contiguous(s, gap_factor, gate)
    out = pd.Series(False, index=s.index)
    if len(s) < 3:
        return out
    d = np.diff(s.to_numpy(dtype=float))
    run = np.cumsum(~ok)
    sig = ok & (np.abs(d) > deadband)
    steps = np.flatnonzero(sig)  # step i runs from sample i to sample i + 1
    if steps.size < 2:
        return out
    signs, runs = np.sign(d[steps]), run[steps]
    flips = steps[1:][(signs[1:] != signs[:-1]) & (runs[1:] == runs[:-1])]
    out.iloc[flips] = True
    return out


def _hunting_stretches(
    signal, flips: pd.Series, per_hr: float, *, gap_factor: float = 3.0, gate=None
):
    """The samples of the stretches in which ``signal`` hunts at ``per_hr`` or faster (0.111,
    #298): from one reversal (``flips``, :func:`_reversal_mask`) to the next within one contiguous
    run, when the two are no more than ``1 / per_hr`` hours apart."""
    s = pd.Series(signal).dropna()
    out = pd.Series(False, index=s.index)
    if len(s) < 3 or per_hr <= 0:
        return out
    s, ok, _hours, _ = _contiguous(s, gap_factor, gate)
    out = pd.Series(False, index=s.index)
    if len(s) < 3:
        return out
    run = np.concatenate(([0], np.cumsum(~ok)))  # a run id per sample
    at = np.flatnonzero(flips.reindex(s.index, fill_value=False).to_numpy(dtype=bool))
    t = s.index.asi8 / 1e9
    limit = 3600.0 / per_hr
    for a, b in zip(at[:-1], at[1:]):
        if run[a] == run[b] and t[b] - t[a] <= limit:
            out.iloc[a : b + 1] = True
    return out


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
    roles_optional = _MODULATING + (
        # fan-on gate: status, else speed, else airflow (see camber.schedules.fan_on_mask)
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
        Role.AIRFLOW,
    )

    def __init__(
        self,
        *,
        warn_per_hr: float = 6.0,
        fault_per_hr: float = 12.0,
        deadband: float = 5.0,
        gap_factor: float = 3.0,
        resolution_margin: float = 1.25,
        fan_gate: bool = True,
    ):
        if resolution_margin < 1.0:
            raise ValueError("resolution_margin must be at least 1")
        self.warn_per_hr = warn_per_hr
        self.fault_per_hr = fault_per_hr
        self.deadband = deadband
        self.gap_factor = gap_factor
        self.resolution_margin = resolution_margin
        self.fan_gate = fan_gate

    def _gate(self, frame: pd.DataFrame):
        """``(fan-on mask | None, label)``; ``(None, "off")`` when gating is disabled."""
        if not self.fan_gate:
            return None, "off"
        return fan_on_mask(frame)

    def _reachable(self, ceiling, threshold: float) -> bool:
        """Whether a trend whose ceiling is ``ceiling``/hr can realistically show ``threshold``."""
        return ceiling is not None and ceiling >= self.resolution_margin * threshold

    def _signals(self, frame):
        return [r for r in _MODULATING if r in frame.columns and frame[r].notna().sum() >= 3]

    def _per_signal(self, frame, gate=None) -> dict:
        """``{role: (rate, reversals, ceiling)}`` for every modulating signal present."""
        out = {}
        for role in self._signals(frame):
            sig = normalize_percent(frame[role])
            rate, rev = reversals_per_hour(
                sig, self.deadband, gap_factor=self.gap_factor, gate=gate
            )
            out[role] = (rate, rev, max_resolvable_per_hour(sig, gap_factor=self.gap_factor))
        return out

    def _worst(self, frame, gate=None, per=None):
        """The worst signal among those whose sampling can show the warn threshold (0.108, #236),
        else among all: ``(role, rate, reversals, ceiling, evaluable)``."""
        per = self._per_signal(frame, gate) if per is None else per
        evaluable = [r for r, v in per.items() if self._reachable(v[2], self.warn_per_hr)]
        pool = evaluable or list(per)
        worst = None
        for role in pool:
            if worst is None or per[role][0] > per[worst][0]:
                worst = role
        if worst is None:
            return None, 0.0, 0, None, evaluable
        rate, rev, ceiling = per[worst]
        return worst, rate, rev, ceiling, evaluable

    def _too_coarse(self, ceiling) -> str:
        """Where a ceiling sits against the warn threshold, for a not-evaluated caveat."""
        if ceiling is None or ceiling < self.warn_per_hr:
            return f"below the {self.warn_per_hr:g}/hr warn threshold"
        return (
            f"too close to the {self.warn_per_hr:g}/hr warn threshold to show it (a "
            f"threshold needs a ceiling {self.resolution_margin:g}x above it)"
        )

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run over an equipment role-frame; return a Finding on the worst-hunting output.

        The resolution check is per signal (0.108, #236): a signal whose sampling cannot show the
        warn threshold is not evaluated (its rate is ``None`` and a caveat names it), and the
        verdict is the worst of the signals that can be evaluated, judged against that signal's
        own ceiling. Only when no signal can be evaluated does the rule decline.
        """
        if not self._signals(frame):
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: no modulating output present",
            )
        gate, fan_src = self._gate(frame)
        per = self._per_signal(frame, gate)
        role, rate, rev, ceiling, evaluable = self._worst(frame, gate, per)
        rates = {getattr(r, "value", str(r)): round(v[0], 2) for r, v in per.items()}
        rn = getattr(role, "value", str(role)) if role is not None else ""
        declined = [r for r in per if r not in evaluable]
        missing = [r.value for r in _MODULATING if r not in frame.columns]
        if fan_src == FAN_GATE_NONE:
            missing.append(Role.SUPPLY_FAN_STATUS.value)
        metrics = {
            "worst_signal": rn,
            "reversals_per_hr": round(rate, 2),
            "reversals": rev,
            "per_signal_rate": rates,
            "deadband": self.deadband,
            "max_resolvable_per_hr": None if ceiling is None else round(ceiling, 2),
            "fan_gate": fan_src,
            "_missing_optional": missing,
        }
        if gate is not None:
            on = gate.reindex(frame.index).fillna(False).astype(bool)
            metrics["fan_on_hours"] = round(self._fan_on_hours(frame, on), 2)
        caveats: list = []
        if fan_src == FAN_GATE_NONE:
            caveats.append(
                "not fan-gated (no fan signal trended): fan-off hours, when the loop is not "
                "controlling, count as calm time and dilute the rate -- map SUPPLY_FAN_STATUS"
            )
        margin = self.resolution_margin
        if not evaluable:
            # No signal's sampling can realistically show a rate as high as the warn threshold:
            # "stable" would be an untested negative. Decline, but say how close to its own
            # ceiling the signal ran.
            share = rate / ceiling if ceiling else None
            metrics["reversals_per_hr"] = None
            metrics["per_signal_rate"] = {k: None for k in rates}
            metrics["reversal_share_of_max"] = None if share is None else round(share, 2)
            dt_min = None if ceiling is None else 60.0 / ceiling
            caveats.append(
                "hunting not evaluated: the trend's sampling "
                + (f"(~{dt_min:.0f}-min) " if dt_min is not None else "")
                + f"can show at most {0 if ceiling is None else ceiling:.1f} reversals/hr, "
                + self._too_coarse(ceiling)
                + " -- a hunting loop is aliased away; re-trend at 1-5 min to judge"
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
        if declined:
            # 0.108 (#236): a coarsely logged signal is not evaluated on its own; it no longer
            # hides hunting on a finely sampled one
            names = [getattr(r, "value", str(r)) for r in declined]
            metrics["not_evaluated_signals"] = names
            for r, k in zip(declined, names):
                metrics["per_signal_rate"][k] = None
                c = per[r][2]
                dt = "" if c is None else f" (~{60.0 / c:.0f}-min, at most {c:.1f} reversals/hr)"
                caveats.append(
                    f"{k} not evaluated for hunting: its sampling{dt} is "
                    + self._too_coarse(c)
                    + " -- re-trend it at 1-5 min to judge it"
                )
        if gate is not None and metrics["fan_on_hours"] == 0:
            metrics["reversals_per_hr"] = None
            metrics["per_signal_rate"] = {k: None for k in rates}
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics=metrics,
                summary=f"{equip}: hunting not evaluated (the fan never ran)",
                caveats=caveats,
            )
        sev = "fault" if rate >= self.fault_per_hr else "warn" if rate >= self.warn_per_hr else "ok"
        fault_reachable = self._reachable(ceiling, self.fault_per_hr)
        if not fault_reachable and ceiling is not None:  # ceiling is known: warn was reachable
            sev = "warn" if sev == "fault" else sev
            thr = f"the {self.fault_per_hr:g}/hr fault threshold"
            where = (
                f"below {thr}"
                if ceiling < self.fault_per_hr
                else f"too close to {thr} to show it (a threshold needs a ceiling {margin:g}x "
                "above it)"
            )
            reach = f"sampling can show at most {ceiling:.1f} reversals/hr, {where}"
            if sev == "warn":
                caveats.append(f"{reach}: severity capped at warn")
            else:
                caveats.append(
                    f"{reach}: only warn-level hunting ({self.warn_per_hr:g}/hr) was testable"
                )
        metrics["fault_reachable"] = fault_reachable
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

    def _fan_on_hours(self, frame: pd.DataFrame, on: pd.Series) -> float:
        """Fan-on observed hours on the modulating signals' sampling (steps between two fan-on
        samples, gaps excluded) -- the denominator the rate is judged over."""
        sigs = self._signals(frame)
        if not sigs:
            return 0.0
        idx = frame[sigs].dropna(how="all").index
        _, _, hours, _ = _contiguous(pd.Series(0.0, index=idx), self.gap_factor, on)
        return hours

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: the worst-hunting signal's trend (the oscillation is the evidence)."""
        from ..charts.evidence import Evidence

        gate = self._gate(frame)[0]
        role = self._worst(frame, gate)[0] if self._signals(frame) else None
        if role is None:
            return None
        # 0.111 (#298): each reversal the rate counts is an event; the hours are the stretches
        # in which the worst signal reverses at the warn rate or faster
        sig = normalize_percent(frame[role])
        flips = _reversal_mask(sig, self.deadband, gap_factor=self.gap_factor, gate=gate)
        hunting = _hunting_stretches(
            sig, flips, self.warn_per_hr, gap_factor=self.gap_factor, gate=gate
        )
        judged = sig.notna()
        if gate is not None:
            judged &= pd.Series(gate).reindex(frame.index).fillna(False).astype(bool)
        name = str(getattr(role, "value", role)).replace("_", " ")
        return Evidence(
            renderer="multitrend",
            roles=[role],
            mask=hunting.reindex(frame.index, fill_value=False),
            events=flips.reindex(frame.index, fill_value=False),
            # 0.111 (#310): the samples the rate is read over -- the signal's, fan-on when gated
            judged=judged,
            judged_basis=("fan-on " if gate is not None else "") + f"{name} samples",
            label=f"hunting at {self.warn_per_hr:g}/hr or faster",
            title=f"{equip}: {getattr(role, 'value', role)} hunting",
        )
