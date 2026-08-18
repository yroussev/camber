"""Rule: cooling-tower **approach** drift -- the tower's heat-rejection detector, over time.

:class:`~camber.rules.coolingtower_rule.CoolingTowerApproach` answers "is the approach high?"
against a static design constant. This rule answers the earlier question, "has the approach been
**climbing**?" -- the signal that shows a tower fouling weeks before it's a work order. A tower
can only cool the condenser water *toward* the ambient wet-bulb; the gap it achieves is the
approach::

    approach = CW_SUPPLY_TEMP - wet_bulb        (condenser water leaving the tower)

and it widens as the fill fouls or scales, nozzles plug, or airflow drops (failed / under-driven
fans). It is **one-sided**: like a heat exchanger's approach, fouling only ever *widens* it, so a
narrowing is not a fault. A widening approach raises condenser-water temperature, chiller lift, and
kW/ton -- so this pairs directly with the chiller condenser-side drift detectors.

The comparison is made **at matched load**. Approach widens with heat rejection on its own, so a
busier current period looks like degradation to any level-vs-level test; scoring the residuals
against a fitted ``approach ~ f(tons)`` line removes that confound (:mod:`camber.chillerbaseline`),
using chiller tons as the condenser-heat proxy the CW-range detector already uses. The reference is
**frozen**, not rolling (:mod:`camber.store.modelstore`): a refit from the window being judged would
define away the drift it is meant to catch.

Wet-bulb is rarely a BAS point, so it is taken measured when present, else derived from outdoor
dry-bulb + RH via Stull's approximation (:func:`camber.coolingtower.tower_approach_f`)
-- no psychrometric dependency. The tower's condenser-water supply temperature plus *some* wet-bulb
source are optional inputs; when they are absent the rule **declines with a caveat** rather than
being silently skipped, so a tower missing from a performance report cannot read as a healthy one.
"""

from __future__ import annotations

import pandas as pd

from ..chillerbaseline import fit_load_baseline, load_drift_stats, tons_from_flow
from ..chillerdrift import (
    CUSUM_CLIP_SIGMA,
    CUSUM_LIMIT_SIGMA,
    CUSUM_MIN_CONSECUTIVE,
    CUSUM_SLACK_SIGMA,
    ApproachDriftMonitor,
)
from ..coolingtower import tower_approach_f
from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_FLOW: "CHW_Flow",
    Role.CW_SUPPLY_TEMP: "CWS_Temp",
    Role.WETBULB_TEMP: "WetBulb",
    Role.OAT: "OAT",
    Role.OUTDOOR_RH: "RH",
}

_KIND = "cooling_tower_approach"
_METRIC = "tower_approach_f"  # the derived column the baseline is fitted on

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds).
#
# Characterized from the behaviour of this signal class, not established on the towers this will run
# against. All are constructor arguments, so tuning is a config change, not a code change.
#
# The floors sit a little wider than the chiller condenser-approach rule's (1/2 degF, 2/3 sigma):
# tower approach is a *difference against wet-bulb*, and the wet-bulb term is noisy -- more so
# when it is derived from OAT + RH rather than measured. As with the chiller detectors, a finding
# must clear BOTH a degF floor and a sigma floor. This rule is one-sided: only a *widening* (a
# positive drift, the fouling direction) is scored; a narrowing returns ok.
# ---------------------------------------------------------------------------------------------
TOWER_APPROACH_WARN_F = 1.5  # screening-grade, applied to the (signed) widening
TOWER_APPROACH_FAULT_F = 3.0  # screening-grade
TOWER_APPROACH_WARN_SIGMA = 2.5  # screening-grade
TOWER_APPROACH_FAULT_SIGMA = 4.0  # screening-grade

# Plausibility bounds on the approach itself, in degF. A tower can't beat the wet-bulb, so a small
# negative reading is sensor noise (kept); the ceiling drops crossed/failed sensors.
TOWER_APPROACH_PLAUSIBLE_F = (-2.0, 40.0)


