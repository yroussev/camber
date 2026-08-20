"""Rule: supply-fan **efficiency** drift -- the fan-power-at-matched-airflow detector.

A fan's electrical power tracks its airflow (P ∝ Q³ on the fan laws, but the screening question is
simpler): at a given airflow it should draw a repeatable power. When it draws **more power at the
same airflow**, its efficiency has fallen -- a slipping or worn belt, bearing drag, a degrading
motor or VFD, or the fan pushed off its curve by a restriction. Fan energy is a large share of an
AHU's use, so this is a direct energy-cost signal. It is the air-side twin of the pump-power
detector (:mod:`camber.rules.pump_power_rule`): both freeze a load-normalized ``power ~ f(flow)``
baseline and score the current period's excess at matched flow, only the flow is airflow, not gpm.

It is **one-sided up**: only *more* power at matched airflow is a fault; less is an efficiency gain.
Absolute fan power is unit-size-dependent, so (as with the pressure/power detectors) the sigma floor
carries the weight and the kW floor is a coarse backstop.

**The confound is stated, not hidden.** Fan power also rises when the *duct static setpoint* is
raised (the fan works harder to hold a higher static) with no efficiency loss at all. So when a
duct-static point is mapped the rule reports the concurrent static shift and **caveats** a power
excess that co-moves with rising static. Reuses the generic ``Role.POWER`` on the AHU's equip-frame
(the equip identifies the fan). Declines loudly when power or airflow is unmapped. **Not**
auto-registered (needs an injected ``BaselineStore``); run via
:meth:`camber.rules.base.Registry.run_periods`.
"""

from __future__ import annotations

import pandas as pd

from ..chillerbaseline import fit_load_baseline, load_drift_stats
from ..chillerdrift import (
    CUSUM_CLIP_SIGMA,
    CUSUM_LIMIT_SIGMA,
    CUSUM_MIN_CONSECUTIVE,
    CUSUM_SLACK_SIGMA,
    ApproachDriftMonitor,
)
from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from .base import Finding

_KIND = "fan_efficiency"

