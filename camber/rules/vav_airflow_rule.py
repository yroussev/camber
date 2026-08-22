"""Rule: VAV terminal **airflow-tracking** drift -- damper creep at matched commanded airflow.

A VAV box modulates its damper to hold a commanded airflow setpoint. As the actuator or linkage
wears, or the box is progressively starved of upstream duct static, the box spends its **reserve
damper authority** -- the damper creeps *further open* to keep delivering the same commanded flow.
Airflow still tracks, so :class:`camber.rules.airflow_rule.AirflowTracking` (the instantaneous
undershoot check) sees nothing; only once the damper saturates near 100% does flow finally fall
short and that level check fires. It catches the creep first -- the **leading** indicator to that
**lagging** one (the terminal-box analog of coil-valve-creep → SAT control).

**The signal is the damper position; the load is the commanded airflow.** Freezing a
``damper ~ f(airflow_sp)`` baseline and scoring the current period's damper residual **at matched
command** isolates the box's flow authority: the commanded airflow is exogenous zone thermal demand
(Guideline-36 maps the zone cooling loop to a flow between min and max), while the damper is the
box's endogenous mechanical response. It is **one-sided up** -- a damper needing *more* opening for
the same commanded flow is authority loss / starvation; needing *less* is authority *gain* (a
serviced actuator, a freed linkage, higher upstream static), not a fault.

**The airflow-setpoint confound is neutralized by construction, not subtracted.** A VAV setpoint
moves constantly (dual-max, zone demand, reset) -- but here the setpoint **is the load axis**, so a
changing setpoint just walks the box along the same frozen ``damper ~ f(command)`` curve and scores
~0 residual. This differs from :mod:`camber.rules.duct_static_rule`, where the confounder (static
setpoint) is in the metric's own unit and shifts it *additively*, so residual subtraction is right;
here the confounder is the x-axis, so the matched-command geometry does the work. (The only residual
risk is extrapolation, when the current period's commands run entirely outside the baseline's fitted
envelope -- reported via the standard extrapolation caveat.)

**Upstream starvation is surfaced, not diagnosed.** A damper can creep because *its own* actuator is
failing **or** because *upstream duct static is low* (an AHU/fan problem). When a building-level
``DUCT_STATIC`` point is mapped (via the runner's ``shared`` channel), the rule reports the
concurrent static shift and caveats a creep co-moving with a static *fall* -- it never blames the
box for a plant problem. Resolving that ambiguity is the future ``diagnose_vav_drift``'s job; this
detector stays screening-grade and only surfaces the signal.

Reuses only existing roles (``DAMPER`` / ``AIRFLOW_SP``; ``load_role=AIRFLOW`` is a constructor arg
for boxes without a mapped setpoint). Declines loudly when the damper or the command is unmapped, or
when the command never sweeps a usable range. **Not** auto-registered (needs an injected
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

_KIND = "vav_damper"
_METRIC = "vav_damper_pct"  # the damper column, renamed to a stable key for the fit
_LOAD = "vav_command_cfm"  # the commanded airflow the baseline is fitted against

# Plausibility bounds: the damper (metric) is 0-100%; the command (load) is airflow in cfm.
DAMPER_PLAUSIBLE = (0.0, 100.0)
COMMAND_PLAUSIBLE = (0.0, 100000.0)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds). One-sided UP (creep is the
# fault); both a %-of-damper floor and a sigma floor must be cleared. The sigma floor carries the
# weight (a box's damper-vs-command slope varies with box size/authority); the % floor is a coarse
# backstop. Copied from CoilValveDrift -- damper % and valve % share a magnitude class.
# ---------------------------------------------------------------------------------------------
DAMPER_WARN_PCT = 8.0  # screening-grade -- coarse backstop
DAMPER_FAULT_PCT = 15.0  # screening-grade
DAMPER_WARN_SIGMA = 2.5  # screening-grade
DAMPER_FAULT_SIGMA = 4.0  # screening-grade

# Below this commanded airflow (cfm) the box is at min/shutoff and the damper carries no condition
# information; those samples are dropped.
MIN_COMMAND_CFM = 50.0
# An upstream duct-static fall of at least this much (inH2O) triggers the starvation caveat.
DUCT_STATIC_CONFOUND = 0.1


class VavAirflowDrift:
    """Detects a VAV box's damper creeping open at matched commanded airflow (flow-authority loss).

    Freezes a ``damper ~ f(commanded airflow)`` baseline and scores the current period's damper
    residual at matched command; **one-sided up** (a creep is the fault). ``load_role`` may be
    :attr:`camber.model.roles.Role.AIRFLOW` (delivered airflow) for boxes without a mapped setpoint.
    A ``BaselineStore`` is injected, so (like the other drift rules) it is **not** auto-registered.
    """

    name = "vav_airflow_drift"

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        damper_role: Role = Role.DAMPER,
        load_role: Role = Role.AIRFLOW_SP,
        duct_static_role: Role = Role.DUCT_STATIC,
        status_role: Role = Role.SUPPLY_FAN_STATUS,
        freeze_if_missing: bool = True,
        warn_pct: float = DAMPER_WARN_PCT,  # screening-grade -- see the module note
        fault_pct: float = DAMPER_FAULT_PCT,  # screening-grade
        warn_sigma: float = DAMPER_WARN_SIGMA,  # screening-grade
        fault_sigma: float = DAMPER_FAULT_SIGMA,  # screening-grade
        duct_static_confound: float = DUCT_STATIC_CONFOUND,
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_command: float = MIN_COMMAND_CFM,
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.damper_role = damper_role
        self.load_role = load_role
        self.duct_static_role = duct_static_role
        self.status_role = status_role
        self._load_basis = "airflow_sp" if load_role == Role.AIRFLOW_SP else "airflow"
        self.roles_required = (damper_role, load_role)
        self.roles_optional = (duct_static_role, status_role, Role.AIRFLOW)
        self.freeze_if_missing = freeze_if_missing
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.duct_static_confound = duct_static_confound
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.min_command = min_command

    # ------------------------------------------------------------------ frame prep
    def _prepared(self, frame: pd.DataFrame):
        """A ``vav_damper_pct`` + ``vav_command_cfm`` frame, masked to active samples.

        Returns ``(prepared, inactive_excluded_fraction)``.
        """
        damper = pd.to_numeric(frame[self.damper_role], errors="coerce")
        command = pd.to_numeric(frame[self.load_role], errors="coerce")
        out = pd.DataFrame({_METRIC: damper, _LOAD: command}, index=frame.index)

        keep = pd.Series(True, index=frame.index)
        if self.status_role in frame.columns:
            keep &= pd.to_numeric(frame[self.status_role], errors="coerce") >= 0.5
        keep &= out[_LOAD] >= self.min_command  # box actually delivering, not at min/shutoff
        keep &= out[_METRIC].between(*DAMPER_PLAUSIBLE)
        keep &= out[_LOAD].between(*COMMAND_PLAUSIBLE)

        n = len(frame)
        excluded = float((~keep).sum()) / n if n else 0.0
        return out[keep], round(excluded, 4)

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
            metric_col=_METRIC,
            load_col=_LOAD,
            min_load=self.min_command,
            metric_range=DAMPER_PLAUSIBLE,
        )
        if fit is None:
            caveats.append(
                f"could not evaluate {_KIND}: the baseline would not support a fit -- too few "
                "active samples, or the commanded airflow never sweeps a usable range"
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
        """One-sided-UP severity: only damper *creep* clears the floors (both % and sigma)."""
        if drift.drift_sigma != drift.drift_sigma:  # NaN: baseline had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on damper % alone")
            if drift.drift_f >= self.fault_pct:
                return "fault"
            return "warn" if drift.drift_f >= self.warn_pct else "ok"
        if drift.drift_f >= self.fault_pct and drift.drift_sigma >= self.fault_sigma:
            return "fault"
        if drift.drift_f >= self.warn_pct and drift.drift_sigma >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ confound
    def _duct_static_confound(self, base, cur, creep: bool, metrics: dict, caveats: list) -> None:
        """Report the upstream duct-static shift; caveat a creep co-moving with a static fall."""
        if self.duct_static_role not in cur.columns or self.duct_static_role not in base.columns:
            return
        base_s = pd.to_numeric(base[self.duct_static_role], errors="coerce").median()
        cur_s = pd.to_numeric(cur[self.duct_static_role], errors="coerce").median()
        if base_s != base_s or cur_s != cur_s:  # a NaN
            return
        shift = round(float(cur_s - base_s), 4)
        metrics["vav_duct_static_shift_inwc"] = shift
        if creep and shift <= -self.duct_static_confound:
            metrics["vav_upstream_starvation_suspected"] = True
            caveats.append(
                f"upstream duct static fell {shift:+.2f} inH2O over the same window; that alone "
                "forces the damper further open for the same commanded flow, so part of this creep "
                "may be plant-side starvation, not a box actuator/linkage fault -- check the "
                "AHU"
            )

    # ------------------------------------------------------------------ the rule
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's damper-at-command vs the frozen baseline; return a Finding."""
        caveats: list = []
        missing = [r.value for r in self.roles_required if r not in current.columns]
        if missing:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "vav_damper_inputs_not_mapped"},
                summary=f"{equip}: declined -- VAV airflow drift needs damper + commanded airflow",
                caveats=[
                    "could not evaluate the VAV box: its damper position and a commanded airflow "
                    f"(or delivered airflow) must both be mapped; missing {', '.join(missing)}"
                ],
            )

        base_t, base_excl = self._prepared(baseline)
        cur_t, cur_excl = self._prepared(current)

        frozen = self._frozen_baseline(equip, base_t, caveats)
        if frozen is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no frozen VAV damper baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_t,
            metric_col=_METRIC,
            load_col=_LOAD,
            min_load=self.min_command,
            metric_range=DAMPER_PLAUSIBLE,
        )
        if drift is None:
            caveats.append(f"could not evaluate {_KIND}: no active samples in the current period")
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
            "vav_airflow_drift_pct": drift.drift_f,
            "vav_airflow_drift_sigma": drift.drift_sigma,
            "vav_airflow_drift_direction": direction,
            "vav_airflow_slope_pct_per_month": drift.slope_f_per_month,
            "vav_airflow_pct_outside_2sigma": drift.pct_outside_2sigma,
            "vav_airflow_n_current": drift.n_current,
            "vav_airflow_baseline_sigma_pct": frozen.sigma_f,
            "vav_airflow_baseline_frozen_at": rec.frozen_at if rec else "",
            "vav_airflow_which": "damper_authority",  # locus label for the AHU/VAV diagnosis
            "vav_airflow_load_basis": self._load_basis,  # "airflow_sp" | "airflow"
            "vav_command_median_cfm": round(float(cur_t[_LOAD].median()), 2)
            if len(cur_t)
            else None,
            "vav_airflow_inactive_excluded_pct": round(100.0 * max(base_excl, cur_excl), 2),
        }
        self._duct_static_confound(baseline, current, direction == "up", metrics, caveats)
        if drift.extrapolated:
            caveats.append(
                "over 10% of the period ran outside the baseline's fitted command envelope, "
                "so part of this drift is extrapolated"
            )

        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="up",  # only a sustained damper creep alarms
            )
            run = monitor.run(
                cur_t,
                approach_col=_METRIC,
                tons_col=_LOAD,
                min_tons=self.min_command,
                approach_range=DAMPER_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "vav_airflow_sustained_alarm": run.alarmed,
                    "vav_airflow_first_alarm_at": run.first_alarm_at,
                    "vav_airflow_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        if direction == "up":
            headline = (
                f"{equip}: VAV damper creep {drift.drift_f:+.0f}% ({drift.drift_sigma:.1f}σ) vs "
                "frozen baseline at matched command -- actuator/linkage authority loss or upstream "
                "starvation (airflow will fall short before airflow_tracking flags it)"
            )
        else:
            headline = (
                f"{equip}: VAV damper {drift.drift_f:+.0f}% vs frozen baseline at matched command "
                "(less damper is not a fault)"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=headline,
            caveats=caveats,
        )
