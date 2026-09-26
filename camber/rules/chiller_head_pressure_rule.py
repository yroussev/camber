"""Rule: **head- / condensing-pressure** drift -- the high-side companion to condenser approach.

Condenser approach drift (:mod:`camber.rules.chiller_drift_rule`) reads the refrigerant-to-water
temperature difference; this rule reads the **discharge (head / condensing) pressure** itself. They
are correlated -- anything that degrades condenser heat rejection raises both -- but the pressure is
often the earlier, more sensitive number, and it is the one a mechanic actually gauges. It is worth
its own detector for two reasons:

1. **It is directly instrumented and directly actionable.** Many chillers publish a discharge
   pressure but not a computed condenser approach; the head-pressure trend is then the only
   load-normalized read on the high side. :attr:`camber.model.roles.Role.DISCHARGE_PRESSURE` is a
   *raw* pressure, not a saturation temperature -- CAMBER models no refrigerant saturation curve, so
   the pressure is trended in its own right rather than converted.
2. **It is one-sided, like approach.** The fault modes that matter -- tube fouling / scale,
   non-condensables in the circuit, a fouled or air-bound condenser, reduced CW flow -- all *raise*
   head pressure. A falling head pressure is not a high-side fault, so the detector alarms only on a
   climb (monitor default ``direction="up"``), mirroring the approach rules.

**The heat-sink confound is regressed out, not just flagged.** Condensing pressure is set first by
the temperature of what the condenser rejects to -- the *entering condenser-water temperature* on a
water-cooled machine, *outdoor air* on an air-cooled one -- and only second by load: a hot afternoon
lifts it without a speck of scale. On real plant data a load-only fit left 44-63 psi of residual
scatter that a fit on load *and* entering-water / outdoor temperature cut to 3-11 psi, turning a 44
psi blockage from a 1-sigma blip into a 4-13 sigma finding. So when ``Role.CW_SUPPLY_TEMP``
(water-cooled) or else ``Role.OAT`` (air-cooled) is mapped and varied enough in the baseline, the
baseline is ``pressure ~ load + heat-sink temperature`` and every score is made at matched load
*and* matched heat-sink temperature. Without one it falls back to load only and says so in a caveat;
the concurrent CW-supply shift is then reported and a co-moving rise caveated as before. A mapped
suction pressure adds the condensing-over-suction *lift* as further context. The verdict stays
screening-grade: it ranks a machine for a gauge-and-walkdown, it does not dispatch on its own.

Everything else is the machinery the approach and subcooling detectors already use: the same
load-normalized fit, the same frozen-with-provenance coefficient store
(:mod:`camber.store.modelstore`), and the same streaming CUSUM (:mod:`camber.chillerdrift`) run
one-sided. Head pressure is load-dependent, so the comparison is made at matched load.
"""

from __future__ import annotations

import pandas as pd

from ..chillerbaseline import tons_from_flow
from ..chillerdrift import (
    CUSUM_CLIP_SIGMA,
    CUSUM_LIMIT_SIGMA,
    CUSUM_MIN_CONSECUTIVE,
    CUSUM_SLACK_SIGMA,
    ApproachDriftMonitor,
)
from ..driftthresholds import threshold_confidence
from ..model.roles import Role
from ..sensorhealth import PHYSICAL_BOUNDS
from . import _chillerfit
from .base import Finding

_ROLE_TO_COL = {
    Role.CHW_SUPPLY_TEMP: "CHWS_Temp",
    Role.CHW_RETURN_TEMP: "CHWR_Temp",
    Role.CHW_FLOW: "CHW_Flow",
}

_KIND = "chiller_head_pressure"

