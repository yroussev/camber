"""Rule: **duct static-pressure control** drift -- reset-schedule-aware.

On a VAV air handler the supply fan modulates to hold the duct static pressure at a **setpoint**, so
at steady control the static sits near that setpoint across the airflow range. When the static-vs-
airflow relationship **drifts at matched airflow**, the control is no longer holding as it did: the
static falling below where it used to sit means the fan can no longer make it (fan / belt
degradation, a leakier or more-open duct system), and rising above it means over-pressurization (a
static sensor reading low, a stuck downstream damper, or demand that collapsed while the setpoint
stayed put). Both are faults, so this rule is **two-sided**, freezing a load-normalized
``static ~ f(airflow)`` baseline and scoring the current period's residual at matched airflow.

**The confound is the static-pressure reset, and it is handled, not just flagged.** Guideline-36
trim-and-respond resets the static setpoint continuously; a static that moved because its
**setpoint** moved is not a fault. So when a duct-static-setpoint point is mapped, the rule measures
the concurrent setpoint shift and **judges on the residual drift not explained by it** -- if the
static move is fully accounted for by the reset, the residual is below the floor and the rule does
not fault (reported as a caveat). Without a setpoint point it scores the raw static drift.

This is the air-side twin of :mod:`camber.rules.loop_dp_rule` (hydronic loop DP). Reuses the
existing ``DUCT_STATIC`` / ``AIRFLOW`` / ``DUCT_STATIC_SP`` roles; no new role. Declines loudly when
static or airflow is unmapped. **Not** auto-registered (needs an injected ``BaselineStore``); run
via :meth:`camber.rules.base.Registry.run_periods`.
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

_KIND = "duct_static"
_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}

# Plausibility bounds for the duct-static metric (inH2O) -- wide, only rejecting dropouts.
STATIC_PLAUSIBLE = (0.0, 6.0)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds). Two-sided, applied to |drift|;
# the sigma floor carries the weight (a system's static magnitude varies), the inH2O floor is a
# coarse backstop. Constructor args; characterized from the signal class, not established here.
# ---------------------------------------------------------------------------------------------
STATIC_WARN_INWC = 0.15  # screening-grade, applied to |drift|
STATIC_FAULT_INWC = 0.30  # screening-grade, applied to |drift|
STATIC_WARN_SIGMA = 2.5  # screening-grade, applied to |drift|
STATIC_FAULT_SIGMA = 4.0  # screening-grade, applied to |drift|

# Below this airflow (cfm) the system carries no condition information.
MIN_AIRFLOW = 200.0

# A setpoint shift of at least this much (inH2O) triggers the reset-confound adjustment.
STATIC_SP_CONFOUND = 0.1


class DuctStaticControlDrift:
    """Detects a VAV system's duct static drifting from a frozen, airflow-normalized baseline.

    Reuses ``Role.DUCT_STATIC`` (metric), ``Role.AIRFLOW`` (load) and ``Role.DUCT_STATIC_SP`` (the
    reset confound); pass the ``*_role`` arguments to override. A ``BaselineStore`` is injected, so
    (as with the other drift rules) it is **not** auto-registered.
    """

    name = "duct_static_drift"

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        static_role: Role = Role.DUCT_STATIC,
        airflow_role: Role = Role.AIRFLOW,
        sp_role: Role = Role.DUCT_STATIC_SP,
        status_role: Role = Role.SUPPLY_FAN_STATUS,
        freeze_if_missing: bool = True,
        warn: float = STATIC_WARN_INWC,  # screening-grade -- see the module note
        fault: float = STATIC_FAULT_INWC,  # screening-grade
        warn_sigma: float = STATIC_WARN_SIGMA,  # screening-grade
        fault_sigma: float = STATIC_FAULT_SIGMA,  # screening-grade
        sp_confound: float = STATIC_SP_CONFOUND,
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_airflow: float = MIN_AIRFLOW,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.static_role = static_role
        self.airflow_role = airflow_role
        self.sp_role = sp_role
        self.status_role = status_role
        self.roles_required = (static_role, airflow_role)
        self.roles_optional = (sp_role, status_role)
        self.freeze_if_missing = freeze_if_missing
        self.warn = warn
        self.fault = fault
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.sp_confound = sp_confound
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
            metric_col=self.static_role,
            load_col=self.airflow_role,
            min_load=self.min_airflow,
            metric_range=STATIC_PLAUSIBLE,
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
    def _severity(self, drift_f, drift_sigma) -> str:
        """Two-sided severity from a (drift, sigma) pair; |drift| must clear both floors."""
        mag_f = abs(drift_f)
        if drift_sigma != drift_sigma:  # NaN: baseline had no residual scatter
            if mag_f >= self.fault:
                return "fault"
            return "warn" if mag_f >= self.warn else "ok"
        mag_sigma = abs(drift_sigma)
        if mag_f >= self.fault and mag_sigma >= self.fault_sigma:
            return "fault"
        if mag_f >= self.warn and mag_sigma >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ the rule
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's duct static vs the frozen baseline; return a Finding."""
        caveats: list = []
        missing = [
            r.value for r in (self.static_role, self.airflow_role) if r not in current.columns
        ]
        if missing:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "static_or_airflow_not_mapped"},
                summary=f"{equip}: declined -- duct static needs a static and an airflow point",
                caveats=[
                    "could not evaluate duct static: a duct-static point and an airflow normalizer "
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
                summary=f"{equip}: declined -- no frozen duct-static baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_r,
            metric_col=self.static_role,
            load_col=self.airflow_role,
            min_load=self.min_airflow,
            metric_range=STATIC_PLAUSIBLE,
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

        severity = self._severity(drift.drift_f, drift.drift_sigma)
        direction = "up" if drift.drift_f >= 0 else "down"
        rec = self.store.get(self.site, equip, _KIND)
        metrics = {
            "duct_static_drift_inwc": drift.drift_f,
            "duct_static_drift_sigma": drift.drift_sigma,
            "duct_static_drift_direction": direction,
            "duct_static_slope_inwc_per_month": drift.slope_f_per_month,
            "duct_static_pct_outside_2sigma": drift.pct_outside_2sigma,
            "duct_static_n_current": drift.n_current,
            "duct_static_baseline_sigma_inwc": frozen.sigma_f,
            "duct_static_baseline_frozen_at": rec.frozen_at if rec else "",
        }

        # static-reset confound: judge on the drift NOT explained by a concurrent setpoint move
        setpoint_driven = False
        if self.sp_role in cur_r.columns and self.sp_role in base_r.columns:
            base_sp = pd.to_numeric(base_r[self.sp_role], errors="coerce").median()
            cur_sp = pd.to_numeric(cur_r[self.sp_role], errors="coerce").median()
            if base_sp == base_sp and cur_sp == cur_sp:  # both non-NaN
                sp_shift = round(float(cur_sp - base_sp), 4)
                metrics["static_sp_shift"] = sp_shift
                if abs(sp_shift) >= self.sp_confound:
                    residual = drift.drift_f - sp_shift
                    residual_sigma = (
                        residual / frozen.sigma_f if frozen.sigma_f > 0 else float("nan")
                    )
                    adj = self._severity(residual, residual_sigma)
                    metrics["duct_static_residual_drift"] = round(residual, 4)
                    if _RANK[adj] < _RANK[severity]:
                        setpoint_driven = True
                        severity = adj
                        tail = (
                            "no independent control fault remains"
                            if adj == "ok"
                            else "a control fault remains after the reset"
                        )
                        caveats.append(
                            f"duct-static setpoint shifted {sp_shift:+.2f} inH2O; judged on the "
                            f"residual drift {residual:+.2f} not explained by the reset -- {tail}"
                        )
                    else:
                        caveats.append(
                            f"duct-static setpoint shifted {sp_shift:+.2f} inH2O, but the static "
                            "drift is not explained by it -- an independent control fault"
                        )
        metrics["duct_static_setpoint_driven"] = setpoint_driven

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
                direction="both",  # a rise and a fall are both faults
            )
            run = monitor.run(
                cur_r,
                approach_col=self.static_role,
                tons_col=self.airflow_role,
                min_tons=self.min_airflow,
                approach_range=STATIC_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "duct_static_sustained_alarm": run.alarmed,
                    "duct_static_first_alarm_at": run.first_alarm_at,
                    "duct_static_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        cause = (
            "over-pressurization / sensor-low / stuck damper"
            if direction == "up"
            else "the fan cannot hold setpoint (fan degradation, duct leakage)"
        )
        arrow = "rose" if direction == "up" else "fell"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=(
                f"{equip}: duct static {arrow} {abs(drift.drift_f):.2f} inH2O "
                f"({abs(drift.drift_sigma):.1f}σ) vs frozen baseline at matched airflow -- {cause}"
            ),
            caveats=caveats,
        )
