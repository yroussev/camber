"""Rule: terminal-box reheat penalty.

A VAV/CAV box reheating while it is also being cooled (cold central supply air,
high outdoor temp, airflow above minimum, or space at/below the cooling setpoint)
wastes energy. Adapts :func:`camber.reheat.analyze_box` to the role-frame
interface; needs HEAT_VALVE, and uses OAT / air temperatures / airflow / setpoint
roles opportunistically for the richer indicators.

**Terminal air-temperature convention.** At a terminal box, ``SUPPLY_AIR_TEMP`` is the box's
*discharge* air (downstream of its reheat coil -- the natural point on most VAV controllers, and
the Haystack "discharge air temp" hint), and the box's *entering* primary air (the cold AHU supply)
is mapped to ``MIXED_AIR_TEMP`` -- the convention :mod:`camber.rules.vav_reheat_valve_rule` already
uses. The cold-supply indicator judges the entering air when it is mapped. With only the discharge
mapped it still runs, but reheat warms discharge air, so the count is a lower bound: the rule
caveats it and, when it is the headline (no OAT), never reports a confident "ok" from it.
Occupancy: a trended ``OCCUPANCY`` point replaces the ``start_hour``/``end_hour``/
``occupied_days`` schedule.

**The valve is cross-checked against the discharge-air rise (#63).** A reheat valve reading is only
worth counting if the air says heat went in. Over occupied samples (skipping the first sample after
the valve changes state, while the coil catches up), the rule compares:

* **valve >= 90 % with no rise** -- the median rise below 5 °F. A hot-water reheat coil at full
  valve lifts discharge air 15-30 °F at heating airflow (typical design discharge 85-95 °F off
  55 °F primary air) and still ~10 °F at full cooling airflow, while sensor error plus duct and fan
  gains are only 1-3 °F, so under 5 °F means the heat isn't reaching the air (no hot water, a failed
  actuator, an inverted or mis-mapped point). The reheat penalty is then **declined**: the valve-
  based percentages are withheld (``None``, so nothing is costed) and severity is ``info``.
* **valve <= 5 % with a large rise** -- at least 25 % of closed-valve samples 10 °F or more above
  the entering air. Twice the no-rise bound, and beyond what duct gains or a slightly passing
  valve give, so the valve under-reports the heating: the finding is **caveated**, a confident
  ``ok`` drops to ``info``, and a ``warn``/``fault`` stands as a lower bound.

The rise is discharge (``SUPPLY_AIR_TEMP``) minus the entering primary air (``MIXED_AIR_TEMP``)
when that is mapped. Without it, the reference is the box's own closed-valve discharge median
(>= 12 samples), else a nominal 55 °F primary air (the usual design cooling supply temperature, and
G36's minimum-SAT default); the closed-valve check then only uses cooling-weather samples (OAT >=
70 °F, where a G36 OAT-limited supply air sits at its minimum) and the basis is recorded as lower
confidence. Each check needs >= 12 samples.
"""

from __future__ import annotations

import pandas as pd

from ..model.roles import Role
from ..reheat import analyze_box
from ..schedules import effective_occupied_mask
from ..units import normalize_percent
from .base import Finding

# #63 valve-vs-discharge-rise cross-check (see the module docstring for the physics)
_OPEN_PCT = 90.0  # valve at/above this is "full open"
_CLOSED_PCT = 5.0  # valve at/below this is shut (analyze_box's valve_thr convention)
_NO_RISE_F = 5.0  # median rise under this at full valve: the heat isn't reaching the air
_BIG_RISE_F = 10.0  # rise at/above this with the valve shut: the valve under-reports
_BIG_RISE_SHARE = 0.25  # ... on at least this share of closed-valve samples
_NOMINAL_PRIMARY_F = 55.0  # fallback entering-air reference (design cooling SAT / G36 min SAT)
_COOLING_WEATHER_F = 70.0  # OAT at/above which a G36 OAT-limited SAT sits at its minimum
_MIN_CHECK_SAMPLES = 12