# Plausibility bounds for the *pressure* metric, psig -- camber.sensorhealth.PHYSICAL_BOUNDS, one
# source. Wide enough to be genuinely refrigerant-neutral: a CO2 (R744) transcritical gas cooler
# runs ~1100-1750 psig, which the former 700 psig ceiling rejected wholesale. Only dropouts and
# sentinel codes (9999, 32767, ...) fall outside. Passed to every fit/score/monitor call so valid
# head pressures are not filtered out by the degF-scaled default range.
PRESSURE_PLAUSIBLE = PHYSICAL_BOUNDS[Role.DISCHARGE_PRESSURE]

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds).
#
# One-sided (a rise is the fault), and a finding must clear BOTH a psi floor and a sigma floor.
#
# The sigma floor carries the weight here, and deliberately so: absolute head pressure is
# refrigerant-dependent (an R-134a machine and an R-410A machine live hundreds of psi apart), so a
# fixed psi floor cannot mean the same thing across machines. The sigma floor -- drift measured
# against the baseline's *own* residual scatter -- self-scales and is refrigerant-neutral; the psi
# floor is only a coarse backstop that stops a very tight baseline from firing on a trivially small
# pressure move. All four are constructor arguments, so tuning is a config change, not a code
# change, and they are characterized from the signal class, not established on the machines here.
# ---------------------------------------------------------------------------------------------
HEAD_PRESSURE_WARN_PSI = 2.0  # screening-grade -- coarse backstop; the sigma floor does the work
HEAD_PRESSURE_FAULT_PSI = 4.0  # screening-grade
HEAD_PRESSURE_WARN_SIGMA = 2.5  # screening-grade
HEAD_PRESSURE_FAULT_SIGMA = 4.0  # screening-grade

# When a CW-supply point is mapped and it rose at least this much (degF) alongside the head-pressure
# climb, flag the ambient/heat-rejection confound: part of the rise may not be a high-side fault.
CW_CONFOUND_WARN_F = 2.0


