"""Rule: hydronic **loop differential-pressure** drift -- the system-resistance / control detector.

A loop's differential pressure sits on its **system curve** (DP ∝ Q²): at a given flow it should be
a repeatable pressure. When DP is **higher than it used to be at matched flow**, the system has
become more restrictive -- valve-authority loss, a throttled or stuck-closed balancing valve; when
it is **lower**, a bypass has opened, a decoupler is short-circuiting, or a valve is stuck open.
Both are faults, so this rule is **two-sided**, freezing a load-normalized ``DP ~ f(flow)`` baseline
and scoring the current period's residual at matched flow.

**The confound is the reset schedule, and it is handled, not just flagged.** On a DP-controlled loop
the pump holds DP at a *setpoint* that operators often reset (by OAT, by valve position, on a
schedule). A DP that moved because its **setpoint** moved is not a fault. So when a DP-setpoint
point is mapped, the rule measures the concurrent setpoint shift and **judges on the residual drift
not explained by it** -- if the DP move is fully accounted for by the reset, the residual is below
the floor and the rule does not fault (reporting the setpoint-driven move as a caveat instead).
Without a setpoint point it scores the raw DP drift and says so.

Loop-parameterized (chilled-water by default; pass the hot-water DP / flow / setpoint roles for that
loop). Declines loudly when DP or the flow normalizer is unmapped. **Not** auto-registered (needs an
injected :class:`~camber.store.modelstore.BaselineStore`); run via
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

_KIND = "loop_dp"
_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}

# Plausibility bounds for the DP metric (psi / ft / inH2O) -- wide, only rejecting dropouts.
DP_PLAUSIBLE = (0.0, 100.0)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds). Two-sided, applied to |drift|;
# the sigma floor carries the weight (a loop's DP units and magnitude vary), the DP-unit floor is a
# coarse backstop. Constructor args; characterized from the signal class, not established here.
# ---------------------------------------------------------------------------------------------
DP_WARN = 2.0  # screening-grade, applied to |drift|
DP_FAULT = 4.0  # screening-grade, applied to |drift|
DP_WARN_SIGMA = 2.5  # screening-grade, applied to |drift|
DP_FAULT_SIGMA = 4.0  # screening-grade, applied to |drift|

# Below this flow the loop carries no condition information.
MIN_LOAD = 50.0

# A setpoint shift of at least this much (DP units) triggers the reset-confound adjustment.
DP_SP_CONFOUND = 1.0


class LoopDPDrift:
    """Detects a hydronic loop's differential pressure drifting from a frozen, flow-normalized fit.

    Defaults to the chilled-water loop (``dp_role=CHW_DIFF_PRESS``, ``flow_role=CHW_FLOW``,
    ``sp_role=CHW_DIFF_PRESS_SP``); pass the hot-water roles for that loop. A ``BaselineStore`` is
    injected, so (as with the chiller drift rules) it is **not** auto-registered.
    """

    name = "loop_dp_drift"

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        dp_role: Role = Role.CHW_DIFF_PRESS,
        flow_role: Role = Role.CHW_FLOW,
        sp_role: Role = Role.CHW_DIFF_PRESS_SP,
        status_role: Role = Role.PUMP_STATUS,
        freeze_if_missing: bool = True,
        warn: float = DP_WARN,  # screening-grade -- see the module note
        fault: float = DP_FAULT,  # screening-grade
        warn_sigma: float = DP_WARN_SIGMA,  # screening-grade
        fault_sigma: float = DP_FAULT_SIGMA,  # screening-grade
        dp_sp_confound: float = DP_SP_CONFOUND,
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_load: float = MIN_LOAD,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.dp_role = dp_role
        self.flow_role = flow_role
        self.sp_role = sp_role
        self.status_role = status_role
        self.roles_required = (dp_role, flow_role)
        self.roles_optional = (sp_role, status_role)
        self.freeze_if_missing = freeze_if_missing
        self.warn = warn
        self.fault = fault
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.dp_sp_confound = dp_sp_confound
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.min_load = min_load

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
            load_col=self.flow_role,
            min_load=self.min_load,
            metric_range=DP_PLAUSIBLE,
        )
        if fit is None:
            caveats.append(
                f"could not evaluate {_KIND}: the baseline period would not support a fit "
                "(too few loaded samples, or too narrow a flow range)"
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
        """Score the current period's loop DP vs the frozen baseline; return a Finding."""
        caveats: list = []
        missing = [r.value for r in (self.dp_role, self.flow_role) if r not in current.columns]
        if missing:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "dp_or_flow_not_mapped"},
                summary=f"{equip}: declined -- loop DP needs a differential-pressure and a flow",
                caveats=[
                    "could not evaluate loop DP: a differential-pressure point and a flow (or "
                    f"speed) normalizer must both be mapped; missing {', '.join(missing)}"
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
                summary=f"{equip}: declined -- no frozen DP baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_r,
            metric_col=self.dp_role,
            load_col=self.flow_role,
            min_load=self.min_load,
            metric_range=DP_PLAUSIBLE,
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

        severity = self._severity(drift.drift_f, drift.drift_sigma)
        direction = "up" if drift.drift_f >= 0 else "down"
        rec = self.store.get(self.site, equip, _KIND)
        metrics = {
            "loop_dp_drift": drift.drift_f,
            "loop_dp_drift_sigma": drift.drift_sigma,
            "loop_dp_drift_direction": direction,
            "loop_dp_slope_per_month": drift.slope_f_per_month,
            "loop_dp_pct_outside_2sigma": drift.pct_outside_2sigma,
            "loop_dp_n_current": drift.n_current,
            "loop_dp_baseline_sigma": frozen.sigma_f,
            "loop_dp_baseline_frozen_at": rec.frozen_at if rec else "",
        }

        # DP-reset confound: judge on the drift NOT explained by a concurrent setpoint move
        setpoint_driven = False
        if self.sp_role in cur_r.columns and self.sp_role in base_r.columns:
            base_sp = pd.to_numeric(base_r[self.sp_role], errors="coerce").median()
            cur_sp = pd.to_numeric(cur_r[self.sp_role], errors="coerce").median()
            if base_sp == base_sp and cur_sp == cur_sp:  # both non-NaN
                sp_shift = round(float(cur_sp - base_sp), 3)
                metrics["dp_sp_shift"] = sp_shift
                if abs(sp_shift) >= self.dp_sp_confound:
                    residual = drift.drift_f - sp_shift
                    residual_sigma = (
                        residual / frozen.sigma_f if frozen.sigma_f > 0 else float("nan")
                    )
                    adj = self._severity(residual, residual_sigma)
                    metrics["loop_dp_residual_drift"] = round(residual, 4)
                    if _RANK[adj] < _RANK[severity]:
                        setpoint_driven = True
                        severity = adj
                        tail = (
                            "no independent control fault remains"
                            if adj == "ok"
                            else "a control fault remains after the reset"
                        )
                        caveats.append(
                            f"DP setpoint shifted {sp_shift:+.1f} over the window; judged on the "
                            f"residual drift {residual:+.1f} not explained by the reset -- {tail}"
                        )
                    else:
                        caveats.append(
                            f"DP setpoint shifted {sp_shift:+.1f} over the window, but the DP "
                            "drift is not explained by it -- an independent control fault"
                        )
        metrics["loop_dp_setpoint_driven"] = setpoint_driven

        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's fitted flow envelope, "
                "so part of this drift is extrapolated"
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
                approach_col=self.dp_role,
                tons_col=self.flow_role,
                min_tons=self.min_load,
                approach_range=DP_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "loop_dp_sustained_alarm": run.alarmed,
                    "loop_dp_first_alarm_at": run.first_alarm_at,
                    "loop_dp_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        cause = (
            "rising system resistance / valve authority"
            if direction == "up"
            else "bypass / stuck-open"
        )
        arrow = "rose" if direction == "up" else "fell"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=(
                f"{equip}: loop DP {arrow} {abs(drift.drift_f):.1f} "
                f"({abs(drift.drift_sigma):.1f}σ) vs frozen baseline at matched flow -- {cause}"
            ),
            caveats=caveats,
        )