# role -> the legacy column name analyze_box expects
_ROLE_TO_BOX_COL = {
    Role.HEAT_VALVE: "HWValve",
    Role.SPACE_TEMP: "SpaceTemp",
    Role.SUPPLY_AIR_TEMP: "SupplyAir",  # at a terminal: the box discharge (see module doc)
    Role.MIXED_AIR_TEMP: "PrimaryAir",  # at a terminal: the entering primary (AHU supply) air
    Role.HEAT_SP: "ActHeatSP",
    Role.COOL_SP: "ActCoolSP",
    Role.AIRFLOW: "ActFlow",
    Role.AIRFLOW_SP: "ActFlowSP",
    Role.DAMPER: "Damper",
    Role.WARMUP: "WarmUp",
    Role.COOLDOWN: "CoolDown",
    Role.OCCUPANCY: "Occupancy",
}


class ReheatPenalty:
    """Detects terminal-box reheat that coincides with cooling (reheat penalty)
    (PNNL Re-tuning Ch.7)."""

    name = "reheat_penalty"
    roles_required = (Role.HEAT_VALVE,)
    roles_optional = (
        Role.OAT,
        Role.SPACE_TEMP,
        Role.SUPPLY_AIR_TEMP,
        Role.MIXED_AIR_TEMP,
        Role.HEAT_SP,
        Role.COOL_SP,
        Role.AIRFLOW,
        Role.AIRFLOW_SP,
        Role.DAMPER,
        Role.WARMUP,
        Role.COOLDOWN,
        Role.OCCUPANCY,
    )

    def __init__(
        self,
        *,
        start_hour: float = 7,
        end_hour: float = 18,
        occupied_days=(0, 1, 2, 3, 4),
    ):
        # The schedule is only an assumption: a trended OCCUPANCY point replaces it.
        self.start_hour = start_hour
        self.end_hour = end_hour
        self.occupied_days = tuple(occupied_days)

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        cols = {r: c for r, c in _ROLE_TO_BOX_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        # OAT is passed to analyze_box as a separate series, not a column
        oat = frame[Role.OAT] if Role.OAT in frame.columns else None
        res = analyze_box(
            legacy,
            equip,
            oat=oat,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            occupied_days=self.occupied_days,
        )
        if res is None:
            return Finding(
                rule=self.name, equip=equip, severity="info", summary="insufficient data"
            )
        check = self._valve_rise_check(frame, oat)
        if check["valve_dat_consistency"] == "open_no_rise":
            return self._declined(equip, res, check)
        # Headline = reheat at high OAT (heating in cooling weather). Falls back to
        # the cold-supply indicator if no OAT was available.
        hi = res.reheat_at_high_oat_pct
        headline = hi if oat is not None else res.reheat_and_coldsupply_pct
        severity = "fault" if headline >= 20.0 else ("warn" if headline >= 5.0 else "ok")
        caveats: list = []
        cold_metric: float | None = res.reheat_and_coldsupply_pct
        if res.coldsupply_basis == "supply":
            caveats.append(
                "cold-supply indicator judged on SUPPLY_AIR_TEMP, which at a terminal is the box "
                "discharge (downstream of the reheat coil): reheat warms it, so simultaneous "
                "heat/cool is under-counted -- map the entering primary air as MIXED_AIR_TEMP"
            )
            if oat is None and severity == "ok":
                severity = "info"
                cold_metric = None
        elif res.coldsupply_basis is None and oat is None:
            caveats.append(
                "no OAT and no primary/supply air temperature: reheat penalty not evaluated"
            )
            severity = "info" if severity == "ok" else severity
            cold_metric = None
        if check["valve_dat_consistency"] == "closed_with_rise":
            caveats.append(
                f"reheat valve reads shut (<= {_CLOSED_PCT:g}%) on "
                f"{check['valve_closed_big_rise_share']:.0%} of its closed samples while discharge "
                f"air is >= {_BIG_RISE_F:g}°F above {check['_ref_label']}: the valve under-reports "
                "the heating (passing valve, inverted or mis-mapped point), so reheat is likely "
                "under-counted"
            )
            if severity == "ok":
                severity = "info"
        check.pop("_ref_label", None)
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                **check,
                "valve_open_pct": res.valve_open_pct,
                "reheat_at_high_oat_pct": res.reheat_at_high_oat_pct,
                "reheat_and_coldsupply_pct": cold_metric,
                "coldsupply_basis": res.coldsupply_basis,
                "reheat_above_min_flow_pct": res.reheat_above_min_flow_pct,
                "reheat_below_coolsp_pct": res.reheat_below_coolsp_pct,
                "mean_valve_when_open": res.mean_valve_when_open,
                "n_considered": res.n_considered,
            },
            summary=(
                f"{equip}: reheat valve open {res.valve_open_pct:.0f}% of occupied "
                f"hours; "
                + (
                    f"{res.reheat_at_high_oat_pct:.0f}% at OAT>65F"
                    if oat is not None
                    else f"{res.reheat_and_coldsupply_pct:.0f}% into cold supply air"
                    if res.coldsupply_basis is not None
                    else "no OAT or air temperature to judge the penalty"
                )
            ),
            caveats=caveats,
        )

    def _valve_rise_check(self, frame: pd.DataFrame, oat) -> dict:
        """#63: does the discharge-air rise corroborate the reheat valve? (metrics dict)"""
        out: dict = {
            "valve_dat_consistency": "not_checked",
            "valve_dat_basis": None,
            "valve_open_median_rise_f": None,
            "valve_closed_big_rise_share": None,
            "_ref_label": "",
        }
        if Role.SUPPLY_AIR_TEMP not in frame.columns:
            return out
        occ = frame[Role.OCCUPANCY] if Role.OCCUPANCY in frame.columns else None
        keep = effective_occupied_mask(
            frame.index,
            occ=occ,
            start_hour=self.start_hour,
            end_hour=self.end_hour,
            days=self.occupied_days,
            warmup=frame[Role.WARMUP] if Role.WARMUP in frame.columns else None,
            cooldown=frame[Role.COOLDOWN] if Role.COOLDOWN in frame.columns else None,
        )
        keep = pd.Series(keep, index=frame.index).fillna(False).astype(bool)
        v = normalize_percent(pd.to_numeric(frame[Role.HEAT_VALVE], errors="coerce"))
        dat = pd.to_numeric(frame[Role.SUPPLY_AIR_TEMP], errors="coerce")
        dat = dat.where((dat > 30) & (dat < 150))
        is_open, is_closed = v >= _OPEN_PCT, v <= _CLOSED_PCT
        # skip the first sample after a change of state: the coil and sensor lag the valve
        steady = keep & dat.notna()
        if Role.AIRFLOW in frame.columns:
            # with (near) no air through the box, its discharge sensor reads the ceiling, not a
            # coil's output: judge only samples moving at least 10 % of the box's p95 airflow
            flow = pd.to_numeric(frame[Role.AIRFLOW], errors="coerce")
            p95 = flow.quantile(0.95)
            if pd.notna(p95) and p95 > 0:
                steady &= (flow >= 0.10 * p95).fillna(False)
        open_s = steady & is_open & is_open.shift(1, fill_value=False)
        closed_s = steady & is_closed & is_closed.shift(1, fill_value=False)
        ent = None
        if Role.MIXED_AIR_TEMP in frame.columns:
            e = pd.to_numeric(frame[Role.MIXED_AIR_TEMP], errors="coerce")
            e = e.where((e > 30) & (e < 150))
            if e.notna().sum() >= _MIN_CHECK_SAMPLES:
                ent = e
        closed_label = "the entering primary air"
        if ent is not None:
            basis, ref_label = "entering_air", "the entering primary air"
            rise = dat - ent
            open_s &= ent.notna()
            closed_s &= ent.notna()
        else:
            # open check: against the box's own closed-valve discharge when it has enough of it,
            # else a nominal primary air; closed check: only in cooling weather, where the primary
            # air sits at its cold end, against the nominal primary air
            if int(closed_s.sum()) >= _MIN_CHECK_SAMPLES:
                basis, ref_label = "closed_valve_discharge", "its closed-valve discharge"
                ref_open = float(dat[closed_s].median())
            else:
                basis = "nominal_primary_55f"
                ref_label = f"a nominal {_NOMINAL_PRIMARY_F:g}°F primary air"
                ref_open = _NOMINAL_PRIMARY_F
            rise = (dat - ref_open).where(open_s, dat - _NOMINAL_PRIMARY_F)
            if oat is None:
                closed_s = pd.Series(False, index=frame.index)
            else:
                oat_a = pd.to_numeric(oat, errors="coerce").reindex(frame.index).ffill(limit=4)
                closed_s &= (oat_a >= _COOLING_WEATHER_F).fillna(False)
            closed_label = f"a nominal {_NOMINAL_PRIMARY_F:g}°F primary air (OAT >= 70°F)"
        out["valve_dat_basis"] = basis
        out["_ref_label"] = ref_label
        checked = False
        if int(open_s.sum()) >= _MIN_CHECK_SAMPLES:
            checked = True
            med = float(rise[open_s].median())
            out["valve_open_median_rise_f"] = round(med, 2)
            if med < _NO_RISE_F:
                out["valve_dat_consistency"] = "open_no_rise"
                return out
        if int(closed_s.sum()) >= _MIN_CHECK_SAMPLES:
            checked = True
            share = float((rise[closed_s] >= _BIG_RISE_F).mean())
            out["valve_closed_big_rise_share"] = round(share, 3)
            if share >= _BIG_RISE_SHARE:
                out["valve_dat_consistency"] = "closed_with_rise"
                out["_ref_label"] = closed_label
                return out
        if checked:
            out["valve_dat_consistency"] = "consistent"
        return out

    def _declined(self, equip: str, res, check: dict) -> Finding:
        """#63: the valve reads full open but the air shows no rise -- don't count the reheat."""
        ref = check.pop("_ref_label", "")
        rise = check["valve_open_median_rise_f"]
        return Finding(
            rule=self.name,
            equip=equip,
            severity="info",
            metrics={
                **check,
                "declined": True,
                "reason": "reheat valve contradicts the discharge-air rise",
                # the valve-based shares are withheld, so nothing is counted or costed on them
                "valve_open_pct": None,
                "reheat_at_high_oat_pct": None,
                "reheat_and_coldsupply_pct": None,
                "coldsupply_basis": res.coldsupply_basis,
                "reheat_above_min_flow_pct": None,
                "reheat_below_coolsp_pct": None,
                "reported_valve_open_pct": res.valve_open_pct,
                "mean_valve_when_open": res.mean_valve_when_open,
                "n_considered": res.n_considered,
            },
            summary=(
                f"{equip}: declined -- the reheat valve reads >= {_OPEN_PCT:g}% open but discharge "
                f"air rises only {rise:+.1f}°F over {ref} (< {_NO_RISE_F:g}°F); reheat not counted"
            ),
            caveats=[
                f"reheat valve at >= {_OPEN_PCT:g}% with a median discharge-air rise of "
                f"{rise:.1f}°F over {ref}: a working reheat coil at full valve lifts discharge air "
                "well over 10°F, so the valve reading is not corroborated (no hot water, failed "
                "actuator, inverted or mis-mapped point) -- verify the valve before counting reheat"
                + (
                    "; basis is lower confidence without the entering primary air (map it as "
                    "MIXED_AIR_TEMP)"
                    if check["valve_dat_basis"] != "entering_air"
                    else ""
                )
            ],
        )

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: reheat-valve position vs OAT — heating in warm weather stands out."""
        from ..charts.evidence import Evidence

        if Role.HEAT_VALVE in frame.columns and Role.OAT in frame.columns:
            return Evidence(
                renderer="oat_scatter",
                roles=[Role.HEAT_VALVE],
                title=f"{equip}: reheat valve vs OAT",
            )
        return None
