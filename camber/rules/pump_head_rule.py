"""Rule: pump **head-at-matched-speed** drift -- the direct pump-condition detector.

Flow-at-speed (:mod:`camber.rules.pump_flow_rule`) catches a pump moving less water than it used to,
but a flow deficit is ambiguous between the pump itself and a more restrictive system. **Head** --
the differential pressure the pump develops across itself -- is the less ambiguous read: by the
affinity laws a healthy pump develops a head set by its speed (H ∝ N²), and when it develops **less
head at the same speed** the pump itself has degraded (worn impeller / wear-ring, cavitation,
internal recirculation). Paired with flow, head is what disambiguates pump-wear from resistance:
flow↓ **and** head↓ → the pump; flow↓ with head steady → the distribution. This rule freezes a
load-normalized ``head ~ f(speed)`` baseline and scores the current period's head deficit at matched
speed, one-sided **down** (a head surplus is not a fault).

**It is instrumentation-gated.** A per-pump head/ΔP point is less commonly trended than speed, so
:attr:`camber.model.roles.Role.PUMP_HEAD` is **optional** and the rule *declines with a caveat* when
it is absent -- a pump missing from a head report must not read as a healthy pump.

**The confound is stated, not hidden.** Head also falls as the operating point rides *down the pump
curve* when flow rises (higher flow, lower head) -- with no wear at all. Normalizing on speed does
not remove it, so when a flow point is mapped the rule reports the concurrent flow shift and
**caveats** a head deficit that co-moves with a flow *increase*. Absolute head is
pump-size-dependent, so (as with the pressure detectors) the sigma floor carries the weight and the
psi floor is only a coarse backstop.

Loop-parameterized like the flow detector; **not** auto-registered (needs an injected
``BaselineStore``); run via :meth:`camber.rules.base.Registry.run_periods`.
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

_KIND = "pump_head"

# Plausibility bounds for the head metric (psi) -- wide, only rejecting dropouts / bad values.
HEAD_PLAUSIBLE = (-5.0, 300.0)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds). One-sided DOWN (a head deficit
# is the fault); both a psi floor and a sigma floor must be cleared. The sigma floor carries the
# weight (absolute head is pump-size-dependent); the psi floor is a coarse backstop. All are args.
# ---------------------------------------------------------------------------------------------
HEAD_WARN_PSI = 2.0  # screening-grade -- coarse backstop
HEAD_FAULT_PSI = 4.0  # screening-grade
HEAD_WARN_SIGMA = 2.5  # screening-grade
HEAD_FAULT_SIGMA = 4.0  # screening-grade

# Below this drive speed (%) the pump carries no condition information -- the analog of min_tons.
MIN_SPEED = 15.0

# A co-moving flow rise of at least this much (gpm) alongside the head deficit flags the
# operating-point confound (head fell riding down the curve, not from wear).
FLOW_CONFOUND = 20.0


class PumpHeadDrift:
    """Detects a pump's head-at-matched-speed drifting **down** from a frozen baseline.

    Defaults to the chilled-water loop; pass ``speed_role`` / ``head_role`` / ``flow_role`` to point
    it at a hot-water loop. Instrumentation-gated: declines when ``head_role`` is unmapped. A
    ``BaselineStore`` is injected (so, as with the chiller drift rules, it is **not**
    auto-registered).
    """

    name = "pump_head_drift"

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        speed_role: Role = Role.CHW_PUMP_SPEED,
        head_role: Role = Role.PUMP_HEAD,
        flow_role: Role = Role.CHW_FLOW,
        status_role: Role = Role.PUMP_STATUS,
        freeze_if_missing: bool = True,
        warn_psi: float = HEAD_WARN_PSI,  # screening-grade -- see the module note
        fault_psi: float = HEAD_FAULT_PSI,  # screening-grade
        warn_sigma: float = HEAD_WARN_SIGMA,  # screening-grade
        fault_sigma: float = HEAD_FAULT_SIGMA,  # screening-grade
        flow_confound: float = FLOW_CONFOUND,
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_speed: float = MIN_SPEED,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.speed_role = speed_role
        self.head_role = head_role
        self.flow_role = flow_role
        self.status_role = status_role
        self.roles_required = (speed_role,)
        self.roles_optional = (head_role, flow_role, status_role)
        self.freeze_if_missing = freeze_if_missing
        self.warn_psi = warn_psi
        self.fault_psi = fault_psi
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.flow_confound = flow_confound
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
        """The frozen head~speed baseline, freezing an initial one from ``base_frame`` if none."""
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
            metric_col=self.head_role,
            load_col=self.speed_role,
            min_load=self.min_speed,
            metric_range=HEAD_PLAUSIBLE,
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
        """One-sided-DOWN severity: only a head *deficit* clears the floors (both psi and sigma)."""
        if drift.drift_sigma != drift.drift_sigma:  # NaN: baseline had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on psi alone")
            if drift.drift_f <= -self.fault_psi:
                return "fault"
            return "warn" if drift.drift_f <= -self.warn_psi else "ok"
        if drift.drift_f <= -self.fault_psi and drift.drift_sigma <= -self.fault_sigma:
            return "fault"
        if drift.drift_f <= -self.warn_psi and drift.drift_sigma <= -self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ confound
    def _flow_confound(self, base, cur, deficit: bool, metrics: dict, caveats: list) -> None:
        """Report the flow shift; caveat a head deficit that co-moves with a flow rise (curve)."""
        if self.flow_role not in cur.columns or self.flow_role not in base.columns:
            return
        base_q = pd.to_numeric(base[self.flow_role], errors="coerce").median()
        cur_q = pd.to_numeric(cur[self.flow_role], errors="coerce").median()
        if base_q != base_q or cur_q != cur_q:  # a NaN
            return
        shift = round(float(cur_q - base_q), 3)
        metrics["flow_shift"] = shift
        if deficit and shift >= self.flow_confound:
            caveats.append(
                f"loop flow also rose {shift:+.0f} gpm over the same window; head falls as the "
                "operating point rides down the pump curve at higher flow, so part of this head "
                "deficit may be that, not wear -- read it together with the flow-at-speed signal"
            )

    # ------------------------------------------------------------------ the rule
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's head-at-speed vs the frozen baseline; return a Finding."""
        caveats: list = []
        if self.head_role not in current.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "pump_head_not_mapped"},
                summary=f"{equip}: declined -- no pump-head point mapped for this pump",
                caveats=[
                    "could not evaluate pump head: differential head is a directly-reported point "
                    "and this pump does not publish one; it cannot be inferred from flow or speed"
                ],
            )
        if self.speed_role not in current.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "speed_not_mapped"},
                summary=f"{equip}: declined -- no drive-speed point mapped for this pump",
                caveats=["could not evaluate pump head: no speed point to normalize against"],
            )

        base_r, cur_r = self._running(baseline), self._running(current)
        frozen = self._frozen_baseline(equip, base_r, caveats)
        if frozen is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no frozen head baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_r,
            metric_col=self.head_role,
            load_col=self.speed_role,
            min_load=self.min_speed,
            metric_range=HEAD_PLAUSIBLE,
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
            "pump_head_drift_psi": drift.drift_f,
            "pump_head_drift_sigma": drift.drift_sigma,
            "pump_head_drift_direction": direction,
            "pump_head_slope_psi_per_month": drift.slope_f_per_month,
            "pump_head_pct_outside_2sigma": drift.pct_outside_2sigma,
            "pump_head_n_current": drift.n_current,
            "pump_head_baseline_sigma_psi": frozen.sigma_f,
            "pump_head_baseline_frozen_at": rec.frozen_at if rec else "",
        }
        self._flow_confound(base_r, cur_r, direction == "down", metrics, caveats)
        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's fitted speed envelope, "
                "so part of this drift is extrapolated"
            )

        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="down",  # only a sustained head deficit alarms
            )
            run = monitor.run(
                cur_r,
                approach_col=self.head_role,
                tons_col=self.speed_role,
                min_tons=self.min_speed,
                approach_range=HEAD_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "pump_head_sustained_alarm": run.alarmed,
                    "pump_head_first_alarm_at": run.first_alarm_at,
                    "pump_head_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        if direction == "down":
            headline = (
                f"{equip}: pump head deficit {drift.drift_f:+.1f} psi "
                f"({drift.drift_sigma:.1f}σ) vs frozen baseline at matched speed"
            )
        else:
            headline = (
                f"{equip}: pump head {drift.drift_f:+.1f} psi vs frozen baseline at matched speed "
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
