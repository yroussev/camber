"""Rule: **suction- / evaporating-pressure** drift -- the low-side companion to evaporator approach.

Where :class:`camber.rules.chiller_head_pressure_rule.ChillerHeadPressureDrift` reads the high side,
this reads the **suction (evaporating) pressure** -- the low-side pressure, gauged directly. At a
matched load and a matched chilled-water temperature the evaporator's saturation pressure is a
direct read on evaporator condition: it *falls* when heat transfer degrades or the circuit runs
short of refrigerant (evaporator fouling, undercharge, a starved / restricted feed force a colder,
lower-pressure evaporator to move the same heat) and *rises* when the evaporator is overfed or
flooding. It is the pressure-domain twin of the condenser-approach ↔ head-pressure pairing, now on
the evaporator side.

Two properties shape the rule:

1. **It is two-sided, and both directions are faults** -- like liquid-line subcooling
   (:mod:`camber.rules.chiller_subcooling_rule`). A *fall* is the evaporator-degradation /
   low-charge signature; a *rise* is the overfeed / flooding one. A one-sided detector would miss
   half the fault space, so the rule scores the **magnitude** of the drift and reports its sign.
   Superheat catches overfeed by temperature; suction pressure catches it (and the far more common
   heat-transfer loss) by pressure, gauged directly.
2. **It is instrumentation-gated.** :attr:`camber.model.roles.Role.SUCTION_PRESSURE` is a raw
   pressure a chiller either publishes or does not; CAMBER models no refrigerant saturation curve,
   so it cannot be reconstructed from a temperature. The role is **optional** and the rule *declines
   with a caveat* when it is absent -- a chiller missing from a low-side report must not read as a
   chiller with a healthy evaporator.

**The confound is stated, not hidden.** Suction pressure also tracks the *chilled-water supply
temperature*: a chilled-water reset that lifts CHW supply raises the evaporating pressure with no
fault at all, and load normalization does not remove it. So the rule reports the concurrent
CHW-supply shift and **caveats** a co-moving move -- some of the change may be setpoint-driven.
The verdict stays screening-grade.

Everything else is the machinery the approach and subcooling detectors already use: the same
load-normalized fit (:mod:`camber.chillerbaseline`), the same frozen-with-provenance coefficient
store (:mod:`camber.store.modelstore`), and the same streaming CUSUM (:mod:`camber.chillerdrift`)
run two-sided. Suction pressure is load-dependent, so the comparison is made at matched load.
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
from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from .base import Finding

_ROLE_TO_COL = {
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_FLOW: "CHW_Flow",
}

_KIND = "chiller_suction_pressure"

# Plausibility bounds for the low-side pressure metric, psig -- wide and refrigerant-neutral (see
# camber.sensorhealth.PHYSICAL_BOUNDS). Passed to every fit/score/monitor call so valid suction
# pressures are not filtered out as impossible by the degF-scaled default range.
PRESSURE_PLAUSIBLE = (-15.0, 400.0)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds).
#
# Two-sided (both directions are faults), and a finding must clear BOTH a psi floor and a sigma
# floor, **applied to |drift|, symmetrically**. The floors are deliberately the *same* as the
# head-pressure rule's: suction and head pressure are the same signal class -- raw refrigerant
# pressures gauged directly -- so they share a threshold philosophy, unlike the noisier computed
# subcooling difference which floors higher in sigma. The sigma floor carries the weight: absolute
# suction pressure is refrigerant-dependent, so a fixed psi floor cannot mean the same thing across
# machines, while drift measured against the baseline's own residual scatter self-scales. All four
# are constructor arguments; they are characterized from the signal class, not established here.
#
# Asymmetric per-direction floors (a fall may deserve a tighter floor than a rise) are a plausible
# future refinement, deferred until real trended fault data can set the asymmetry -- a wrong split
# would quietly desensitize one half of the fault space, which is worse than none.
# ---------------------------------------------------------------------------------------------
SUCTION_PRESSURE_WARN_PSI = 2.0  # screening-grade, applied to |drift|
SUCTION_PRESSURE_FAULT_PSI = 4.0  # screening-grade, applied to |drift|
SUCTION_PRESSURE_WARN_SIGMA = 2.5  # screening-grade, applied to |drift|
SUCTION_PRESSURE_FAULT_SIGMA = 4.0  # screening-grade, applied to |drift|

# When CHW supply shifted at least this much (degF) alongside the suction-pressure move, flag the
# chilled-water-reset confound: part of the move may be setpoint-driven, not an evaporator fault.
CHW_CONFOUND_WARN_F = 1.0


class ChillerSuctionPressureDrift:
    """Detects suction / evaporating pressure drifting either way from a frozen baseline.

    A :class:`~camber.store.modelstore.BaselineStore` is injected so the reference survives between
    runs, which (as with the approach, subcooling and head-pressure rules) means this is **not**
    auto-registered in :func:`camber.rules.builtin.builtin_registry`; the caller instantiates and
    registers it. Run it via :meth:`camber.rules.base.Registry.run_periods`.

    Two-sided by construction: :meth:`_severity` scores ``abs(drift)`` against one pair of floors,
    so an equal fall and rise score identically, while ``suction_pressure_drift_direction`` (and the
    CUSUM's ``alarm_direction``, run ``direction="both"``) says which way it went -- a *fall* is the
    evaporator-degradation / low-charge signature, a *rise* the overfeed / flooding one. The period
    statistic and the sustained-shift alarm are reported in one Finding.

    When CHW supply shifts materially between the periods the rule reports it and caveats a
    co-moving move (the chilled-water-reset confound).
    """

    name = "chiller_suction_pressure_drift"
    roles_required = (Role.CHW_FLOW, Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP)
    roles_optional = (Role.SUCTION_PRESSURE,)

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        freeze_if_missing: bool = True,
        warn_psi: float = SUCTION_PRESSURE_WARN_PSI,  # screening-grade -- see the module note
        fault_psi: float = SUCTION_PRESSURE_FAULT_PSI,  # screening-grade
        warn_sigma: float = SUCTION_PRESSURE_WARN_SIGMA,  # screening-grade
        fault_sigma: float = SUCTION_PRESSURE_FAULT_SIGMA,  # screening-grade
        chw_confound_f: float = CHW_CONFOUND_WARN_F,
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
        self.warn_psi = warn_psi
        self.fault_psi = fault_psi
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.chw_confound_f = chw_confound_f
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.min_tons = min_tons

    # ------------------------------------------------------------------ frame prep
    def _prepared(self, frame: pd.DataFrame) -> pd.DataFrame:
        """A ``tons`` + suction-pressure + CHW-supply frame; tons derived as in camber.chiller."""
        legacy = frame.rename(columns={r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns})
        out = pd.DataFrame({"tons": tons_from_flow(legacy)}, index=frame.index)
        for role in (Role.SUCTION_PRESSURE, Role.CHW_SUPPLY_TEMP):
            if role in frame.columns:
                out[role] = frame[role]
        return out

    def _frozen_baseline(self, equip, base_frame, caveats):
        """The frozen suction-pressure baseline; freeze one from ``base_frame`` if none exists."""
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
            metric_col=Role.SUCTION_PRESSURE,
            load_col="tons",
            min_load=self.min_tons,
            metric_range=PRESSURE_PLAUSIBLE,
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
        """Two-sided severity: |drift| must clear both the psi and the sigma floor.

        Symmetric by construction -- one pair of floors, applied to the magnitude -- so a fall and
        an equal rise return the same severity. The direction is reported separately, not folded in.
        """
        mag_psi = abs(drift.drift_f)
        if drift.drift_sigma != drift.drift_sigma:  # NaN: baseline had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on psi alone")
            if mag_psi >= self.fault_psi:
                return "fault"
            return "warn" if mag_psi >= self.warn_psi else "ok"
        mag_sigma = abs(drift.drift_sigma)
        if mag_psi >= self.fault_psi and mag_sigma >= self.fault_sigma:
            return "fault"
        if mag_psi >= self.warn_psi and mag_sigma >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ confound
    def _chw_confound(self, base_t, cur_t, degrading: bool, metrics: dict, caveats: list) -> None:
        """Report the CHW-supply shift; caveat a co-moving move (the CHW-reset confound)."""
        if Role.CHW_SUPPLY_TEMP not in cur_t.columns or Role.CHW_SUPPLY_TEMP not in base_t.columns:
            return
        base_chw = pd.to_numeric(base_t[Role.CHW_SUPPLY_TEMP], errors="coerce").median()
        cur_chw = pd.to_numeric(cur_t[Role.CHW_SUPPLY_TEMP], errors="coerce").median()
        if base_chw != base_chw or cur_chw != cur_chw:  # a NaN
            return
        shift = round(float(cur_chw - base_chw), 3)
        metrics["chw_supply_shift_f"] = shift
        if degrading and abs(shift) >= self.chw_confound_f:
            caveats.append(
                f"chilled-water supply also shifted {shift:+.1f}°F over the same window; some or "
                "all of this suction-pressure move may be a chilled-water-reset effect rather than "
                "an evaporator fault -- confirm the CHW setpoint history before acting"
            )

    # ------------------------------------------------------------------ the rule
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's suction pressure vs the frozen baseline; return a Finding."""
        caveats: list = []
        if Role.SUCTION_PRESSURE not in current.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "suction_pressure_not_mapped"},
                summary=f"{equip}: declined -- no suction-pressure point mapped for this chiller",
                caveats=[
                    "could not evaluate the low side: suction pressure is a directly-reported "
                    "point and this chiller does not publish one; it cannot be derived from the "
                    "evaporator approach or chilled-water temperatures"
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
                summary=f"{equip}: declined -- no frozen suction-pressure baseline to compare",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_t,
            metric_col=Role.SUCTION_PRESSURE,
            load_col="tons",
            min_load=self.min_tons,
            metric_range=PRESSURE_PLAUSIBLE,
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
            "suction_pressure_drift_psi": drift.drift_f,
            "suction_pressure_drift_sigma": drift.drift_sigma,
            "suction_pressure_drift_direction": direction,
            "suction_pressure_slope_psi_per_month": drift.slope_f_per_month,
            "suction_pressure_pct_outside_2sigma": drift.pct_outside_2sigma,
            "suction_pressure_n_current": drift.n_current,
            "suction_pressure_baseline_sigma_psi": frozen.sigma_f,
            "suction_pressure_baseline_frozen_at": rec.frozen_at if rec else "",
        }
        self._chw_confound(base_t, cur_t, severity in ("warn", "fault"), metrics, caveats)
        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's fitted load envelope, "
                "so part of this drift is extrapolated"
            )

        # the same frozen baseline, folded sample-by-sample: did it move and *stay* moved?
        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="both",  # low-side faults move it either way
            )
            run = monitor.run(
                cur_t,
                approach_col=Role.SUCTION_PRESSURE,
                tons_col="tons",
                min_tons=self.min_tons,
                approach_range=PRESSURE_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "suction_pressure_sustained_alarm": run.alarmed,
                    "suction_pressure_first_alarm_at": run.first_alarm_at,
                    "suction_pressure_alarm_direction": run.alarm_direction,
                }
            )
        # Severity is magnitude-driven (screening-grade); the sustained-alarm metrics, when present,
        # add a temporal claim that rests on the weaker, untuned parameters -- label both.
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        arrow = "rose" if direction == "up" else "fell"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=(
                f"{equip}: suction pressure {arrow} {abs(drift.drift_f):.1f} psi "
                f"({abs(drift.drift_sigma):.1f}σ) vs frozen baseline at matched load"
            ),
            caveats=caveats,
        )