# Plausibility bounds for the power metric (kW) -- wide, only rejecting dropouts / bad values.
POWER_PLAUSIBLE = (0.0, 1e7)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds). One-sided UP (excess power is
# the fault); both a kW floor and a sigma floor must be cleared. The sigma floor carries the weight
# (absolute fan power is unit-size-dependent); the kW floor is a coarse backstop. Constructor args.
# ---------------------------------------------------------------------------------------------
POWER_WARN_KW = 0.5  # screening-grade -- coarse backstop
POWER_FAULT_KW = 1.0  # screening-grade
POWER_WARN_SIGMA = 2.5  # screening-grade
POWER_FAULT_SIGMA = 4.0  # screening-grade

# Below this airflow (cfm) the fan carries no condition information.
MIN_AIRFLOW = 200.0

# A co-moving duct-static rise of at least this much (static units) flags the setpoint confound.
STATIC_CONFOUND = 0.2


class FanEfficiencyDrift:
    """Detects a supply fan's power-at-matched-airflow drifting **up** from a frozen baseline.

    Reuses ``Role.POWER`` on the AHU equip-frame for fan power and ``Role.AIRFLOW`` as the load;
    pass ``power_role`` / ``airflow_role`` to override. A ``BaselineStore`` is injected, so (as with
    the other drift rules) it is **not** auto-registered.
    """

    name = "fan_efficiency_drift"

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        power_role: Role = Role.POWER,
        airflow_role: Role = Role.AIRFLOW,
        static_role: Role = Role.DUCT_STATIC,
        status_role: Role = Role.SUPPLY_FAN_STATUS,
        freeze_if_missing: bool = True,
        warn_kw: float = POWER_WARN_KW,  # screening-grade -- see the module note
        fault_kw: float = POWER_FAULT_KW,  # screening-grade
        warn_sigma: float = POWER_WARN_SIGMA,  # screening-grade
        fault_sigma: float = POWER_FAULT_SIGMA,  # screening-grade
        static_confound: float = STATIC_CONFOUND,
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_airflow: float = MIN_AIRFLOW,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.power_role = power_role
        self.airflow_role = airflow_role
        self.static_role = static_role
        self.status_role = status_role
        self.roles_required = (power_role, airflow_role)
        self.roles_optional = (static_role, status_role)
        self.freeze_if_missing = freeze_if_missing
        self.warn_kw = warn_kw
        self.fault_kw = fault_kw
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.static_confound = static_confound
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.min_airflow = min_airflow

    # ------------------------------------------------------------------ frame prep
    def _running(self, frame: pd.DataFrame) -> pd.DataFrame:
        if self.status_role in frame.columns:
            status = pd.to_numeric(frame[self.status_role], errors="coerce")
            return frame[status >= 0.5]
        return frame

    def _frozen_baseline(self, equip, base_frame, caveats):
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
            metric_col=self.power_role,
            load_col=self.airflow_role,
            min_load=self.min_airflow,
            metric_range=POWER_PLAUSIBLE,
        )
        if fit is None:
            caveats.append(
                f"could not evaluate {_KIND}: the baseline period would not support a fit "
                "(too few running samples, or too narrow an airflow range)"
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
        """One-sided-UP severity: only *excess* power clears the floors (both kW and sigma)."""
        if drift.drift_sigma != drift.drift_sigma:  # NaN: baseline had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on kW alone")
            if drift.drift_f >= self.fault_kw:
                return "fault"
            return "warn" if drift.drift_f >= self.warn_kw else "ok"
        if drift.drift_f >= self.fault_kw and drift.drift_sigma >= self.fault_sigma:
            return "fault"
        if drift.drift_f >= self.warn_kw and drift.drift_sigma >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ confound
    def _static_confound(self, base, cur, excess: bool, metrics: dict, caveats: list) -> None:
        """Report the duct-static shift; caveat a power excess that co-moves with rising static."""
        if self.static_role not in cur.columns or self.static_role not in base.columns:
            return
        base_s = pd.to_numeric(base[self.static_role], errors="coerce").median()
        cur_s = pd.to_numeric(cur[self.static_role], errors="coerce").median()
        if base_s != base_s or cur_s != cur_s:  # a NaN
            return
        shift = round(float(cur_s - base_s), 4)
        metrics["duct_static_shift"] = shift
        if excess and shift >= self.static_confound:
            caveats.append(
                f"duct static also rose {shift:+.2f} over the same window; the fan works harder to "
                "hold a higher static, so part of this power excess may be that, not efficiency "
                "loss -- check the static setpoint history"
            )

    # ------------------------------------------------------------------ the rule
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's fan power-at-airflow vs the frozen baseline -> a Finding."""
        caveats: list = []
        missing = [
            r.value for r in (self.power_role, self.airflow_role) if r not in current.columns
        ]
        if missing:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "power_or_airflow_not_mapped"},
                summary=f"{equip}: declined -- fan efficiency needs a power and an airflow point",
                caveats=[
                    "could not evaluate fan efficiency: a power point and an airflow normalizer "
                    f"must both be mapped; missing {', '.join(missing)}"
                ],
            )

        base_r, cur_r = self._running(baseline), self._running(current)
        frozen = self._frozen_baseline(equip, base_r, caveats)
        if frozen is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no frozen fan-power baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_r,
            metric_col=self.power_role,
            load_col=self.airflow_role,
            min_load=self.min_airflow,
            metric_range=POWER_PLAUSIBLE,
        )
        if drift is None:
            caveats.append(f"could not evaluate {_KIND}: no running samples in the current period")
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
            "fan_power_drift_kw": drift.drift_f,
            "fan_power_drift_sigma": drift.drift_sigma,
            "fan_power_drift_direction": direction,
            "fan_power_slope_kw_per_month": drift.slope_f_per_month,
            "fan_power_pct_outside_2sigma": drift.pct_outside_2sigma,
            "fan_power_n_current": drift.n_current,
            "fan_power_baseline_sigma_kw": frozen.sigma_f,
            "fan_power_baseline_frozen_at": rec.frozen_at if rec else "",
        }
        self._static_confound(base_r, cur_r, direction == "up", metrics, caveats)
        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's fitted airflow "
                "envelope, so part of this drift is extrapolated"
            )

        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="up",  # only sustained excess fan power alarms
            )
            run = monitor.run(
                cur_r,
                approach_col=self.power_role,
                tons_col=self.airflow_role,
                min_tons=self.min_airflow,
                approach_range=POWER_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "fan_power_sustained_alarm": run.alarmed,
                    "fan_power_first_alarm_at": run.first_alarm_at,
                    "fan_power_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        if direction == "up":
            headline = (
                f"{equip}: fan power excess {drift.drift_f:+.1f} kW "
                f"({drift.drift_sigma:.1f}σ) vs frozen baseline at matched airflow"
            )
        else:
            headline = (
                f"{equip}: fan power {drift.drift_f:+.1f} kW vs frozen baseline at matched airflow "
                "(less power is not a fault)"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=headline,
            caveats=caveats,
        )
