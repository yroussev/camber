"""Rule: a terminal box's zone below its heating setpoint with the reheat saturated (0.93, #44).

A VAV box heats its zone by opening the reheat valve (and, in a G36 dual-max sequence, by raising
airflow toward the heating maximum). When the valve is already **fully open** and the zone still
sits **below its heating setpoint**, the box has run out of heating: the controller cannot do more,
so this is not a tuning problem but a **capacity or airflow** one -- low hot-water supply
temperature or flow, a fouled or undersized coil, a starved or stuck damper, low primary airflow,
or a zone load beyond design. It is the lagging, symptomatic end of what
:class:`camber.rules.vav_reheat_valve_rule.VavReheatValveDrift` catches early (a valve creeping
open at matched duty), and the complement of ``unmet_setpoint_hours`` -- which counts cold hours
without asking whether the box had anything left to give.

**The check.** In occupied samples (the ``OCCUPANCY`` point, else the ``start_hour``/``end_hour``/
``occupied_days`` schedule), minus a ``recovery_hours`` window after each occupied period starts
and after each heating-setpoint step up, and minus trended ``WARMUP`` samples (morning warm-up from
a setback is expected to run the valve wide open below setpoint), and minus samples with the box's
own fan off where a fan signal is mapped (a fan-powered box delivering no air), a sample is a
**saturated shortfall** when

* the zone temperature is more than ``tol_f`` (default 1.5 F, the ``unmet_setpoint_hours``
  tolerance) below the heating setpoint, and
* the reheat valve is at or above ``reheat_saturated_pct`` (default 90 %, as in
  ``overcooling_severity``, which sets these samples aside as a heating shortfall rather than
  overcooling; this rule reports them as the finding).

The zone heating setpoint comes from the data (``HEAT_SP``) or, when it is not trended, from the
config: ``heat_sp_f`` as one value for every box or a ``{equip: degF}`` mapping per box. Without
either the rule declines (``info``) rather than guess a setpoint.

The share of evaluated samples in saturated shortfall grades it: ``warn`` from ``warn_pct``
(default 5 %), ``fault`` from ``fault_pct`` (default 20 %), and only once at least
``min_hours`` (default 10 h) of shortfall have accumulated. The thresholds are screening-grade
engineering judgement, not a standard's. The summary also reports how often the zone was
under-heated with the valve **not** saturated -- a control or tuning problem, not this rule's
finding.

**Which problem?** With a measured airflow and its setpoint mapped, the median airflow-to-setpoint
ratio in the shortfall samples points at airflow (below ~90 % of setpoint: damper, static pressure,
primary air) or at heating capacity (at setpoint: hot-water temperature or flow, the coil). With a
discharge-air temperature mapped, its median in the shortfall samples is reported. Both are
metrics and a caveat, not a separate verdict.

Terminal boxes only (the 0.91 equipment-class gate, :mod:`camber.rules.applicability`): an air
handler's heating valve at 100 % with a cold return is a different problem. Heat pumps and fan
coils have their own capacity checks (#40).
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..schedules import effective_occupied_mask
from .base import Finding

__all__ = ["AIRFLOW_SHORT_RATIO", "ReheatCapacityShortfall"]

#: airflow below this fraction of its setpoint in the shortfall samples reads as an airflow problem
AIRFLOW_SHORT_RATIO = 0.9


def _sample_hours(index) -> float:
    """The frame's sample spacing in hours (median step; 1 h when it cannot be told)."""
    if len(index) < 2:
        return 1.0
    step = pd.Series(index).diff().median()
    return float(step / pd.Timedelta(hours=1)) if pd.notna(step) else 1.0