class CoolingTowerApproachDrift:
    """Detects a cooling tower's approach drifting **up** from a frozen, load-normalized baseline.

    A :class:`~camber.store.modelstore.BaselineStore` is injected so the reference survives between
    runs, which (as with the chiller drift rules) means this is **not** auto-registered in
    :func:`camber.rules.builtin.builtin_registry`; the caller instantiates and registers it. Run it
    via :meth:`camber.rules.base.Registry.run_periods`.

    Like the CW-range and subcooling rules, the period statistic and the sustained-shift alarm are
    reported in **one** Finding. Unlike them it is **one-sided**: fouling only widens, so
    :meth:`_severity` scores the signed drift (a narrowing is ok); the CUSUM runs one-sided (up).
    """

    name = "cooling_tower_approach_drift"
    roles_required = (Role.CHW_FLOW, Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP)
    roles_optional = (Role.CW_SUPPLY_TEMP, Role.WETBULB_TEMP, Role.OAT, Role.OUTDOOR_RH)

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        freeze_if_missing: bool = True,
        warn_f: float = TOWER_APPROACH_WARN_F,  # screening-grade -- see the module note
        fault_f: float = TOWER_APPROACH_FAULT_F,  # screening-grade
        warn_sigma: float = TOWER_APPROACH_WARN_SIGMA,  # screening-grade
        fault_sigma: float = TOWER_APPROACH_FAULT_SIGMA,  # screening-grade
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_tons: float = 5.0,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.freeze_if_missing = freeze_if_missing
        self.warn_f = warn_f
        self.fault_f = fault_f
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.min_tons = min_tons

    # ------------------------------------------------------------------ inputs
    @staticmethod
    def _has_approach(frame: pd.DataFrame) -> bool:
        """Whether the tower approach can be computed: a CW supply temp plus a wet-bulb source."""
        if Role.CW_SUPPLY_TEMP not in frame.columns:
            return False
        return Role.WETBULB_TEMP in frame.columns or (
            Role.OAT in frame.columns and Role.OUTDOOR_RH in frame.columns
        )

    def _prepared(self, frame: pd.DataFrame) -> pd.DataFrame:
        """A ``tons`` + tower-approach frame; tons derived as in :mod:`camber.chiller`."""
        legacy = frame.rename(columns={r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns})
        out = pd.DataFrame({"tons": tons_from_flow(legacy)}, index=frame.index)
        if self._has_approach(frame):
            out[_METRIC] = tower_approach_f(legacy)
        return out

    def _frozen_baseline(self, equip, base_frame, caveats):
        """The frozen approach baseline, freezing an initial one from ``base_frame`` if none."""
        frozen = self.store.model_for(self.site, equip, _KIND)
        if frozen is not None:
            return frozen
        if not self.freeze_if_missing:
            caveats.append(
                f"could not evaluate {_KIND}: no frozen baseline and freezing is disabled"
            )
            return None
        fit = fit_load_baseline(
            base_frame,
            metric_col=_METRIC,
            load_col="tons",
            min_load=self.min_tons,
            metric_range=TOWER_APPROACH_PLAUSIBLE_F,
        )
        if fit is None:
            caveats.append(
                f"could not evaluate {_KIND}: the baseline period would not support a fit "
                "(too few loaded samples, or too narrow a load range)"
            )
            return None
        idx = base_frame.index
        self.store.freeze(
            fit,
            site=self.site,
            equip=equip,
            kind=_KIND,
            frozen_at=self.run_id,
            period=(str(idx.min()), str(idx.max())),
            reason="initial baseline frozen from the supplied baseline period",
        )
        return fit

    # ------------------------------------------------------------------ severity
    def _severity(self, drift, caveats) -> str:
        """One-sided severity: a *widening* (positive drift) must clear both floors."""
        if drift.drift_sigma != drift.drift_sigma:  # NaN: baseline had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on degF alone")
            if drift.drift_f >= self.fault_f:
                return "fault"
            return "warn" if drift.drift_f >= self.warn_f else "ok"
        if drift.drift_f >= self.fault_f and drift.drift_sigma >= self.fault_sigma:
            return "fault"
        if drift.drift_f >= self.warn_f and drift.drift_sigma >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ the rule
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's tower approach against the frozen baseline."""
        caveats: list = []
        if not self._has_approach(current):
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "tower_approach_not_available"},
                summary=f"{equip}: declined -- no cooling-tower approach available",
                caveats=[
                    "could not evaluate cooling-tower performance: the approach needs a "
                    "condenser-water supply temperature and a wet-bulb (measured, or outdoor "
                    "dry-bulb + RH to derive it), and this equipment is missing one of them"
                ],
            )

        base_t, cur_t = self._prepared(baseline), self._prepared(current)
        frozen = self._frozen_baseline(equip, base_t, caveats)
        if frozen is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no frozen tower-approach baseline",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_t,
            metric_col=_METRIC,
            load_col="tons",
            min_load=self.min_tons,
            metric_range=TOWER_APPROACH_PLAUSIBLE_F,
        )
        if drift is None:
            caveats.append(f"could not evaluate {_KIND}: no loaded samples in the current period")
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- nothing scoreable in the current period",
                caveats=caveats,
            )

        severity = self._severity(drift, caveats)
        direction = "up" if drift.drift_f >= 0 else "down"
        rec = self.store.get(self.site, equip, _KIND)
        metrics = {
            "tower_approach_drift_f": drift.drift_f,
            "tower_approach_drift_sigma": drift.drift_sigma,
            "tower_approach_drift_direction": direction,
            "tower_approach_slope_f_per_month": drift.slope_f_per_month,
            "tower_approach_pct_outside_2sigma": drift.pct_outside_2sigma,
            "tower_approach_n_current": drift.n_current,
            "tower_approach_baseline_sigma_f": frozen.sigma_f,
            "tower_approach_baseline_frozen_at": rec.frozen_at if rec else "",
        }
        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's fitted load envelope, "
                "so part of this drift is extrapolated"
            )

        # the same frozen baseline, folded sample-by-sample: has it widened and *stayed* widened?
        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="up",  # fouling only ever widens an approach
            )
            run = monitor.run(
                cur_t,
                approach_col=_METRIC,
                tons_col="tons",
                min_tons=self.min_tons,
                approach_range=TOWER_APPROACH_PLAUSIBLE_F,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "tower_approach_sustained_alarm": run.alarmed,
                    "tower_approach_first_alarm_at": run.first_alarm_at,
                    "tower_approach_alarm_direction": run.alarm_direction,
                }
            )
        # Severity is magnitude-driven (screening-grade); the sustained-alarm metrics, when present,
        # add a temporal claim resting on the weaker, untuned parameters -- label both.
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=(
                f"{equip}: cooling-tower approach widened {drift.drift_f:+.1f}°F "
                f"({drift.drift_sigma:.1f}σ) vs frozen baseline at matched load"
            ),
            caveats=caveats,
        )
