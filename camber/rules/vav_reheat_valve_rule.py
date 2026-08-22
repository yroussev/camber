"""Rule: VAV **reheat-coil** heat-transfer drift -- reheat-valve creep at matched reheat duty.

A VAV box's hot-water reheat coil raises the discharge air above the cold primary supply air to hold
a discharge/zone heating setpoint. A coil losing heat-transfer capacity -- waterside fouling/scale,
low HW flow or waterside ΔT, air bypass, valve-authority loss -- does not usually announce itself
by missing setpoint: the controller hides it, opening the reheat valve **further and further** to
deliver the same reheat. This rule catches that creep, weeks before the coil finally runs out of
valve and the box misses its setpoint and the instantaneous reheat rules
(:class:`camber.rules.reheat_rule.ReheatPenalty`,
:class:`camber.rules.reheat_min_rule.ReheatMinimization`) see it -- the **leading** indicator to
those **lagging** ones (the terminal-box twin of :mod:`camber.rules.coil_valve_rule`).

**The signal is the reheat valve; the load is the reheat *duty*.** Unlike an AHU coil at a roughly
fixed design airflow, a VAV box's airflow varies, and delivered heat ``Q = ṁ·cp·ΔT ∝ airflow × ΔT``.
At matched ΔT but different airflow the valve genuinely differs, so ΔT-alone is a confounded load;
the physically correct load is the **reheat duty ∝ airflow × ΔT** (cfm·°F) -- the box's own "tons".
(The
tempting simplification "Guideline-36 dual-max pins reheat at minimum airflow, so ΔT-alone is fine"
only holds in the low heating loop; above min, flow rises toward heating-max -- the very regime
``reheat_minimization_g36`` flags -- so duty is right across both.) Freezing ``valve ~ f(duty)`` and
scoring the current-period valve residual **at matched duty** isolates the coil's transfer function:
fouling raises valve-at-matched-duty, demand unchanged. Both factors are exogenous of the valve --
the discharge setpoint is zone-driven and the airflow is commanded by the damper -- so the load is
demand, the valve the endogenous response; non-circular. It is **one-sided up**: a valve *fall* for
the same duty is a capacity gain (a cleaned coil, hotter/greater HW flow), not a fault. A
``load_basis="deltat"`` constructor option drops airflow and fits ``valve ~ f(ΔT)`` (with an
unmodeled-airflow caveat) for boxes without a mapped flow.

**The box's entering primary air is mapped to** ``MIXED_AIR_TEMP`` (exactly the coil-valve heating
convention): ``warm = SUPPLY_AIR_TEMP`` (box discharge, downstream of the coil), ``cool =
MIXED_AIR_TEMP`` (the cold AHU primary air feeding the box), ΔT = warm − cool. The box's own
discharge already owns ``SUPPLY_AIR_TEMP``, so the entering primary needs the distinct
``MIXED_AIR_TEMP`` role; no new role is introduced.

**The waterside HW-reset confound is a caveat:** a colder hot-water supply needs *more* valve for
the same reheat, so a HW supply-temp reset can move the valve for reasons other than fouling -- when
``HW_SUPPLY_TEMP`` is mapped the rule reports the shift and caveats a creep that co-moves with a HW
*fall*. (Valve % and water °F are different units, so this is a caveat, not a residual subtraction.)
The reheat ΔT is *sensible only*, but a heating coil has no latent term, so it is cleaner than the
cooling case; the duty load assumes constant ``cp`` and no measured hot-water flow -- a screening
proxy for ``Q``.

Reuses only existing roles. Declines loudly when the valve or the entering/discharge air pair (or,
in duty basis, the airflow) is unmapped. **Not** auto-registered (needs an injected
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

_KIND = "vav_reheat_valve"
_METRIC = "reheat_valve_pct"  # the reheat-valve column, renamed to a stable key for the fit
_LOAD = "reheat_load"  # the reheat duty (or ΔT) the baseline is fitted against
_DT = "reheat_dt"  # the derived reheat air-ΔT (kept for reporting + the deltat basis)

# Plausibility bounds: valve (metric) is a 0-100% position; ΔT is the sensible reheat rise; airflow
# is the box discharge flow (cfm); duty is airflow × ΔT (cfm·°F).
VALVE_PLAUSIBLE = (0.0, 100.0)
DELTAT_PLAUSIBLE = (0.5, 60.0)
AIRFLOW_PLAUSIBLE = (0.0, 100000.0)
DUTY_PLAUSIBLE = (0.0, 6000000.0)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds). One-sided UP (valve creep is the
# fault); both a %-of-valve floor and a sigma floor must be cleared. The sigma floor carries the
# weight; the % floor is a coarse backstop. Copied from CoilValveDrift -- reheat valve % shares the
# magnitude class. Constructor args; characterized from the signal class, not the coils.
# ---------------------------------------------------------------------------------------------
VALVE_WARN_PCT = 8.0  # screening-grade -- coarse backstop
VALVE_FAULT_PCT = 15.0  # screening-grade
VALVE_WARN_SIGMA = 2.5  # screening-grade
VALVE_FAULT_SIGMA = 4.0  # screening-grade

# Below this reheat ΔT (degF) the coil is barely reheating; the valve carries no condition info.
MIN_DELTAT = 3.0
# Below this reheat duty (cfm·°F) the coil is barely working (duty basis min-load floor).
MIN_DUTY = 1000.0
# Below this valve opening (%) the coil is not reheating (heating-mode gate).
VALVE_DEADBAND = 5.0
# A HW supply-temp shift of at least this much (degF) triggers the reset caveat.
WATER_CONFOUND = 2.0


class VavReheatValveDrift:
    """Detects a VAV reheat coil's valve creeping open at matched reheat duty (heat-transfer loss).

    ``load_basis`` (``"duty"`` | ``"deltat"``) picks the load: ``"duty"`` (default) fits
    ``valve ~ f(airflow × ΔT)`` and normalizes VAV airflow variation; ``"deltat"`` fits
    ``valve ~ f(ΔT)`` for boxes without a mapped flow (with an unmodeled-airflow caveat). Override
    any ``*_role`` to match a site's mapping. A ``BaselineStore`` is injected, so (like the other
    drift rules) it is **not** auto-registered.
    """

    name = "vav_reheat_valve_drift"

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        load_basis: str = "duty",
        valve_role: Role = Role.HEAT_VALVE,
        warm_role: Role = Role.SUPPLY_AIR_TEMP,
        cool_role: Role = Role.MIXED_AIR_TEMP,
        airflow_role: Role = Role.AIRFLOW,
        water_supply_role: Role = Role.HW_SUPPLY_TEMP,
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
        min_duty: float = MIN_DUTY,
        valve_deadband: float = VALVE_DEADBAND,
    ):
        if load_basis not in ("duty", "deltat"):
            raise ValueError(f"load_basis must be 'duty' or 'deltat', got {load_basis!r}")
        self.store = store
        self.site = site
        self.run_id = run_id
        self.load_basis = load_basis
        self.valve_role = valve_role
        self.warm_role = warm_role
        self.cool_role = cool_role
        self.airflow_role = airflow_role
        self.water_supply_role = water_supply_role
        self.status_role = status_role
        self._water_sign = (
            -1.0
        )  # heating: a *falling* HW supply needs more valve for the same reheat
        self.roles_required: tuple[Role, ...]
        self.roles_optional: tuple[Role, ...]
        if load_basis == "duty":
            self.roles_required = (valve_role, warm_role, cool_role, airflow_role)
            self.roles_optional = (water_supply_role, status_role)
            self.min_load = min_duty
        else:
            self.roles_required = (valve_role, warm_role, cool_role)
            self.roles_optional = (water_supply_role, status_role, airflow_role)
            self.min_load = min_deltat
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

    # ------------------------------------------------------------------ frame prep
    def _prepared(self, frame: pd.DataFrame):
        """A ``reheat_valve_pct`` + ``reheat_load`` (+ ``reheat_dt``) frame, masked to reheating.

        Returns ``(prepared, gated_excluded_fraction)``.
        """
        warm = pd.to_numeric(frame[self.warm_role], errors="coerce")
        cool = pd.to_numeric(frame[self.cool_role], errors="coerce")
        valve = pd.to_numeric(frame[self.valve_role], errors="coerce")
        dt = warm - cool
        if self.load_basis == "duty":
            airflow = pd.to_numeric(frame[self.airflow_role], errors="coerce")
            load = airflow * dt
        else:
            airflow = None
            load = dt
        out = pd.DataFrame({_METRIC: valve, _LOAD: load, _DT: dt}, index=frame.index)

        keep = pd.Series(True, index=frame.index)
        if self.status_role in frame.columns:
            keep &= pd.to_numeric(frame[self.status_role], errors="coerce") >= 0.5
        keep &= out[_METRIC] > self.valve_deadband  # coil actually reheating (heating-mode gate)
        keep &= out[_DT].between(*DELTAT_PLAUSIBLE) & (out[_DT] >= self.min_deltat)
        if airflow is not None:
            keep &= airflow.between(*AIRFLOW_PLAUSIBLE)
            keep &= out[_LOAD].between(*DUTY_PLAUSIBLE)

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
            min_load=self.min_load,
            metric_range=VALVE_PLAUSIBLE,
        )
        if fit is None:
            caveats.append(
                f"could not evaluate {_KIND}: the baseline would not support a fit -- too few "
                "reheating samples, or too narrow a reheat-load range"
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
        """Report the HW-supply-temp shift; caveat a creep that co-moves with a HW fall."""
        if self.water_supply_role not in cur.columns or self.water_supply_role not in base.columns:
            return
        base_w = pd.to_numeric(base[self.water_supply_role], errors="coerce").median()
        cur_w = pd.to_numeric(cur[self.water_supply_role], errors="coerce").median()
        if base_w != base_w or cur_w != cur_w:  # a NaN
            return
        shift = round(float(cur_w - base_w), 3)
        metrics["water_supply_shift_f"] = shift
        if (
            creep and self._water_sign * shift >= self.water_confound
        ):  # a HW *fall* needs more valve
            caveats.append(
                f"hot-water supply shifted {shift:+.1f}°F (colder) over the same window; "
                "that alone needs more valve for the same reheat, so part of this creep may be a "
                "waterside-reset effect rather than coil fouling -- check the plant setpoint"
            )

    # ------------------------------------------------------------------ the rule
    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current reheat valve-at-duty vs the frozen baseline; return a Finding."""
        caveats: list = []
        missing = [r.value for r in self.roles_required if r not in current.columns]
        if missing:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "vav_reheat_valve_inputs_not_mapped"},
                summary=f"{equip}: declined -- reheat-valve drift needs valve + entering/discharge",
                caveats=[
                    "could not evaluate the reheat coil: its valve position, an entering-primary "
                    "(mixed-air) temp and a discharge (supply-air) temp -- plus airflow (duty "
                    f"basis) -- must be mapped; missing {', '.join(missing)}"
                ],
            )
        if self.load_basis == "deltat":
            caveats.append(
                "load basis is ΔT-only; VAV airflow variation is unmodeled, so a healthy box whose "
                "airflow swings may read noisier -- map airflow for the duty basis"
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
                summary=f"{equip}: declined -- no frozen reheat-valve baseline to compare against",
                caveats=caveats,
            )

        drift = load_drift_stats(
            frozen,
            cur_t,
            metric_col=_METRIC,
            load_col=_LOAD,
            min_load=self.min_load,
            metric_range=VALVE_PLAUSIBLE,
        )
        if drift is None:
            caveats.append(
                f"could not evaluate {_KIND}: no reheating samples in the current period"
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
        rec = self.store.get(self.site, equip, _KIND)
        metrics = {
            "vav_reheat_valve_drift_pct": drift.drift_f,
            "vav_reheat_valve_drift_sigma": drift.drift_sigma,
            "vav_reheat_valve_drift_direction": direction,
            "vav_reheat_valve_slope_pct_per_month": drift.slope_f_per_month,
            "vav_reheat_valve_pct_outside_2sigma": drift.pct_outside_2sigma,
            "vav_reheat_valve_n_current": drift.n_current,
            "vav_reheat_valve_baseline_sigma_pct": frozen.sigma_f,
            "vav_reheat_valve_baseline_frozen_at": rec.frozen_at if rec else "",
            "vav_reheat_which": "reheat",  # locus label for the future VAV diagnosis
            "vav_reheat_load_basis": self.load_basis,  # "duty" | "deltat"
            "vav_reheat_deltat_median_f": round(float(cur_t[_DT].median()), 3)
            if len(cur_t)
            else None,
            "vav_reheat_duty_median_cfmf": round(float(cur_t[_LOAD].median()), 1)
            if (self.load_basis == "duty" and len(cur_t))
            else None,
            "vav_reheat_valve_gated_excluded_pct": round(100.0 * max(base_excl, cur_excl), 2),
        }
        self._water_confound(baseline, current, direction == "up", metrics, caveats)
        if drift.extrapolated:
            caveats.append(
                "over 10% of the current period ran outside the baseline's fitted reheat-load "
                "envelope, so part of this drift is extrapolated"
            )

        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="up",  # only a sustained reheat-valve creep alarms
            )
            run = monitor.run(
                cur_t,
                approach_col=_METRIC,
                tons_col=_LOAD,
                min_tons=self.min_load,
                approach_range=VALVE_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "vav_reheat_valve_sustained_alarm": run.alarmed,
                    "vav_reheat_valve_first_alarm_at": run.first_alarm_at,
                    "vav_reheat_valve_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        if direction == "up":
            headline = (
                f"{equip}: VAV reheat-valve creep {drift.drift_f:+.0f}% ({drift.drift_sigma:.1f}σ) "
                "vs frozen baseline at matched reheat duty -- reheat coil fouling / HW starvation "
                "/ valve-authority loss (misses setpoint before reheat_penalty flags it)"
            )
        else:
            headline = (
                f"{equip}: VAV reheat-valve {drift.drift_f:+.0f}% vs frozen baseline at matched "
                "reheat duty (less valve is not a fault)"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=headline,
            caveats=caveats,
        )