class ReheatCapacityShortfall:
    """Flags a terminal box whose zone sits below its heating setpoint with the reheat saturated
    (a capacity or airflow problem; 0.93, #44)."""

    name = "reheat_capacity_shortfall"
    roles_required = (Role.SPACE_TEMP, Role.HEAT_VALVE)
    roles_optional = (
        Role.HEAT_SP,
        Role.OCCUPANCY,
        Role.AIRFLOW,
        Role.AIRFLOW_SP,
        Role.SUPPLY_AIR_TEMP,
        Role.WARMUP,
        Role.SUPPLY_FAN_STATUS,
        Role.SUPPLY_FAN_SPEED,
    )

    def __init__(
        self,
        *,
        heat_sp_f: float | dict | None = None,
        tol_f: float = 1.5,
        reheat_saturated_pct: float = 90.0,
        warn_pct: float = 5.0,
        fault_pct: float = 20.0,
        min_hours: float = 10.0,
        recovery_hours: float = 2.0,
        start_hour: float = 7,
        end_hour: float = 18,
        occupied_days=(0, 1, 2, 3, 4),
    ):
        # ``heat_sp_f``: the zone heating setpoint (degF) when HEAT_SP is not trended -- one value
        # for every box, or {equip: degF}. ``tol_f``: how far below it the zone must sit.
        # ``reheat_saturated_pct``: the reheat valve at or above this is fully open (the
        # overcooling_severity default, 90 %). ``recovery_hours``:
        # samples this long after occupancy starts or the setpoint steps up are warm-up, not a
        # shortfall. The schedule applies only without an OCCUPANCY point.
        self.heat_sp_f = dict(heat_sp_f) if isinstance(heat_sp_f, dict) else heat_sp_f
        self.tol_f = float(tol_f)
        self.reheat_saturated_pct = float(reheat_saturated_pct)
        self.warn_pct = float(warn_pct)
        self.fault_pct = float(fault_pct)
        self.min_hours = float(min_hours)
        self.recovery_hours = float(recovery_hours)
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.occupied_days = tuple(occupied_days)

    def _setpoint(self, equip: str, frame: pd.DataFrame):
        """(setpoint Series, source): the trended HEAT_SP, else the configured value, else None."""
        if Role.HEAT_SP in frame.columns and frame[Role.HEAT_SP].notna().any():
            return frame[Role.HEAT_SP].astype(float), "data"
        cfg = self.heat_sp_f
        if isinstance(cfg, dict):
            cfg = cfg.get(equip)
        if cfg is None:
            return None, None
        return pd.Series(float(cfg), index=frame.index), "config"

    def _recovery(self, occ: pd.Series, sp: pd.Series) -> pd.Series:
        """Samples within ``recovery_hours`` after occupancy starts or the setpoint steps up."""
        if self.recovery_hours <= 0:
            return pd.Series(False, index=occ.index)
        starts = occ & ~occ.shift(1, fill_value=False)
        step_up = sp.diff() >= 1.0
        events = (starts | step_up.fillna(False)).astype(float)
        win = pd.Timedelta(hours=self.recovery_hours)
        if not isinstance(events.index, pd.DatetimeIndex):
            return pd.Series(False, index=occ.index)
        # a sample is in recovery when an event happened in the preceding window (inclusive)
        recent = events.rolling(win, closed="both").max()
        return recent.fillna(0.0) > 0

    def _masks(self, frame: pd.DataFrame, sp: pd.Series):
        """(evaluated, shortfall, under-heated, saturated, deficit, recovering, have) masks."""
        occ = effective_occupied_mask(
            frame.index,
            occ=frame[Role.OCCUPANCY] if Role.OCCUPANCY in frame.columns else None,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            days=self.occupied_days,
        )
        occ = pd.Series(occ, index=frame.index).astype(bool)
        zone = frame[Role.SPACE_TEMP].astype(float)
        valve = frame[Role.HEAT_VALVE].astype(float)
        recovering = self._recovery(occ, sp)
        if Role.WARMUP in frame.columns:
            recovering = recovering | (frame[Role.WARMUP].fillna(0) > 0)
        recovering = recovering & occ
        have = zone.notna() & valve.notna() & sp.notna()
        if Role.SUPPLY_FAN_STATUS in frame.columns and frame[Role.SUPPLY_FAN_STATUS].notna().any():
            have = have & (frame[Role.SUPPLY_FAN_STATUS].astype(float) > 0.5)
        elif Role.SUPPLY_FAN_SPEED in frame.columns and frame[Role.SUPPLY_FAN_SPEED].notna().any():
            have = have & (frame[Role.SUPPLY_FAN_SPEED].astype(float) > 5.0)
        ev = occ & have & ~recovering
        deficit = sp - zone
        under = ev & (deficit > self.tol_f)
        saturated = valve >= self.reheat_saturated_pct
        return ev, under & saturated, under, saturated, deficit, recovering, have

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on a terminal box's role-frame; return a Finding."""
        if Role.SPACE_TEMP not in frame.columns or Role.HEAT_VALVE not in frame.columns:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: needs zone temperature + reheat valve",
            )
        sp, sp_src = self._setpoint(equip, frame)
        if sp is None:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=(
                    f"{equip}: no zone heating setpoint (map heat_sp, or configure heat_sp_f)"
                ),
                caveats=["no heating setpoint in the data or the config: not evaluated"],
            )
        ev, short, under, saturated, deficit, recovering, have = self._masks(frame, sp)
        n = int(ev.sum())
        if n == 0:
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary=f"{equip}: no occupied zone-temperature + reheat-valve data",
            )
        n_short = int(short.sum())
        hrs = _sample_hours(frame.index)
        short_hours = round(n_short * hrs, 1)
        short_pct = round(100.0 * n_short / n, 2)
        under_pct = round(100.0 * int(under.sum()) / n, 2)
        under_unsat_pct = round(100.0 * int((under & ~saturated).sum()) / n, 2)
        sat_pct = round(100.0 * int((ev & saturated).sum()) / n, 2)
        med_deficit = round(float(deficit[short].median()), 2) if n_short else None

        metrics = {
            "shortfall_pct": short_pct,  # of evaluated occupied samples
            "shortfall_hours": short_hours,
            "underheated_pct": under_pct,
            "underheated_unsaturated_pct": under_unsat_pct,
            "valve_saturated_pct": sat_pct,
            "median_deficit_f": med_deficit,
            "n_evaluated": n,
            "n_recovery_excluded": int((recovering & have).sum()),
            "setpoint_source": sp_src,
            "reheat_saturated_pct": self.reheat_saturated_pct,
            "tol_f": self.tol_f,
            "airflow_to_sp_ratio": None,
            "discharge_temp_f": None,
            "likely_cause": None,
        }
        caveats = []
        if sp_src == "config":
            caveats.append(
                "zone heating setpoint from the config (not trended): a scheduled setback or a "
                "local adjustment the config does not know would move the verdict"
            )
        if Role.OCCUPANCY not in frame.columns:
            caveats.append(
                f"no trended occupancy: occupied = the assumed schedule "
                f"({self.start_hour:g}-{self.end_hour:g}h, days {list(self.occupied_days)})"
            )
        if n_short and Role.AIRFLOW in frame.columns and Role.AIRFLOW_SP in frame.columns:
            af = frame[Role.AIRFLOW].astype(float)[short]
            afsp = frame[Role.AIRFLOW_SP].astype(float)[short]
            ok = afsp > 0
            if ok.any():
                ratio = round(float((af[ok] / afsp[ok]).median()), 2)
                metrics["airflow_to_sp_ratio"] = ratio
                metrics["likely_cause"] = "airflow" if ratio < AIRFLOW_SHORT_RATIO else "capacity"
        if n_short and Role.SUPPLY_AIR_TEMP in frame.columns:
            dat = frame[Role.SUPPLY_AIR_TEMP].astype(float)[short].dropna()
            if len(dat):
                metrics["discharge_temp_f"] = round(float(dat.median()), 1)
        if n_short and metrics["likely_cause"] is None:
            caveats.append(
                "no airflow and airflow setpoint: capacity (hot-water temperature or flow, the "
                "coil) and airflow (damper, static pressure, primary air) are not told apart"
            )

        if short_hours >= self.min_hours and short_pct >= self.fault_pct:
            sev = "fault"
        elif short_hours >= self.min_hours and short_pct >= self.warn_pct:
            sev = "warn"
        else:
            sev = "ok"

        ratio = metrics["airflow_to_sp_ratio"]
        if metrics["likely_cause"] == "airflow":
            cause = f"; airflow {ratio:.0%} of its setpoint -- an airflow problem"
        elif metrics["likely_cause"] == "capacity":
            cause = f"; airflow at {ratio:.0%} of its setpoint -- a heating capacity problem"
        else:
            cause = ""
        deficit_txt = f", median {med_deficit:.1f}F below" if med_deficit is not None else ""
        summary = (
            f"{equip}: zone below its heating setpoint with the reheat valve saturated "
            f"(>= {self.reheat_saturated_pct:g}%) in {short_pct:.1f}% of occupied samples "
            f"({short_hours:g} h{deficit_txt}){cause}; under-heated {under_pct:.1f}% in all, "
            f"{under_unsat_pct:.1f}% with reheat to spare"
        )
        return Finding(
            rule=self.name,
            equip=equip,
            severity=sev,
            metrics=metrics,
            summary=summary,
            caveats=caveats,
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: zone temperature against its heating setpoint, saturated-shortfall spans."""
        from ..charts.evidence import Evidence

        if any(r not in frame.columns for r in (Role.SPACE_TEMP, Role.HEAT_VALVE)):
            return None
        sp, _ = self._setpoint(equip, frame)
        if sp is None:
            return None
        short = self._masks(frame, sp)[1]
        roles = [Role.SPACE_TEMP] + [r for r in (Role.HEAT_SP,) if r in frame.columns]
        return Evidence(
            renderer="multitrend",
            roles=roles,
            mask=short,
            label="below setpoint, reheat saturated",
            title=f"{equip}: zone vs heating setpoint",
        )