class ChillerHeadPressureDrift:
    """Detects a chiller's head / condensing pressure drifting **up** from a frozen baseline.

    A :class:`~camber.store.modelstore.BaselineStore` is injected so the reference survives between
    runs, which (as with the approach and subcooling rules) means this is **not** auto-registered in
    :func:`camber.rules.builtin.builtin_registry`; the caller instantiates and registers it. Run it
    via :meth:`camber.rules.base.Registry.run_periods`.

    One-sided by construction: :meth:`_severity` scores the signed drift and only a *rise* clears
    the floors, and the CUSUM runs at its ``direction="up"`` default -- head-pressure faults only
    ever lift the high side. The period statistic and the sustained-shift alarm are reported in one
    Finding.

    When ``Role.CW_SUPPLY_TEMP`` is mapped the rule reports the concurrent CW-supply shift and
    caveats a co-moving rise (the ambient/heat-rejection confound); when ``Role.SUCTION_PRESSURE``
    is mapped it reports the condensing-over-suction lift shift as further context. Both are
    optional and the rule degrades gracefully when they are absent.
    """

    name = "chiller_head_pressure_drift"
    roles_required = (
        Role.DISCHARGE_PRESSURE,
        Role.CHW_FLOW,
        Role.CHW_SUPPLY_TEMP,
        Role.CHW_RETURN_TEMP,
    )
    roles_optional = (Role.CW_SUPPLY_TEMP, Role.OAT, Role.SUCTION_PRESSURE)

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        freeze_if_missing: bool = True,
        warn_psi: float = HEAD_PRESSURE_WARN_PSI,  # screening-grade -- see the module note
        fault_psi: float = HEAD_PRESSURE_FAULT_PSI,  # screening-grade
        warn_sigma: float = HEAD_PRESSURE_WARN_SIGMA,  # screening-grade
        fault_sigma: float = HEAD_PRESSURE_FAULT_SIGMA,  # screening-grade
        cw_confound_f: float = CW_CONFOUND_WARN_F,
        normalize_on_condition: bool = True,
        min_covariate_span_f: float = _chillerfit.COVARIATE_MIN_SPAN_F,
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_tons: float | None = None,  # None = size-relative (camber.chillerbaseline)
        min_tons_span: float | None = None,  # None = size-relative
    ):
        self.store = store
        self.site = site
        self.run_id = run_id
        self.freeze_if_missing = freeze_if_missing
        self.warn_psi = warn_psi
        self.fault_psi = fault_psi
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.cw_confound_f = cw_confound_f
        self.normalize_on_condition = normalize_on_condition
        self.min_covariate_span_f = min_covariate_span_f
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.min_tons = min_tons
        self.min_tons_span = min_tons_span

    # ------------------------------------------------------------------ frame prep
    def _prepared(self, frame: pd.DataFrame) -> pd.DataFrame:
        """A ``tons`` + pressure (+ optional CW-supply / suction) frame; tons as in chiller."""
        legacy = frame.rename(columns={r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns})
        out = pd.DataFrame({"tons": tons_from_flow(legacy)}, index=frame.index)
        for role in (
            Role.DISCHARGE_PRESSURE,
            Role.CW_SUPPLY_TEMP,
            Role.OAT,
            Role.SUCTION_PRESSURE,
        ):
            if role in frame.columns:
                out[role] = frame[role]
        return out

    def _frozen_baseline(self, equip, base_frame, caveats, gate=None):
        """The frozen head-pressure baseline; freeze an initial one from ``base_frame`` if none."""
        return _chillerfit.freeze_baseline(
            self,
            equip,
            base_frame,
            caveats,
            kind=_KIND,
            metric_col=Role.DISCHARGE_PRESSURE,
            metric_range=PRESSURE_PLAUSIBLE,
            gate=gate or _chillerfit.gates(base_frame, self.min_tons, self.min_tons_span),
            metric_name="head pressure",
            covariates=self._covariates(),
            min_covariate_span=self.min_covariate_span_f,
        )

    # ------------------------------------------------------------------ severity
    def _severity(self, drift, caveats) -> str:
        """One-sided severity: only a *rise* clears the floors, on both psi and sigma.

        A negative drift (head pressure fell vs baseline) is not a fault; it scores ``ok``.
        """
        if drift.drift_sigma != drift.drift_sigma:  # NaN: the baseline fit had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on psi alone")
            if drift.drift_f >= self.fault_psi:
                return "fault"
            return "warn" if drift.drift_f >= self.warn_psi else "ok"
        if drift.drift_f >= self.fault_psi and drift.drift_sigma >= self.fault_sigma:
            return "fault"
        if drift.drift_f >= self.warn_psi and drift.drift_sigma >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ confounds
    def _covariates(self) -> tuple:
        """The heat-sink temperatures head pressure is regressed on, in preference order.

        Condensing pressure is set by the temperature of the medium the condenser rejects to:
        entering condenser water on a water-cooled machine, outdoor air on an air-cooled one.
        """
        return (Role.CW_SUPPLY_TEMP, Role.OAT) if self.normalize_on_condition else ()

    def _confound_signals(
        self, base_t, cur_t, rising: bool, metrics: dict, caveats: list, frozen=None
    ) -> None:
        """Report the CW-supply and suction-pressure confound context, mutating metrics/caveats.

        Load normalization removes the tons confound but not the entering-CW-temperature one, so a
        co-moving CW-supply rise is surfaced rather than silently attributed to a high-side fault.
        """
        if Role.CW_SUPPLY_TEMP in cur_t.columns and Role.CW_SUPPLY_TEMP in base_t.columns:
            base_cw = pd.to_numeric(base_t[Role.CW_SUPPLY_TEMP], errors="coerce").median()
            cur_cw = pd.to_numeric(cur_t[Role.CW_SUPPLY_TEMP], errors="coerce").median()
            if base_cw == base_cw and cur_cw == cur_cw:  # both non-NaN
                shift = round(float(cur_cw - base_cw), 3)
                metrics["cw_supply_shift_f"] = shift
                regressed = getattr(frozen, "covariate", "") == Role.CW_SUPPLY_TEMP.value
                if rising and shift >= self.cw_confound_f and not regressed:
                    caveats.append(
                        f"entering condenser-water supply also rose {shift:+.1f}°F over the same "
                        "window; some or all of this head-pressure climb may be heat-rejection / "
                        "ambient-driven rather than a high-side fault -- gauge and corroborate "
                        "before attributing it to fouling or non-condensables"
                    )

        if Role.SUCTION_PRESSURE in cur_t.columns and Role.SUCTION_PRESSURE in base_t.columns:
            b_dis = pd.to_numeric(base_t[Role.DISCHARGE_PRESSURE], errors="coerce").median()
            c_dis = pd.to_numeric(cur_t[Role.DISCHARGE_PRESSURE], errors="coerce").median()
            b_suc = pd.to_numeric(base_t[Role.SUCTION_PRESSURE], errors="coerce").median()
            c_suc = pd.to_numeric(cur_t[Role.SUCTION_PRESSURE], errors="coerce").median()
            if all(v == v for v in (b_dis, c_dis, b_suc, c_suc)):
                metrics["suction_pressure_shift_psi"] = round(float(c_suc - b_suc), 3)
                metrics["lift_shift_psi"] = round(float((c_dis - c_suc) - (b_dis - b_suc)), 3)

    # ------------------------------------------------------------------ the rule
    # ------------------------------------------------------------------ pattern J evidence
    def drift_signature(self):
        """The frozen-model kind and the (load, metric) columns the baseline is fitted on."""
        return _KIND, "tons", Role.DISCHARGE_PRESSURE

    def drift_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """The prepared frame the baseline is fitted on (head pressure at matched load)."""
        return self._prepared(frame)

    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's head pressure vs the frozen baseline; return a Finding."""
        caveats: list = []
        if Role.DISCHARGE_PRESSURE not in current.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "discharge_pressure_not_mapped"},
                summary=f"{equip}: declined -- no discharge-pressure point mapped for this chiller",
                caveats=[
                    "could not evaluate the high side: discharge pressure is a directly-reported "
                    "point and this chiller does not publish one; it cannot be derived from the "
                    "condenser approach or water temperatures"
                ],
            )

        base_t, cur_t = self._prepared(baseline), self._prepared(current)
        gate = _chillerfit.gates(base_t, self.min_tons, self.min_tons_span)
        frozen = self._frozen_baseline(equip, base_t, caveats, gate)
        if frozen is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no frozen head-pressure baseline to compare against",
                caveats=caveats,
            )

        drift = _chillerfit.score(
            frozen,
            cur_t,
            caveats,
            kind=_KIND,
            metric_col=Role.DISCHARGE_PRESSURE,
            metric_range=PRESSURE_PLAUSIBLE,
            gate=gate,
            metric_name="head pressure",
        )
        if drift is None:
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
            "head_pressure_drift_psi": drift.drift_f,
            "head_pressure_drift_sigma": drift.drift_sigma,
            "head_pressure_drift_direction": direction,
            "head_pressure_slope_psi_per_month": drift.slope_f_per_month,
            "head_pressure_pct_outside_2sigma": drift.pct_outside_2sigma,
            "head_pressure_n_current": drift.n_current,
            "head_pressure_baseline_sigma_psi": frozen.sigma_f,
            "head_pressure_baseline_frozen_at": rec.frozen_at if rec else "",
        }
        self._confound_signals(
            base_t,
            cur_t,
            direction == "up",
            metrics,
            caveats,
            None if drift.covariate_extrapolated else frozen,
        )
        if not getattr(frozen, "covariate", ""):
            caveats.append(
                "head pressure is normalized on load only -- no entering condenser-water or "
                "outdoor-air temperature was usable as a second regressor, so a heat-sink "
                "(ambient / tower) rise is not separated from a high-side fault"
            )
        metrics["head_pressure_min_tons"] = gate[0]
        _chillerfit.fit_notes(frozen, caveats, metrics, "head_pressure")
        _chillerfit.envelope_caveats(drift, caveats, frozen)

        # the same frozen baseline, folded sample-by-sample: did it climb and *stay* climbed?
        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                # one-sided: a head-pressure fault only ever lifts the high side (monitor default)
            )
            run = monitor.run(
                cur_t,
                approach_col=Role.DISCHARGE_PRESSURE,
                tons_col="tons",
                min_tons=gate[0],
                approach_range=PRESSURE_PLAUSIBLE,
                covariate_col=_chillerfit.covariate_key(frozen),
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "head_pressure_sustained_alarm": run.alarmed,
                    "head_pressure_first_alarm_at": run.first_alarm_at,
                    "head_pressure_alarm_direction": run.alarm_direction,
                }
            )
        # Severity is magnitude-driven (screening-grade); the sustained-alarm metrics, when present,
        # add a temporal claim that rests on the weaker, untuned parameters -- label both.
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        if direction == "up":
            headline = (
                f"{equip}: head pressure climbed {drift.drift_f:+.1f} psi "
                f"({drift.drift_sigma:.1f}σ) vs frozen baseline at {_chillerfit.matched(frozen)}"
            )
        else:
            headline = (
                f"{equip}: head pressure {drift.drift_f:+.1f} psi vs frozen baseline at "
                f"{_chillerfit.matched(frozen)} (a fall is not a high-side fault)"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=headline,
            caveats=caveats,
        )
