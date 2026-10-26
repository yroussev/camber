"""Rule: G36 reheat-minimization compliance at VAV reheat boxes (§5.6.5).

G36's dual-max reheat sequence minimizes reheat energy: when a zone needs heat,
the box should FIRST raise the discharge-air temperature while holding airflow at
the heating *minimum* (heating loop 0-50%), and only raise airflow toward the
heating maximum at high heating demand (loop 51-100%). Raising airflow while
reheating but not deep in heating demand wastes energy -- you reheat more air than
necessary.

We detect the observable footprint: the reheat valve is open (heating active) while airflow is
meaningfully ABOVE the box's minimum-flow setpoint, outside G36's high heating loop. Persistent
high-flow reheat indicates the dual-max minimization is not in effect (a single-maximum sequence,
or the minimum flow set too high -- see the overcooling diagnostic).

0.106 (#186):

* **The minimum airflow.** ``airflow_min_sp`` (the box's minimum airflow setpoint) is read when
  it is mapped; else ``airflow_sp`` is read as the minimum, as before -- unless it moves like
  G36's *active* airflow setpoint (it swings with load and the airflow tracks it), which would
  hide every hour above the minimum. Then the minimum is inferred as a low percentile of that
  setpoint, the finding says so and it cannot rate above warn (:mod:`camber.rules._minflow`).
* **The high heating loop.** In G36's dual-maximum sequence the airflow rises above the minimum by
  design once the heating loop passes 50 %, with the discharge air already at its maximum. Those
  hours are not violations. The rule reads the zone's heating loop (``zone_heat_loop`` > 50 %)
  when it is trended; otherwise it counts an hour as high heating loop when the reheat valve is
  at or above ``dual_max_valve`` or, with the box's discharge temperature (``supply_air_temp`` at
  a terminal) and the space temperature both trended, when the discharge is at least
  ``dual_max_dat_rise`` above the space. A single-maximum box holding a high airflow with its
  valve wide open looks the same in those hours; it still shows in its part-load reheat hours.
* **Occupancy.** A trended ``occupancy`` point replaces the assumed weekday 07:00-18:00 schedule.

0.108 (#238): reheat is counted in hours (samples x the sample interval), and below
``min_reheat_hours`` of occupied reheat the rule does not rate the box (``info``, with a caveat),
so one sample cannot decide the verdict. The sample count is ``n_reheat_samples``. With the
heating loop trended, samples where it reads nothing are dropped, not read as a low loop.

Clean-room: implements the §5.6.5 logic; G36 text/tables not reproduced.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..model.roles import Role
from ..schedules import effective_occupied_mask
from ._minflow import BASIS_INFERRED, INFERRED_CAVEAT, min_flow_series
from .base import Finding


class ReheatMinimization:
    """Detects high-flow reheat that violates G36 dual-max reheat minimization
    (ASHRAE Guideline 36 §5.6.5)."""

    name = "reheat_minimization_g36"
    roles_required = (Role.HEAT_VALVE, Role.AIRFLOW)
    # 0.106 (#186): the box's minimum airflow setpoint, else its airflow setpoint read as one
    roles_any_of = ((Role.AIRFLOW_MIN_SP, Role.AIRFLOW_SP),)
    roles_optional = (
        Role.ZONE_HEAT_LOOP,
        Role.SUPPLY_AIR_TEMP,
        Role.SPACE_TEMP,
        Role.OCCUPANCY,
        Role.WARMUP,
        Role.COOLDOWN,
    )

    def __init__(
        self,
        valve_thr: float = 5.0,
        flow_margin: float = 1.20,
        *,
        dual_max_valve: float = 90.0,
        dual_max_dat_rise: float = 15.0,
        min_reheat_hours: float = 2.0,
        start_hour: float = 7,
        end_hour: float = 18,
        occupied_days=(0, 1, 2, 3, 4),
    ):
        # airflow above (1 + margin-1) x its minimum setpoint counts as "above min"
        self.valve_thr = valve_thr
        self.flow_margin = flow_margin
        # 0.106 (#186): the high-heating-loop evidence without a trended heating loop
        self.dual_max_valve = dual_max_valve
        self.dual_max_dat_rise = dual_max_dat_rise
        # 0.108 (#238): below this much occupied reheat the box is not rated
        self.min_reheat_hours = min_reheat_hours
        # The schedule is only an assumption: a trended OCCUPANCY point replaces it.
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.occupied_days = tuple(occupied_days)

    def _high_loop(self, w: pd.DataFrame) -> tuple[pd.Series, str]:
        """Hours in G36's high heating loop (airflow may rise above the minimum by design)."""
        if Role.ZONE_HEAT_LOOP in w.columns and w[Role.ZONE_HEAT_LOOP].notna().any():
            return w[Role.ZONE_HEAT_LOOP] > 50.0, "zone_heat_loop"
        high = w[Role.HEAT_VALVE] >= self.dual_max_valve
        if Role.SUPPLY_AIR_TEMP in w.columns and Role.SPACE_TEMP in w.columns:
            rise = w[Role.SUPPLY_AIR_TEMP] - w[Role.SPACE_TEMP]
            if rise.notna().any():
                return high | (rise >= self.dual_max_dat_rise), "valve or discharge temperature"
        return high, "valve"

    def _window(self, frame: pd.DataFrame) -> tuple:
        """``(w, min-flow basis, samples dropped for no heating-loop reading)``: the occupied
        samples with a valve, an airflow and a minimum, ``_vmin`` the minimum (#298)."""
        occ_pt = frame[Role.OCCUPANCY] if Role.OCCUPANCY in frame.columns else None
        occupied = effective_occupied_mask(
            frame.index,
            occ=occ_pt,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            days=self.occupied_days,
            warmup=frame[Role.WARMUP] if Role.WARMUP in frame.columns else None,
            cooldown=frame[Role.COOLDOWN] if Role.COOLDOWN in frame.columns else None,
        )
        vmin, basis = min_flow_series(frame, occupied)
        w = frame.copy()
        w["_vmin"] = vmin
        w = w[occupied.reindex(w.index).fillna(False).astype(bool)]
        w = w.dropna(subset=[Role.HEAT_VALVE, Role.AIRFLOW, "_vmin"])
        # 0.108 (#238): a trended heating loop that reads nothing is not a low loop -- drop it
        n_loop_missing = 0
        if Role.ZONE_HEAT_LOOP in w.columns and w[Role.ZONE_HEAT_LOOP].notna().any():
            n_loop_missing = int(w[Role.ZONE_HEAT_LOOP].isna().sum())
            w = w[w[Role.ZONE_HEAT_LOOP].notna()]
        return w, basis, n_loop_missing

    def _violation(self, w: pd.DataFrame) -> pd.Series:
        """Reheating with airflow above the minimum outside the high heating loop."""
        reheating = w[Role.HEAT_VALVE] > self.valve_thr
        above_min = w[Role.AIRFLOW] > w["_vmin"] * self.flow_margin
        return reheating & above_min & ~self._high_loop(w)[0]

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J (#298): the valve and airflow against the minimum, with the occupied samples
        the finding counts as G36 violations (reheat above the minimum outside the high heating
        loop) shaded."""
        from ..charts.evidence import Evidence

        has_min = Role.AIRFLOW_MIN_SP in frame.columns or Role.AIRFLOW_SP in frame.columns
        if any(r not in frame.columns for r in (Role.HEAT_VALVE, Role.AIRFLOW)) or not has_min:
            return None
        w, _basis, _n = self._window(frame)
        if w.empty:
            return None
        sp = Role.AIRFLOW_MIN_SP if Role.AIRFLOW_MIN_SP in frame.columns else Role.AIRFLOW_SP
        return Evidence(
            renderer="multitrend",
            roles=[Role.HEAT_VALVE, Role.AIRFLOW, sp],
            mask=self._violation(w),
            label="reheat above minimum",
            title=f"{equip}: reheat above the minimum airflow (G36 §5.6.5)",
            normalize="unit",
        )

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        has_min = Role.AIRFLOW_MIN_SP in frame.columns or Role.AIRFLOW_SP in frame.columns
        if any(r not in frame.columns for r in (Role.HEAT_VALVE, Role.AIRFLOW)) or not has_min:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="needs reheat valve + airflow + minimum airflow setpoint",
            )
        occ_pt = frame[Role.OCCUPANCY] if Role.OCCUPANCY in frame.columns else None
        w, basis, n_loop_missing = self._window(frame)
        n = len(w)
        occupancy = "trended" if occ_pt is not None and occ_pt.notna().any() else "schedule"
        if n == 0:
            return Finding(rule=self.name, equip=equip, severity="info", summary="no occupied data")
        reheating = w[Role.HEAT_VALVE] > self.valve_thr
        n_reheat = int(reheating.sum())
        step_h = _interval_hours(frame.index)
        reheat_h = round(n_reheat * step_h, 2)
        if n_reheat == 0:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="ok",
                metrics={
                    "reheat_hours_pct": 0.0,
                    "n_considered": n,
                    "min_flow_basis": basis,
                    "occupancy": occupancy,
                },
                summary=f"{equip}: no reheating in occupied hours",
            )
        above_min = w[Role.AIRFLOW] > w["_vmin"] * self.flow_margin
        high_loop, evidence = self._high_loop(w)
        # G36 violation: reheating AND airflow above minimum outside the high heating loop
        # (the airflow should stay at the minimum until deep heating demand)
        violation = reheating & above_min & ~high_loop
        by_design = reheating & above_min & high_loop
        viol_pct = round(100.0 * int(violation.sum()) / n_reheat, 1)  # of reheat hours
        severity = "fault" if viol_pct >= 40.0 else ("warn" if viol_pct >= 15.0 else "ok")
        caveats = []
        if n_loop_missing:
            caveats.append(
                f"{n_loop_missing} occupied sample(s) with no heating-loop reading were left out"
            )
        if basis == BASIS_INFERRED:
            caveats.append(INFERRED_CAVEAT)
            if severity == "fault":
                severity = "warn"
        n_design = int(by_design.sum())
        if evidence != "zone_heat_loop" and n_design:
            dat = (
                f" or discharge >= {self.dual_max_dat_rise:g} °F above the space"
                if evidence != "valve"
                else ""
            )
            caveats.append(
                f"heating loop not trended: {round(n_design * step_h, 2):g} reheating hour(s) "
                "above the minimum were "
                f"read as G36's high heating loop (valve >= {self.dual_max_valve:g} %{dat}) and "
                "not counted; a single-maximum box with its valve wide open looks the same"
            )
        if reheat_h < self.min_reheat_hours:
            # 0.108 (#238): too little reheat to rate the sequence on
            caveats.append(
                f"only {reheat_h:g} h of occupied reheat, under min_reheat_hours "
                f"({self.min_reheat_hours:g} h): not rated"
            )
            severity = "info"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            caveats=caveats,
            metrics={
                "reheat_hours_pct": round(100.0 * n_reheat / n, 1),
                "reheat_above_min_pct": viol_pct,  # of reheating hours
                "reheat_high_loop_pct": round(100.0 * n_design / n_reheat, 1),
                "n_reheat_hours": reheat_h,
                "n_reheat_samples": n_reheat,
                "n_considered": n,
                "min_flow_basis": basis,
                "high_loop_evidence": evidence,
                "occupancy": occupancy,
            },
            summary=(
                f"{equip}: reheating {100.0 * n_reheat / n:.0f}% of occupied hours; "
                f"of those, {viol_pct:.0f}% with airflow above minimum outside the high heating "
                f"loop (G36 §5.6.5 would hold min flow until deep heating demand)"
            ),
        )


def _interval_hours(index) -> float:
    """The median positive sample spacing in hours (1.0 with fewer than two distinct samples)."""
    idx = pd.DatetimeIndex(index)
    if len(idx) < 2:
        return 1.0
    steps = np.diff(idx.asi8) / 3.6e12
    steps = steps[steps > 0]
    return float(np.median(steps)) if steps.size else 1.0
