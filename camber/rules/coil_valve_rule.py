"""Rule: coil **heat-transfer** drift -- valve-position creep at matched delivered air-ΔT.

A cooling or heating coil that is losing heat-transfer capacity -- fouling, waterside starvation
(low flow / low waterside ΔT), air bypass, or valve-authority loss -- does not usually announce
itself by
missing its supply-air setpoint. The controller hides it: the coil still holds SAT, but the valve
opens **further and further** to do so. This rule catches that creep, weeks before the coil finally
runs out of valve and :mod:`camber.rules.satcontrol_rule` sees an off-setpoint failure -- the drift
detector is the *leading* indicator to that level check's *lagging* one.

**The signal is the valve position; the load is the coil's delivered air-ΔT** (cooling: MIXED_AIR −
SUPPLY_AIR; heating: SUPPLY_AIR − MIXED_AIR, kept positive by the warm/cool-role convention). The
choice is deliberate and non-circular: under good control SAT ≈ its setpoint, so the ΔT ≈
MIXED_AIR − SAT_setpoint is an **exogenous, weather-driven demand** independent of coil condition,
while the valve is the **endogenous** response the controller uses to meet it. Freezing a
``valve ~ f(ΔT)`` baseline and scoring the current period's valve residual **at matched ΔT**
isolates the coil's transfer function: fouling raises valve-at-matched-ΔT, weather unchanged.
(Using a ΔT *across* the coil rather than absolute temps also cancels common-mode bias -- Sellers,
*Relative Accuracy*.) It is **one-sided up**: a valve *fall* for the same ΔT is a capacity gain
(a cleaned coil, colder/greater flow), not a fault.

**Economizer samples are gated out, not caveated.** When an air-side economizer provides free
cooling, the cooling valve is driven by mixed-air control, not coil demand, so those samples corrupt
the valve↔ΔT relationship; rows with the economizer enabled (or the OA damper economizing) are
excluded before the fit. **The waterside-reset confound is a caveat:** colder chilled water (or
hotter hot water) needs *less* valve for the same ΔT, so a waterside supply-temp reset can move the
valve for reasons other than fouling -- when a waterside supply-temp point is mapped the rule
reports the shift and caveats a valve creep that co-moves with it. (Valve % and water °F are
different units, so this is a caveat, not the residual-subtraction the duct-static reset uses.)

**Known screening limitation:** the air-ΔT is *sensible only*. On a humid day a cooling coil does
latent work the ΔT does not show, so valve-per-sensible-ΔT rises with humidity and can mimic
fouling. Heating coils have no latent term and are cleaner. Read a cooling-coil creep against the
season.

Coil-parameterized (a cooling coil and a heating coil on one AHU freeze under distinct model kinds
so they never collide). Reuses only existing roles. Declines loudly when the valve or the
air-temperature pair is unmapped. **Not** auto-registered (needs an injected ``BaselineStore``); via
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

_METRIC = "coil_valve_pct"  # the valve column, renamed to a stable key for the fit
_DELTAT = "coil_deltat_f"  # the derived air-ΔT the baseline is fitted against

# Plausibility bounds: the valve (metric) is a 0-100% position; the ΔT (load) is a real coil split.
VALVE_PLAUSIBLE = (0.0, 100.0)
DELTAT_PLAUSIBLE = (0.5, 60.0)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds). One-sided UP (valve creep is the
# fault); both a %-of-valve floor and a sigma floor must be cleared. The sigma floor carries the
# weight (a coil's valve-vs-ΔT slope varies with valve size/authority); the % floor is a coarse
# backstop. Constructor args; characterized from the signal class, not established on the coils.
# ---------------------------------------------------------------------------------------------
VALVE_WARN_PCT = 8.0  # screening-grade -- coarse backstop
VALVE_FAULT_PCT = 15.0  # screening-grade
VALVE_WARN_SIGMA = 2.5  # screening-grade
VALVE_FAULT_SIGMA = 4.0  # screening-grade

# Below this delivered ΔT (degF) the coil is barely working; the valve carries no condition info.
MIN_DELTAT = 3.0
# Below this valve opening (%) the coil is effectively off (and, for cooling, likely economizing).
VALVE_DEADBAND = 5.0
# An OA damper above this (%) is treated as economizing (cooling coil only).
ECON_DAMPER_OPEN = 25.0
# A waterside supply-temp shift of at least this much (degF) triggers the reset caveat.
WATER_CONFOUND = 2.0

_COILS = {
    "cooling": (
        Role.COOL_VALVE,
        Role.MIXED_AIR_TEMP,
        Role.SUPPLY_AIR_TEMP,
        Role.CHW_SUPPLY_TEMP,
        "coil_valve_cool",
        +1.0,
        "cooling",
    ),
    "heating": (
        Role.HEAT_VALVE,
        Role.SUPPLY_AIR_TEMP,
        Role.MIXED_AIR_TEMP,
        Role.HW_SUPPLY_TEMP,
        "coil_valve_heat",
        -1.0,
        "heating",
    ),
}


class CoilValveDrift:
    """Detects a coil's valve creeping open at matched delivered air-ΔT (heat-transfer loss).

    ``coil`` (``"cooling"`` | ``"heating"``) picks the default valve / warm / cool / waterside roles
    and the model kind; override any ``*_role`` to match a site's mapping. A ``BaselineStore`` is
    injected, so (as with the other drift rules) it is **not** auto-registered.
    """

    name = "coil_valve_drift"

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        coil: str = "cooling",
        valve_role: Role | None = None,
        warm_role: Role | None = None,
        cool_role: Role | None = None,
        water_supply_role: Role | None = None,
        econ_role: Role = Role.ECON_CMD,
        damper_role: Role = Role.OA_DAMPER,
        status_role: Role = Role.SUPPLY_FAN_STATUS,
        freeze_if_missing: bool = True,
        warn_pct: float = VALVE_WARN_PCT,  # screening-grade -- see the module note
        fault_pct: float = VALVE_FAULT_PCT,  # screening-grade
        warn_sigma: float = VALVE_WARN_SIGMA,  # screening-grade
        fault_sigma: float = VALVE_FAULT_SIGMA,  # screening-grade
        water_confound: float = WATER_CONFOUND,
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
        min_deltat: float = MIN_DELTAT,
        valve_deadband: float = VALVE_DEADBAND,
        econ_damper_open: float = ECON_DAMPER_OPEN,
    ):
        if coil not in _COILS:
            raise ValueError(f"coil must be 'cooling' or 'heating', got {coil!r}")
        d_valve, d_warm, d_cool, d_water, kind, water_sign, label = _COILS[coil]
        self.store = store
        self.site = site
        self.run_id = run_id
        self.coil = coil
        self._kind = kind
        self._water_sign = water_sign  # +1 cooling (rising water confounds), -1 heating (falling)
        self._label = label
        self.valve_role = valve_role or d_valve
        self.warm_role = warm_role or d_warm
        self.cool_role = cool_role or d_cool
        self.water_supply_role = water_supply_role or d_water
        self.econ_role = econ_role
        self.damper_role = damper_role
        self.status_role = status_role
        self.roles_required = (self.valve_role, self.warm_role, self.cool_role)
        self.roles_optional = (
            self.water_supply_role,
            econ_role,
            damper_role,
            status_role,
        )
        self.freeze_if_missing = freeze_if_missing
        self.warn_pct = warn_pct
        self.fault_pct = fault_pct
        self.warn_sigma = warn_sigma
        self.fault_sigma = fault_sigma
        self.water_confound = water_confound
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive
        self.min_deltat = min_deltat
        self.valve_deadband = valve_deadband
        self.econ_damper_open = econ_damper_open

    # ------------------------------------------------------------------ frame prep
    def _prepared(self, frame: pd.DataFrame):
        """A ``coil_valve_pct`` + ``coil_deltat_f`` frame, masked to active, non-econ samples.

        Returns ``(prepared, econ_excluded_fraction)``.
        """
        warm = pd.to_numeric(frame[self.warm_role], errors="coerce")
        cool = pd.to_numeric(frame[self.cool_role], errors="coerce")
        valve = pd.to_numeric(frame[self.valve_role], errors="coerce")
        out = pd.DataFrame({_METRIC: valve, _DELTAT: warm - cool}, index=frame.index)

        keep = pd.Series(True, index=frame.index)
        if self.status_role in frame.columns:
            keep &= pd.to_numeric(frame[self.status_role], errors="coerce") >= 0.5
        keep &= out[_METRIC] > self.valve_deadband  # coil actually working
        keep &= out[_DELTAT].between(*DELTAT_PLAUSIBLE)

        econ = pd.Series(False, index=frame.index)
        if self.coil == "cooling":  # only a cooling coil is stolen by the economizer
            if self.econ_role in frame.columns:
                econ |= pd.to_numeric(frame[self.econ_role], errors="coerce") >= 0.5
            if self.damper_role in frame.columns:
                econ |= (
                    pd.to_numeric(frame[self.damper_role], errors="coerce") > self.econ_damper_open
                )
        n_active = int(keep.sum())
        excluded = float((keep & econ).sum()) / n_active if n_active else 0.0
        return out[keep & ~econ], round(excluded, 4)

    def _frozen_baseline(self, equip, base_frame, caveats):
        frozen = self.store.model_for(self.site, equip, self._kind)
        if frozen is not None:
            return frozen
        if not self.freeze_if_missing:
            caveats.append(
                f"could not evaluate {self._kind}: no frozen baseline and freezing is disabled"
            )
            return None
        fit = fit_load_baseline(
            base_frame,
            metric_col=_METRIC,
            load_col=_DELTAT,
            min_load=self.min_deltat,
            metric_range=VALVE_PLAUSIBLE,
        )
        if fit is None:
            caveats.append(
                f"could not evaluate {self._kind}: the baseline period would not support a fit "
                "(too few active, non-economizing samples, or too narrow a ΔT range)"
            )
            return None
        idx = base_frame.index
        self.store.freeze(
            fit,
            site=self.site,
            equip=equip,
            kind=self._kind,
            frozen_at=self.run_id,
            period=(str(idx.min()), str(idx.max())),
            reason="initial baseline frozen from the supplied baseline period",
        )
        return fit

    # ------------------------------------------------------------------ severity
    def _severity(self, drift, caveats) -> str:
        """One-sided-UP severity: only valve *creep* clears the floors (both % and sigma)."""
        if drift.drift_sigma != drift.drift_sigma:  # NaN: baseline had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on valve % alone")
            if drift.drift_f >= self.fault_pct:
                return "fault"
            return "warn" if drift.drift_f >= self.warn_pct else "ok"
        if drift.drift_f >= self.fault_pct and drift.drift_sigma >= self.fault_sigma:
            return "fault"
        if drift.drift_f >= self.warn_pct and drift.drift_sigma >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ confound
    def _water_confound(self, base, cur, creep: bool, metrics: dict, caveats: list) -> None:
        """Report the waterside supply-temp shift; caveat a creep that co-moves with it."""
        if self.water_supply_role not in cur.columns or self.water_supply_role not in base.columns:
            return
        base_w = pd.to_numeric(base[self.water_supply_role], errors="coerce").median()
        cur_w = pd.to_numeric(cur[self.water_supply_role], errors="coerce").median()
        if base_w != base_w or cur_w != cur_w:  # a NaN
            return
        shift = round(float(cur_w - base_w), 3)
        metrics["water_supply_shift_f"] = shift
        # cooling: a *rising* CHW supply needs more valve; heating: a *falling* HW supply does.
        if creep and self._water_sign * shift >= self.water_confound:
            worse = "warmer" if self.coil == "cooling" else "colder"
            caveats.append(
                f"waterside supply temperature shifted {shift:+.1f}°F ({worse}) over the same "
                "window; that alone needs more valve for the same ΔT, so part of this creep may be "
                "a waterside-reset effect rather than coil fouling -- check the plant setpoint"
            )

    # ------------------------------------------------------------------ the rule
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's coil valve-at-ΔT vs the frozen baseline; return a Finding."""
        caveats: list = []
        missing = [
            r.value
            for r in (self.valve_role, self.warm_role, self.cool_role)
            if r not in current.columns
        ]
        if missing:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "coil_valve_inputs_not_mapped"},
                summary=f"{equip}: declined -- coil valve drift needs valve + entering/leaving air",
                caveats=[
                    f"could not evaluate the {self._label} coil: its valve position, an "
                    "entering-air (mixed-air) temp and a leaving-air (supply-air) temp must all be "
                    f"mapped; missing {', '.join(missing)}"
                ],
            )

        base_t, base_excl = self._prepared(baseline)
        cur_t, cur_excl = self._prepared(current)
        if (
            self.coil == "cooling"
            and self.econ_role not in current.columns
            and (self.damper_role not in current.columns)
        ):
            caveats.append(
                "no economizer-command or OA-damper point is mapped, so free-cooling samples could "
                "not be excluded; a cooling-coil valve signal may be corrupted during economizing"
            )

        frozen = self._frozen_baseline(equip, base_t, caveats)
        if frozen is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True},
                summary=f"{equip}: declined -- no frozen coil-valve baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_t,
            metric_col=_METRIC,
            load_col=_DELTAT,
            min_load=self.min_deltat,
            metric_range=VALVE_PLAUSIBLE,
        )
        if drift is None:
            caveats.append(
                f"could not evaluate {self._kind}: no active, non-economizing samples in the "
                "current period"
            )
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
        rec = self.store.get(self.site, equip, self._kind)
        metrics = {
            "coil_valve_drift_pct": drift.drift_f,
            "coil_valve_drift_sigma": drift.drift_sigma,
            "coil_valve_drift_direction": direction,
            "coil_valve_slope_pct_per_month": drift.slope_f_per_month,
            "coil_valve_pct_outside_2sigma": drift.pct_outside_2sigma,
            "coil_valve_n_current": drift.n_current,
            "coil_valve_baseline_sigma_pct": frozen.sigma_f,
            "coil_valve_baseline_frozen_at": rec.frozen_at if rec else "",
            "coil_deltat_median_f": round(float(cur_t[_DELTAT].median()), 3)
            if len(cur_t)
            else None,
            "coil_valve_econ_excluded_pct": round(100.0 * max(base_excl, cur_excl), 2),
        }
        self._water_confound(baseline, current, direction == "up", metrics, caveats)
        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's fitted ΔT envelope, "
                "so part of this drift is extrapolated"
            )

        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="up",  # only a sustained valve creep alarms
            )
            run = monitor.run(
                cur_t,
                approach_col=_METRIC,
                tons_col=_DELTAT,
                min_tons=self.min_deltat,
                approach_range=VALVE_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "coil_valve_sustained_alarm": run.alarmed,
                    "coil_valve_first_alarm_at": run.first_alarm_at,
                    "coil_valve_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        if direction == "up":
            headline = (
                f"{equip}: {self._label}-coil valve creep {drift.drift_f:+.0f}% "
                f"({drift.drift_sigma:.1f}σ) vs frozen baseline at matched air-ΔT -- "
                "coil fouling / waterside starvation / valve-authority loss"
            )
        else:
            headline = (
                f"{equip}: {self._label}-coil valve {drift.drift_f:+.0f}% vs frozen baseline at "
                "matched air-ΔT (less valve is not a fault)"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=headline,
            caveats=caveats,
        )
