"""Rule: air-filter **loading** drift -- the dirty-filter detector, normalized for airflow.

An air filter's pressure drop **rises monotonically as its media loads** with captured dust; filter
life is judged in the field by comparing the measured pressure drop *at a given airflow* against a
final-DP threshold (design, manufacturer, or field standard). The catch is airflow: a filter's DP
grows with face velocity, and on a VAV system airflow swings all day, so a raw month-to-month DP
comparison confuses "more air this month" with "dirtier this month." The system curve is quadratic
in flow (ΔP ∝ Q²), so scoring filter DP against a frozen ``filterDP ~ f(airflow)`` baseline -- the
residual **at matched airflow** -- isolates the loading from the flow. (Physics per Chimack &
Sellers, *Using Extended Surface Air Filters in HVAC Systems*, ACEEE Summer Study.)

It is **one-sided up**: only a *rising* DP-at-matched-airflow is loading; a falling one is a filter
change (a welcome reset, handled by re-freezing the baseline). Filter DP is measured **across the
filter**, so the signal is filter-specific -- a wetted coil or a duct restriction that raises
*system* static does not move it -- which is why airflow is the only confound, and normalization
removes it. A sustained rise is the "schedule a filter change" signal, weeks before a static alarm
or a starved-ventilation complaint.

Reuses the existing ``Role.FILTER_DIFF_PRESS`` and ``Role.AIRFLOW``; no new role. Declines loudly
when either is unmapped. **Not** auto-registered (needs an injected ``BaselineStore``); run via
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

_KIND = "filter_loading"

# Plausibility bounds for the filter-DP metric (inH2O) -- wide, only rejecting bad values.
FILTER_DP_PLAUSIBLE = (0.0, 8.0)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds). One-sided UP (a rising DP is
# loading); both an inH2O floor and a sigma floor must be cleared. The sigma floor carries the
# weight (a filter's clean DP and its structural-capacity final DP vary widely by media/size); the
# inH2O floor is a coarse backstop. Constructor args; characterized, not established here.
# ---------------------------------------------------------------------------------------------
FILTER_WARN_INWC = 0.15  # screening-grade -- coarse backstop
FILTER_FAULT_INWC = 0.30  # screening-grade
FILTER_WARN_SIGMA = 2.5  # screening-grade
FILTER_FAULT_SIGMA = 4.0  # screening-grade

# Below this airflow (cfm) the filter carries no condition information.
MIN_AIRFLOW = 200.0


class FilterLoadingDrift:
    """Detects a filter's pressure drop drifting **up** at matched airflow from a frozen baseline.

    Reuses ``Role.FILTER_DIFF_PRESS`` (the metric) and ``Role.AIRFLOW`` (the load); pass
    ``dp_role`` / ``airflow_role`` to override. A ``BaselineStore`` is injected, so (as with the
    other drift rules) it is **not** auto-registered.
    """

    name = "filter_loading_drift"

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        dp_role: Role = Role.FILTER_DIFF_PRESS,
        airflow_role: Role = Role.AIRFLOW,
        status_role: Role = Role.SUPPLY_FAN_STATUS,
        freeze_if_missing: bool = True,
        warn_inwc: float = FILTER_WARN_INWC,  # screening-grade -- see the module note
        fault_inwc: float = FILTER_FAULT_INWC,  # screening-grade
        warn_sigma: float = FILTER_WARN_SIGMA,  # screening-grade
        fault_sigma: float = FILTER_FAULT_SIGMA,  # screening-grade
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_airflow: float = MIN_AIRFLOW,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.dp_role = dp_role
        self.airflow_role = airflow_role
        self.status_role = status_role
        self.roles_required = (dp_role, airflow_role)
        self.roles_optional = (status_role,)
        self.freeze_if_missing = freeze_if_missing
        self.warn_inwc = warn_inwc
        self.fault_inwc = fault_inwc
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
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
            metric_col=self.dp_role,
            load_col=self.airflow_role,
            min_load=self.min_airflow,
            metric_range=FILTER_DP_PLAUSIBLE,
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
            reason="initial (clean-filter) baseline frozen from the supplied baseline period",
        )
        return fit

    # ------------------------------------------------------------------ severity
    def _severity(self, drift, caveats) -> str:
        """One-sided-UP severity: only a *rising* DP clears the floors (both inH2O and sigma)."""
        if drift.drift_sigma != drift.drift_sigma:  # NaN: baseline had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on inH2O alone")
            if drift.drift_f >= self.fault_inwc:
                return "fault"
            return "warn" if drift.drift_f >= self.warn_inwc else "ok"
        if drift.drift_f >= self.fault_inwc and drift.drift_sigma >= self.fault_sigma:
            return "fault"
        if drift.drift_f >= self.warn_inwc and drift.drift_sigma >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ the rule
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's filter DP-at-airflow vs the frozen baseline -> a Finding."""
        caveats: list = []
        missing = [r.value for r in (self.dp_role, self.airflow_role) if r not in current.columns]
        if missing:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "filter_dp_or_airflow_not_mapped"},
                summary=f"{equip}: declined -- filter loading needs a filter-DP and an airflow",
                caveats=[
                    "could not evaluate filter loading: a filter differential-pressure point and "
                    f"an airflow normalizer must both be mapped; missing {', '.join(missing)}"
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
                summary=f"{equip}: declined -- no frozen clean-filter baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_r,
            metric_col=self.dp_role,
            load_col=self.airflow_role,
            min_load=self.min_airflow,
            metric_range=FILTER_DP_PLAUSIBLE,
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
            "filter_dp_drift_inwc": drift.drift_f,
            "filter_dp_drift_sigma": drift.drift_sigma,
            "filter_dp_drift_direction": direction,
            "filter_dp_slope_inwc_per_month": drift.slope_f_per_month,
            "filter_dp_pct_outside_2sigma": drift.pct_outside_2sigma,
            "filter_dp_n_current": drift.n_current,
            "filter_dp_baseline_sigma_inwc": frozen.sigma_f,
            "filter_dp_baseline_frozen_at": rec.frozen_at if rec else "",
        }
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
                direction="up",  # only a sustained rise (loading) alarms
            )
            run = monitor.run(
                cur_r,
                approach_col=self.dp_role,
                tons_col=self.airflow_role,
                min_tons=self.min_airflow,
                approach_range=FILTER_DP_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "filter_dp_sustained_alarm": run.alarmed,
                    "filter_dp_first_alarm_at": run.first_alarm_at,
                    "filter_dp_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        if direction == "up":
            headline = (
                f"{equip}: filter DP rose {drift.drift_f:+.2f} inH2O "
                f"({drift.drift_sigma:.1f}σ) vs frozen baseline at matched airflow -- loading"
            )
        else:
            headline = (
                f"{equip}: filter DP {drift.drift_f:+.2f} inH2O vs frozen baseline at matched "
                "airflow (a fall is a filter change, not a fault)"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=headline,
            caveats=caveats,
        )
