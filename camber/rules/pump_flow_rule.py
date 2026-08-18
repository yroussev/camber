"""Rule: pump **flow-at-matched-speed** drift -- the pump-wear / restriction detector.

A healthy pump on a variable-speed drive moves a flow set by the affinity laws (Q ∝ N): at a given
speed it delivers a repeatable flow. When it delivers **less flow at the same speed**, something has
degraded -- a worn impeller or wear-ring, a clogged suction strainer, cavitation, entrained air, or
(the confound) a rise in system resistance that pushes the operating point back up the pump curve.
This rule freezes a load-normalized ``flow ~ f(speed)`` baseline and scores the current period's
flow deficit at matched speed, exactly as the chiller detectors score approach/pressure at matched
load -- only the normalizer is pump speed, not thermal tons.

It is **one-sided down**: only a flow *deficit* is a fault; a surplus at matched speed is not (the
:class:`camber.chillerdrift.ApproachDriftMonitor` gained a ``direction="down"`` mode for exactly
this signal). The existing ``HwPumpDrift``/``ChwPumpDrift`` heuristics answer "is the pump pinned
near full or near minimum right now?" against static thresholds; this answers the earlier, quieter
"is it moving less flow than it used to, at the same speed?" -- the wear signal that shows first.

**The confound is stated, not hidden.** A flow deficit at matched speed is ambiguous between the
pump itself getting weaker and the *system* getting more restrictive (a throttled or stuck-closed
valve downstream). Load normalization does not remove it. So when a loop differential-pressure point
is mapped the rule reports the concurrent DP shift and **caveats** a deficit that co-moves with
*rising* DP -- the signature of added system resistance, not pump wear (the head detector and the
loop diagnosis resolve it further). The verdict stays screening-grade.

The rule is **loop-parameterized**: it defaults to the chilled-water roles but takes ``flow_role`` /
``speed_role`` (and the optional confound / status roles) so one class serves a hot-water loop too
-- the equip identifies which pump. It is **not** auto-registered (it needs an injected
``BaselineStore``); run it via :meth:`camber.rules.base.Registry.run_periods`.
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

_KIND = "pump_flow"

# Plausibility bounds for the flow metric (gpm) -- wide, only rejecting dropouts / bad values.
FLOW_PLAUSIBLE = (0.0, 1e6)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds).
#
# One-sided DOWN (a flow deficit is the fault), and a finding must clear BOTH a gpm floor and a
# sigma floor. As with the pressure detectors the sigma floor carries the weight: absolute flow is
# pump-size-dependent, so a fixed gpm floor cannot mean the same thing across pumps, while a deficit
# measured against the baseline's own residual scatter self-scales; the gpm floor is a coarse
# backstop that stops a very tight baseline from firing on a trivially small deficit. All are
# constructor arguments, characterized from the signal class, not established on the pumps here.
# ---------------------------------------------------------------------------------------------
FLOW_WARN_GPM = 15.0  # screening-grade -- coarse backstop; the sigma floor does the work
FLOW_FAULT_GPM = 30.0  # screening-grade
FLOW_WARN_SIGMA = 2.5  # screening-grade
FLOW_FAULT_SIGMA = 4.0  # screening-grade

# Below this drive speed (%) the pump carries no condition information -- the analog of min_tons.
MIN_SPEED = 15.0

# A co-moving loop-DP rise of at least this much (in the DP's own units) alongside the flow deficit
# flags the system-resistance confound (the deficit may be a throttled valve, not pump wear).
DP_CONFOUND = 1.0


class PumpFlowDrift:
    """Detects a pump's flow-at-matched-speed drifting **down** from a frozen baseline.

    Defaults to the chilled-water loop roles; pass ``flow_role`` / ``speed_role`` (and optionally
    ``dp_role`` / ``dp_sp_role`` for the resistance confound and ``status_role`` to mask to running
    samples) to point it at a hot-water loop. A :class:`~camber.store.modelstore.BaselineStore` is
    injected so the reference survives between runs, which (as with the chiller drift rules) means
    it is **not** auto-registered; the caller instantiates and registers it.
    """

    name = "pump_flow_drift"

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        flow_role: Role = Role.CHW_FLOW,
        speed_role: Role = Role.CHW_PUMP_SPEED,
        dp_role: Role = Role.CHW_DIFF_PRESS,
        dp_sp_role: Role = Role.CHW_DIFF_PRESS_SP,
        status_role: Role = Role.PUMP_STATUS,
        freeze_if_missing: bool = True,
        warn_gpm: float = FLOW_WARN_GPM,  # screening-grade -- see the module note
        fault_gpm: float = FLOW_FAULT_GPM,  # screening-grade
        warn_sigma: float = FLOW_WARN_SIGMA,  # screening-grade
        fault_sigma: float = FLOW_FAULT_SIGMA,  # screening-grade
        dp_confound: float = DP_CONFOUND,
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_speed: float = MIN_SPEED,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.flow_role = flow_role
        self.speed_role = speed_role
        self.dp_role = dp_role
        self.dp_sp_role = dp_sp_role
        self.status_role = status_role
        # Instance-level so the loop parameterization is reflected to Registry.run_periods.
        self.roles_required = (flow_role, speed_role)
        self.roles_optional = (dp_role, dp_sp_role, status_role)
        self.freeze_if_missing = freeze_if_missing
        self.warn_gpm = warn_gpm
        self.fault_gpm = fault_gpm
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.dp_confound = dp_confound
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.min_speed = min_speed

    # ------------------------------------------------------------------ frame prep
    def _running(self, frame: pd.DataFrame) -> pd.DataFrame:
        """Mask to running samples when a pump-status point is mapped (else the frame unchanged)."""
        if self.status_role in frame.columns:
            status = pd.to_numeric(frame[self.status_role], errors="coerce")
            return frame[status >= 0.5]
        return frame

    def _frozen_baseline(self, equip, base_frame, caveats):
        """The frozen flow~speed baseline, freezing an initial one from ``base_frame`` if none."""
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
            metric_col=self.flow_role,
            load_col=self.speed_role,
            min_load=self.min_speed,
            metric_range=FLOW_PLAUSIBLE,
        )
        if fit is None:
            caveats.append(
                f"could not evaluate {_KIND}: the baseline period would not support a fit "
                "(too few running samples, or too narrow a speed range)"
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
        """One-sided-DOWN severity: only a flow *deficit* clears the floors (both gpm and sigma)."""
        if drift.drift_sigma != drift.drift_sigma:  # NaN: baseline had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on gpm alone")
            if drift.drift_f <= -self.fault_gpm:
                return "fault"
            return "warn" if drift.drift_f <= -self.warn_gpm else "ok"
        if drift.drift_f <= -self.fault_gpm and drift.drift_sigma <= -self.fault_sigma:
            return "fault"
        if drift.drift_f <= -self.warn_gpm and drift.drift_sigma <= -self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ confound
    def _resistance_confound(self, base, cur, deficit: bool, metrics: dict, caveats: list) -> None:
        """Report the loop-DP shift; caveat a deficit that co-moves with rising DP (resistance)."""
        if self.dp_role not in cur.columns or self.dp_role not in base.columns:
            return
        base_dp = pd.to_numeric(base[self.dp_role], errors="coerce").median()
        cur_dp = pd.to_numeric(cur[self.dp_role], errors="coerce").median()
        if base_dp != base_dp or cur_dp != cur_dp:  # a NaN
            return
        shift = round(float(cur_dp - base_dp), 3)
        metrics["dp_shift"] = shift
        if deficit and shift >= self.dp_confound:
            caveats.append(
                f"loop differential pressure also rose {shift:+.1f} over the same window; a flow "
                "deficit with rising DP points at increased system resistance (a throttled or "
                "stuck-closed valve downstream) rather than pump wear -- check the distribution, "
                "and the head signal, before condemning the impeller"
            )

    # ------------------------------------------------------------------ the rule
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's flow-at-speed vs the frozen baseline; return a Finding."""
        caveats: list = []
        if self.flow_role not in current.columns or self.speed_role not in current.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "flow_or_speed_not_mapped"},
                summary=f"{equip}: declined -- no flow/speed pair mapped for this pump",
                caveats=[
                    "could not evaluate pump flow: both a flow and a drive-speed point must be "
                    "mapped, and this pump is missing one; a deficit can't be inferred without them"
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
                summary=f"{equip}: declined -- no frozen flow baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_r,
            metric_col=self.flow_role,
            load_col=self.speed_role,
            min_load=self.min_speed,
            metric_range=FLOW_PLAUSIBLE,
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
        direction = "down" if drift.drift_f < 0 else "up"
        rec = self.store.get(self.site, equip, _KIND)
        metrics = {
            "pump_flow_drift_gpm": drift.drift_f,
            "pump_flow_drift_sigma": drift.drift_sigma,
            "pump_flow_drift_direction": direction,
            "pump_flow_slope_gpm_per_month": drift.slope_f_per_month,
            "pump_flow_pct_outside_2sigma": drift.pct_outside_2sigma,
            "pump_flow_n_current": drift.n_current,
            "pump_flow_baseline_sigma_gpm": frozen.sigma_f,
            "pump_flow_baseline_frozen_at": rec.frozen_at if rec else "",
        }
        self._resistance_confound(base_r, cur_r, direction == "down", metrics, caveats)
        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's fitted speed envelope, "
                "so part of this drift is extrapolated"
            )

        # the same frozen baseline, folded sample-by-sample: did the deficit open and *stay* open?
        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="down",  # only a sustained flow deficit alarms
            )
            run = monitor.run(
                cur_r,
                approach_col=self.flow_role,
                tons_col=self.speed_role,
                min_tons=self.min_speed,
                approach_range=FLOW_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "pump_flow_sustained_alarm": run.alarmed,
                    "pump_flow_first_alarm_at": run.first_alarm_at,
                    "pump_flow_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        if direction == "down":
            headline = (
                f"{equip}: pump flow deficit {drift.drift_f:+.0f} gpm "
                f"({drift.drift_sigma:.1f}σ) vs frozen baseline at matched speed"
            )
        else:
            headline = (
                f"{equip}: pump flow {drift.drift_f:+.0f} gpm vs frozen baseline at matched speed "
                "(a surplus is not a fault)"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=headline,
            caveats=caveats,
        )
