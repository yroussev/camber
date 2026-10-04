"""Rule: coil-valve **leak** drift -- the valve-shut air rise across a coil, against the unit's own
baseline (0.100, #100; opt-in, provisional).

:class:`camber.rules.leakvalve_rule.LeakingValve` judges a leak against fixed margins: with both
valves commanded shut, the supply air must not fall more than a few °F below the mixed air (or rise
more than the fan heat plus a margin above it). A small leak on a unit whose fan adds a steady rise
can hide inside those margins: the published 10 % cooling-valve leak on ``lbnl-sdahu`` takes the
valve-shut rise from about +1.1 °F to about -0.1 °F, which no fixed margin catches without a fan
heat calibrated on the very run it is then scored on (0.98, #84).

This detector asks the drift question instead. **The signal is the coil's valve-shut air rise**
(the coil's own leaving-air temperature, or the supply air, minus the mixed air) on fan-on hours
with every mapped coil valve commanded shut; **the load is the mixed-air temperature** (the
entering air). In a known-good baseline window the rise holds whatever the unit's fan heat, sensor
placement and duct gains make it, and it is fitted against the entering air; in the current window
a leaking cooling valve pulls the rise **down** and a leaking heating valve pushes it **up**, at the
same entering-air temperature. Nothing about the fan heat has to be known or calibrated: it is
part of the baseline.

* ``coil="cooling"`` is one-sided **down** (a cooling leak); ``coil="heating"`` is one-sided
  **up** (a heating leak). A rise in the opposite direction is reported, not judged.
* The baseline comes from the drift machinery: a frozen baseline in a
  :class:`~camber.store.modelstore.BaselineStore` (``camber drift freeze``), or a **declared
  reference** (0.98, #86, S4: ``"reference": {"period": [...]}`` for a known-good window of the
  same unit, or ``{"equip": ...}`` for a healthy twin). It is never fitted on the window it scores.
* Only current hours inside the mixed-air range the baseline saw are judged; the rest are counted
  (``coil_leak_n_out_of_scope``) and caveated, never extrapolated. Where the mixed air barely moves
  in the baseline (an economizing unit holds it within a few °F) the fit is a flat level, scored
  inside its fitted band widened to ``min_mat_span_f``
  (:meth:`camber.chillerbaseline.LoadBaseline.in_scope`).

**What it cannot tell apart.** A shift in the supply-air (or mixed-air) sensor reads exactly like a
leak on the supply-air path: the ``lbnl-sdahu`` supply-air bias runs (``coi_bias_*``) move the rise
by the bias. A coil leaving-air sensor narrows it to that sensor. A valve **stuck partly open** is
the same uncommanded flow and reads the same; so is a reversed valve signal. The finding says so.

Coil-parameterized like :class:`~camber.rules.coil_valve_rule.CoilValveDrift` (both instances share
the name ``coil_leak_drift`` and freeze under distinct model kinds). **Not** auto-registered (it
needs an injected ``BaselineStore``) and **not** in the default AHU drift family: a family entry
opts in with ``"coil_leak": ["cooling"]`` (see :mod:`camber.config`).
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
from ..schedules import effective_occupied_mask
from .base import Finding

_RISE = "leak_rise_f"  # the coil's valve-shut air rise (leaving minus mixed air), degF
_MAT = "leak_mixed_air_f"  # the entering (mixed) air the baseline is fitted against, degF

# Plausibility bounds. The rise is two-sided (a leak can take it either way); the mixed air is a
# real entering-air temperature (the same 30-120 F guard leaking_valve applies).
RISE_PLAUSIBLE = (-60.0, 60.0)
MIXED_AIR_PLAUSIBLE = (30.0, 120.0)
LEAVING_PLAUSIBLE = (30.0, 140.0)

# ---------------------------------------------------------------------------------------------
# MAGNITUDE FLOORS -- SCREENING-GRADE (see camber.driftthresholds). One-sided per coil; both a °F
# floor and a sigma floor must be cleared. The sigma floors are CoilValveDrift's (2.5 / 4.0); the °F
# floors sit above the repeatability of a pair of duct temperature sensors read against themselves
# (a drift compares each sensor with its own past, so a fixed calibration offset cancels).
# ---------------------------------------------------------------------------------------------
LEAK_WARN_F = 0.5  # screening-grade
LEAK_FAULT_F = 1.5  # screening-grade
LEAK_WARN_SIGMA = 2.5  # screening-grade
LEAK_FAULT_SIGMA = 4.0  # screening-grade

VALVE_CLOSED = 5.0  # % -- a valve at/below this is commanded shut (leaking_valve's deadband)
FAN_ON_MIN = 0.5  # fan status (mean over the sample) at/above this counts as running
FAN_SPEED_ON = 5.0  # % -- with no status, a fan speed above this counts as running
MIN_MAT_SPAN = 10.0  # degF -- a narrower baseline mixed-air range fits a flat level instead

_COILS = {
    # coil: (own valve, other valve, own leaving-air role, model kind, sign of a leak, label)
    "cooling": (
        Role.COOL_VALVE,
        Role.HEAT_VALVE,
        Role.COOL_COIL_LEAVING_TEMP,
        "coil_leak_cool",
        -1.0,
        "cooling",
    ),
    "heating": (
        Role.HEAT_VALVE,
        Role.COOL_VALVE,
        Role.HEAT_COIL_LEAVING_TEMP,
        "coil_leak_heat",
        +1.0,
        "heating",
    ),
}

_BASIS = {
    Role.SUPPLY_AIR_TEMP: "supply_air_temp",
    Role.COOL_COIL_LEAVING_TEMP: "cool_coil_leaving_temp",
    Role.HEAT_COIL_LEAVING_TEMP: "heat_coil_leaving_temp",
}


class CoilLeakDrift:
    """Detects a leaking (passing) coil valve as a shift in the valve-shut air rise across the coil,
    at matched mixed-air temperature, against the unit's own baseline.

    ``coil`` (``"cooling"`` | ``"heating"``) picks the valve whose leak is judged and the direction
    (cooling: the rise falls; heating: it rises). Every mapped coil valve must be shut for a sample
    to count. A ``BaselineStore`` is injected, so (as with the other drift rules) it is **not**
    auto-registered.
    """

    name = "coil_leak_drift"

    def __init__(
        self,
        store,
        *,
        site: str = "",
        run_id: str = "",
        coil: str = "cooling",
        freeze_if_missing: bool = True,
        warn_f: float = LEAK_WARN_F,  # screening-grade -- see the module note
        fault_f: float = LEAK_FAULT_F,  # screening-grade
        warn_sigma: float = LEAK_WARN_SIGMA,  # screening-grade
        fault_sigma: float = LEAK_FAULT_SIGMA,  # screening-grade
        valve_closed_thr: float = VALVE_CLOSED,
        fan_on_min: float = FAN_ON_MIN,
        fan_speed_thr: float = FAN_SPEED_ON,
        occupied_only: bool = False,
        use_coil_leaving: bool = True,
        judge_heating_on_supply_air: bool = True,
        min_mat_span_f: float = MIN_MAT_SPAN,
        slack_sigma: float = CUSUM_SLACK_SIGMA,  # PROVISIONAL/UNTUNED -- see camber.chillerdrift
        limit_sigma: float = CUSUM_LIMIT_SIGMA,  # PROVISIONAL/UNTUNED
        clip_sigma: float = CUSUM_CLIP_SIGMA,  # PROVISIONAL/UNTUNED
        min_consecutive: int = CUSUM_MIN_CONSECUTIVE,  # PROVISIONAL/UNTUNED
    ):
        if coil not in _COILS:
            raise ValueError(f"coil must be 'cooling' or 'heating', got {coil!r}")
        own, other, leaving, kind, sign, label = _COILS[coil]
        self.store = store
        self.site = site
        self.run_id = run_id
        self.coil = coil
        self._kind = kind
        self._sign = sign
        self._label = label
        self.valve_role = own
        self.other_valve_role = other
        self.leaving_role = leaving
        self.roles_required = (own, Role.MIXED_AIR_TEMP, Role.SUPPLY_AIR_TEMP)
        self.roles_optional = (
            other,
            leaving,
            Role.SUPPLY_FAN_STATUS,
            Role.SUPPLY_FAN_SPEED,
            Role.OCCUPANCY,
        )
        self.freeze_if_missing = freeze_if_missing
        self.warn_f = float(warn_f)
        self.fault_f = float(fault_f)
        self.warn_sigma = float(warn_sigma)
        self.fault_sigma = float(fault_sigma)
        self.valve_closed_thr = float(valve_closed_thr)
        self.fan_on_min = float(fan_on_min)
        self.fan_speed_thr = float(fan_speed_thr)
        self.occupied_only = bool(occupied_only)
        self.use_coil_leaving = bool(use_coil_leaving)
        self.judge_heating_on_supply_air = bool(judge_heating_on_supply_air)
        self.min_mat_span_f = float(min_mat_span_f)
        self.slack_sigma = slack_sigma
        self.limit_sigma = limit_sigma
        self.clip_sigma = clip_sigma
        self.min_consecutive = min_consecutive

    # ------------------------------------------------------------------ frame prep
    def _basis(self, frame: pd.DataFrame):
        """The leaving-air role the coil is judged on, or None when no allowed basis exists."""
        if (
            self.use_coil_leaving
            and self.leaving_role in frame.columns
            and pd.to_numeric(frame[self.leaving_role], errors="coerce").notna().any()
        ):
            return self.leaving_role
        if self.coil == "heating" and not self.judge_heating_on_supply_air:
            return None
        return Role.SUPPLY_AIR_TEMP

    def _gates(self, frame: pd.DataFrame) -> dict:
        """Which gates applied: the fan signal used and the occupancy source (for metrics)."""
        fan = None
        if self._has(frame, Role.SUPPLY_FAN_STATUS):
            fan = "status"
        elif self._has(frame, Role.SUPPLY_FAN_SPEED):
            fan = "speed"
        occ = None
        if self.occupied_only:
            occ = (
                "trended occupancy"
                if self._has(frame, Role.OCCUPANCY)
                else "assumed schedule (weekdays 07-18)"
            )
        return {"fan": fan, "occupancy": occ}

    @staticmethod
    def _has(frame: pd.DataFrame, role) -> bool:
        return role in frame.columns and pd.to_numeric(frame[role], errors="coerce").notna().any()

    def _prepared(self, frame: pd.DataFrame, basis=None) -> pd.DataFrame:
        """A ``leak_rise_f`` + ``leak_mixed_air_f`` frame over fan-on, valve-shut samples."""
        basis = basis if basis is not None else self._basis(frame)
        if basis is None or basis not in frame.columns:
            return pd.DataFrame(columns=[_RISE, _MAT])
        mat = pd.to_numeric(frame[Role.MIXED_AIR_TEMP], errors="coerce")
        leave = pd.to_numeric(frame[basis], errors="coerce")
        keep = pd.to_numeric(frame[self.valve_role], errors="coerce") <= self.valve_closed_thr
        if self._has(frame, self.other_valve_role):
            other = pd.to_numeric(frame[self.other_valve_role], errors="coerce")
            # a missing reading of a mapped valve is not "shut"
            keep &= other <= self.valve_closed_thr
        if self._has(frame, Role.SUPPLY_FAN_STATUS):
            keep &= pd.to_numeric(frame[Role.SUPPLY_FAN_STATUS], errors="coerce") >= self.fan_on_min
        elif self._has(frame, Role.SUPPLY_FAN_SPEED):
            keep &= (
                pd.to_numeric(frame[Role.SUPPLY_FAN_SPEED], errors="coerce") > self.fan_speed_thr
            )
        if self.occupied_only and isinstance(frame.index, pd.DatetimeIndex):
            occ = frame[Role.OCCUPANCY] if self._has(frame, Role.OCCUPANCY) else None
            occ_mask = effective_occupied_mask(frame.index, occ=occ)
            keep &= pd.Series(occ_mask.to_numpy(dtype=bool), index=frame.index)
        keep &= mat.between(*MIXED_AIR_PLAUSIBLE) & leave.between(*LEAVING_PLAUSIBLE)
        out = pd.DataFrame({_RISE: leave - mat, _MAT: mat}, index=frame.index)
        return out[keep.fillna(False).astype(bool)]

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
            metric_col=_RISE,
            load_col=_MAT,
            min_load=MIXED_AIR_PLAUSIBLE[0],
            metric_range=RISE_PLAUSIBLE,
            min_load_span=self.min_mat_span_f,
            level_fallback=True,
        )
        if fit is None:
            caveats.append(
                f"could not evaluate {self._kind}: the baseline period has too few fan-on hours "
                "with the coil valves shut to fit the valve-shut rise"
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
        """One-sided severity: only a shift in the leak direction clears the floors."""
        toward = self._sign * drift.drift_f  # > 0 = in the direction a leak moves the rise
        if drift.drift_sigma != drift.drift_sigma:  # NaN: the baseline had no residual scatter
            caveats.append("baseline had no residual scatter, so drift is judged on °F alone")
            if toward >= self.fault_f:
                return "fault"
            return "warn" if toward >= self.warn_f else "ok"
        sig = self._sign * drift.drift_sigma
        if toward >= self.fault_f and sig >= self.fault_sigma:
            return "fault"
        if toward >= self.warn_f and sig >= self.warn_sigma:
            return "warn"
        return "ok"

    # ------------------------------------------------------------------ pattern J evidence
    def drift_signature(self):
        """The per-coil frozen-model kind and the (mixed-air, rise) columns it is fitted on."""
        return self._kind, _MAT, _RISE

    def drift_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        """The prepared valve-shut rise-vs-mixed-air frame."""
        return self._prepared(frame)

    # ------------------------------------------------------------------ the rule
    def _decline(self, equip, reason, summary, caveats) -> Finding:
        return Finding(
            rule=self.name,
            equip=equip,
            severity="info",
            metrics={"declined": True, "reason": reason, "coil_leak_which": self._label},
            summary=f"{equip}: declined -- {summary}",
            caveats=caveats,
        )

    def analyze_periods(self, equip: str, baseline: pd.DataFrame, current: pd.DataFrame) -> Finding:
        """Score the current period's valve-shut rise vs the baseline at matched mixed air."""
        caveats: list = []
        missing = [r.value for r in self.roles_required if r not in current.columns]
        if missing:
            return self._decline(
                equip,
                "coil_leak_inputs_not_mapped",
                f"{self._label}-coil leak drift needs its valve, mixed air and supply air",
                [
                    f"could not evaluate the {self._label} coil for a leak: its valve command, the "
                    f"mixed-air and the supply-air temperatures must be mapped; missing "
                    f"{', '.join(missing)}"
                ],
            )
        basis = self._basis(current)
        if basis is None:
            return self._decline(
                equip,
                "no_heating_coil_basis",
                "no air stream that leaves the heating coil is mapped",
                [
                    "heating coil not judged: it has no leaving-air sensor, and "
                    "judge_heating_on_supply_air is off (the mapped supply air does not leave it)"
                ],
            )
        base_basis = self._basis(baseline)
        if base_basis != basis:
            return self._decline(
                equip,
                "basis_mismatch",
                "the baseline and current windows judge the coil on different air streams",
                [
                    f"the baseline window reads {_BASIS.get(base_basis, base_basis)} and the "
                    f"current window {_BASIS.get(basis, basis)}; a rise on one sensor cannot be "
                    "scored against a baseline fitted on another"
                ],
            )

        base_t = self._prepared(baseline, basis)
        cur_t = self._prepared(current, basis)
        gates = self._gates(current)
        if gates["fan"] is None:
            caveats.append(
                "no fan status or speed: every valve-shut sample is judged, including any with the "
                "fan off (no air across the coil)"
            )

        frozen = self._frozen_baseline(equip, base_t, caveats)
        if frozen is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "coil_leak_which": self._label},
                summary=f"{equip}: declined -- no frozen valve-shut rise baseline to compare with",
                caveats=caveats,
            )

        # Judge only the mixed-air range the baseline saw: the rise-vs-mixed-air line says nothing
        # outside it (a damper stuck open takes the mixed air 20 F below a baseline fitted on a
        # mild season, and extrapolating the slope there reads as a leak). A flat level already
        # scores only inside its band (LoadBaseline.in_scope).
        mats = cur_t[_MAT].to_numpy(dtype=float)
        if frozen.load_model == "level":
            inside = frozen.in_scope(mats)
        else:
            inside = (mats >= frozen.tons_min) & (mats <= frozen.tons_max)
        n_out = int((~inside).sum()) if len(cur_t) else 0
        scored = cur_t[inside] if len(cur_t) else cur_t
        drift = load_drift_stats(
            frozen,
            scored,
            metric_col=_RISE,
            load_col=_MAT,
            min_load=MIXED_AIR_PLAUSIBLE[0],
            metric_range=RISE_PLAUSIBLE,
        )
        if drift is None:
            caveats.append(
                f"could not evaluate {self._kind}: fewer than 10 fan-on, valve-shut samples in the "
                "current period"
                + (
                    f" inside the baseline's mixed-air range ({n_out} fell outside it)"
                    if n_out
                    else ""
                )
            )
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={
                    "declined": True,
                    "coil_leak_which": self._label,
                    "coil_leak_n_valve_shut": int(len(cur_t)),
                    "coil_leak_n_out_of_scope": n_out,
                },
                summary=f"{equip}: declined -- nothing scoreable in the current period",
                caveats=caveats,
            )

        severity = self._severity(drift, caveats)
        toward = self._sign * drift.drift_f >= 0
        direction = "down" if drift.drift_f < 0 else "up"
        rec = self.store.get(self.site, equip, self._kind)
        metrics = {
            "coil_leak_drift_f": drift.drift_f,
            "coil_leak_drift_sigma": drift.drift_sigma,
            "coil_leak_drift_direction": direction,
            "coil_leak_which": self._label,
            "coil_leak_basis": _BASIS.get(basis, str(basis)),
            "coil_leak_slope_f_per_month": drift.slope_f_per_month,
            "coil_leak_pct_outside_2sigma": drift.pct_outside_2sigma,
            "coil_leak_n_current": drift.n_current,
            "coil_leak_n_out_of_scope": n_out,
            "coil_leak_baseline_n": frozen.n,
            "coil_leak_baseline_sigma_f": frozen.sigma_f,
            "coil_leak_baseline_model": frozen.load_model,
            "coil_leak_baseline_frozen_at": rec.frozen_at if rec else "",
            "coil_leak_rise_median_f": round(float(scored[_RISE].median()), 3),
            "coil_leak_fan_gate": gates["fan"],
        }
        if gates["occupancy"] is not None:
            metrics["coil_leak_occupancy_gate"] = gates["occupancy"]
        if n_out:
            share = n_out / max(len(cur_t), 1)
            caveats.append(
                f"{n_out} valve-shut samples ({share:.0%}) ran at a mixed-air temperature outside "
                "the baseline's range and were not judged; a baseline that spans more seasons "
                "covers them"
            )
        if severity in ("warn", "fault"):
            on = "supply air" if basis == Role.SUPPLY_AIR_TEMP else "coil leaving air"
            caveats.append(
                f"a shift in the {on} or mixed-air sensor moves this rise exactly as a leak does; "
                "check both sensors against a reference thermometer before pulling the valve. A "
                "valve stuck partly open, or a reversed valve signal, passes water the same way"
            )

        try:
            monitor = ApproachDriftMonitor(
                frozen,
                slack_sigma=self.slack_sigma,
                limit_sigma=self.limit_sigma,
                clip_sigma=self.clip_sigma,
                min_consecutive=self.min_consecutive,
                direction="down" if self._sign < 0 else "up",
            )
            run = monitor.run(
                scored,
                approach_col=_RISE,
                tons_col=_MAT,
                min_tons=MIXED_AIR_PLAUSIBLE[0],
                approach_range=RISE_PLAUSIBLE,
            )
        except ValueError as exc:
            run = None
            caveats.append(f"could not run the sustained-shift alarm: {exc}")
        if run is not None:
            metrics.update(
                {
                    "coil_leak_sustained_alarm": run.alarmed,
                    "coil_leak_first_alarm_at": run.first_alarm_at,
                    "coil_leak_alarm_direction": run.alarm_direction,
                }
            )
        metrics.update(threshold_confidence(magnitude=True, temporal=run is not None))

        if toward:
            headline = (
                f"{equip}: {self._label}-coil valve-shut air rise {drift.drift_f:+.1f}°F "
                f"({drift.drift_sigma:+.1f}σ) vs frozen baseline at matched mixed air -- "
                f"{'uncommanded cooling' if self._sign < 0 else 'uncommanded heating'} "
                "(a leaking or passing valve)"
            )
        else:
            headline = (
                f"{equip}: {self._label}-coil valve-shut air rise {drift.drift_f:+.1f}°F vs frozen "
                "baseline at matched mixed air (not the direction a "
                f"{self._label} leak moves it)"
            )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics=metrics,
            summary=headline,
            caveats=caveats,
        )
