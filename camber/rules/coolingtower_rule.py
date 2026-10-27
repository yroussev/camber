"""Rule: cooling-tower approach (condenser-water supply vs wet-bulb; CTI/ASHRAE).

Flags a cooling tower achieving a persistently high approach at load -- fouled fill,
plugged nozzles, reduced airflow, or an over-loaded tower -- which raises condenser
water temperature, chiller lift, and kW/ton. Adapts
:func:`camber.coolingtower.analyze_cooling_tower_approach` to the role-frame interface.
Wet-bulb is taken from a mapped point or derived from OAT + RH; OAT/RH are
building-level and arrive via the runner's ``shared`` channel. The design approach is
tower/climate-specific, so it is a constructor parameter, not a baked constant.
"""

from __future__ import annotations

import pandas as pd

from ..coolingtower import analyze_cooling_tower_approach
from ..model.roles import Role
from ..units import normalize_percent
from .base import Finding

_ROLE_TO_COL = {
    Role.CW_SUPPLY_TEMP: "CWS_Temp",
    Role.CW_RETURN_TEMP: "CWR_Temp",
    Role.WETBULB_TEMP: "WetBulb",
    Role.OAT: "OAT",
    Role.OUTDOOR_RH: "RH",
    Role.TOWER_FAN_SPEED: "TowerFanSpeed",
}


#: Why the approach rule declines with no operating signal (0.107, #202).
NO_OPERATING_SIGNAL = (
    "no tower fan speed, CW return temperature, pump or chiller run status, or condenser-water "
    "flow is mapped, so idle-tower hours cannot be told from operating ones -- map the tower fan "
    "speed or a run status"
)


class CoolingTowerApproach:
    """Detects a cooling tower running above its design approach at load (CTI/ASHRAE)."""

    name = "cooling_tower_approach"
    roles_required = (Role.CW_SUPPLY_TEMP,)
    roles_optional = (
        Role.WETBULB_TEMP,
        Role.OAT,
        Role.OUTDOOR_RH,
        Role.CW_RETURN_TEMP,
        Role.TOWER_FAN_SPEED,
        # 0.107 (#202): run gates (the #184 plant gates) -- pump, chiller, condenser flow
        Role.PUMP_STATUS,
        Role.COMPRESSOR_STATUS,
        Role.CW_FLOW,
    )

    def __init__(
        self,
        design_approach_f: float = 7.0,
        *,
        min_effort_pct: float | None = 90.0,
        elevation_ft: float | None = None,
        pressure_psia: float | None = None,
    ):
        # Tower/climate-specific: confirm against the tower schedule / selection.
        self.design_approach_f = design_approach_f
        self.min_effort_pct = min_effort_pct
        # A wet-bulb derived from OAT + RH assumes sea level unless the site elevation (or a
        # measured barometric pressure) is given -- see camber.coolingtower.stull_wetbulb_f.
        self.elevation_ft = elevation_ft
        self.pressure_psia = pressure_psia

    def _decline_reason(self, frame: pd.DataFrame) -> tuple[str, str, dict]:
        """Why the analysis returned nothing -- stated as what actually happened.

        Returns ``(summary_tail, caveat, metrics)``. The analysis needs a CW-supply temperature, a
        wet-bulb source, and (when a fan speed is trended) at least 10 samples at high fan effort;
        this names whichever of those was missing rather than assuming it was the fan.
        """
        has_wb = Role.WETBULB_TEMP in frame.columns or (
            Role.OAT in frame.columns and Role.OUTDOOR_RH in frame.columns
        )
        if not has_wb:
            return (
                "no wet-bulb source (need a wet-bulb point, or OAT + RH)",
                "could not evaluate tower approach: no wet-bulb, and no OAT + RH to derive it",
                {},
            )
        fan = normalize_percent(pd.to_numeric(frame[Role.TOWER_FAN_SPEED], errors="coerce"))
        n_high = int((fan >= self.min_effort_pct).sum())
        metrics = {"n_high_effort": n_high}
        if n_high == 0:
            peak = float(fan.max()) if fan.notna().any() else float("nan")
            return (
                f"the tower fan never reached {self.min_effort_pct:.0f}% "
                f"(peak {peak:.0f}%; at part fan the approach is the controller's choice, not the "
                "tower's limit)",
                "could not evaluate tower approach: the fan never reached the high-effort gate",
                metrics,
            )
        return (
            f"only {n_high} sample(s) at >= {self.min_effort_pct:.0f}% fan -- after requiring a "
            "plausible CW-supply and wet-bulb reading at the same time, fewer than the 10 needed "
            "to judge the tower's limit",
            f"could not evaluate tower approach: too few usable samples at high fan effort "
            f"({n_high} reached {self.min_effort_pct:.0f}%, need 10 with valid CW supply and "
            "wet-bulb)",
            metrics,
        )

    @staticmethod
    def _run_gate(frame: pd.DataFrame):
        """``(running mask | None, source)`` from a mapped run signal (0.107, #202), strongest
        first: a pump's run status, a chiller's run status, then the condenser-water flow above
        5 % of its own 95th percentile (the #184 loop-flow cut). ``(None, None)`` when none is
        mapped with data."""
        from ..chwplant import chiller_running
        from .chwplant_rule import LOOP_FLOW_RUN_FRAC, _above_p95

        if not frame.index.is_unique:  # as analyze_cooling_tower_approach: the first sample wins
            frame = frame[~frame.index.duplicated(keep="first")]

        for role, source in (
            (Role.PUMP_STATUS, "pump_status"),
            (Role.COMPRESSOR_STATUS, "chiller_status"),
        ):
            if role in frame.columns:
                run = chiller_running(frame[role], index=frame.index)
                if run is not None:
                    return run, source
        if Role.CW_FLOW in frame.columns:
            run = _above_p95(frame[Role.CW_FLOW], LOOP_FLOW_RUN_FRAC)
            if run is not None:
                return run, "cw_flow"
        return None, None

    @staticmethod
    def _has_operating_gate(frame: pd.DataFrame, run_source) -> bool:
        """True when some signal separates operating from idle hours: the tower fan speed, a CW
        return temperature (the range gate), or a run status / flow (0.107, #202)."""

        def has(role):
            return (
                role in frame.columns and pd.to_numeric(frame[role], errors="coerce").notna().any()
            )

        return run_source is not None or has(Role.TOWER_FAN_SPEED) or has(Role.CW_RETURN_TEMP)

    def _run(self, equip: str, frame: pd.DataFrame, masks_out: dict | None = None):
        run_mask, run_source = self._run_gate(frame)
        if not self._has_operating_gate(frame, run_source):
            return None
        cols = {r: c for r, c in _ROLE_TO_COL.items() if r in frame.columns}
        legacy = frame.rename(columns=cols)
        # a 0-1 fan speed would never clear the percent gates (Registry.run normalizes upstream;
        # a direct analyze() call may not have)
        for col in ("TowerFanSpeed", "RH"):
            if col in legacy.columns:
                legacy[col] = normalize_percent(pd.to_numeric(legacy[col], errors="coerce"))
        return analyze_cooling_tower_approach(
            legacy,
            equip,
            design_approach_f=self.design_approach_f,
            min_effort_pct=self.min_effort_pct,
            elevation_ft=self.elevation_ft,
            pressure_psia=self.pressure_psia,
            masks_out=masks_out,
            run_mask=run_mask,
        )

    def violation_mask(self, frame: pd.DataFrame) -> pd.Series:
        """The judged samples counted in ``pct_hours_high_approach`` (0.102, #119): approach
        above design + 3 F at the operating / high-fan gate. All False when nothing is judged."""
        out: dict = {}
        self._run("", frame, masks_out=out)
        return out.get("high", pd.Series(False, index=frame.index))

    def evidence(self, equip: str, frame: pd.DataFrame):
        """Pattern J: approach vs wet-bulb over the judged samples, the high ones shaded (#119).

        The scatter carries only the samples the rule judged (operating, at high fan effort when
        the fan is trended), on a band from the design approach + 3 F the rule counts against."""
        from ..charts._labels import role_label
        from ..charts.diagnostic import band
        from ..charts.evidence import Evidence

        out: dict = {}
        if self._run(equip, frame, masks_out=out) is None or "judged" not in out:
            return None
        limit = self.design_approach_f + 3.0  # analyze_cooling_tower_approach's high_margin_f
        return Evidence(
            renderer="diagnostic",
            template=band(
                "wetbulb",
                "approach",
                low=-2.0,  # the approach is clipped at 2 F below wet-bulb (sensor noise)
                high=limit,
                name=f"{equip}: tower approach vs design {self.design_approach_f:g} F + 3 F",
                xlabel=role_label(Role.WETBULB_TEMP),
                ylabel="approach: CW supply − wet-bulb (°F)",
            ),
            frame=out["judged"],
            mask=out["high"],
            # 0.111 (#310): pct_hours_high_approach is of these (n_operating)
            judged=pd.Series(True, index=out["judged"].index),
            judged_basis="operating samples",
            label=f"approach > {limit:g} F",
            title=f"{equip}: cooling-tower approach",
        )

    def gate_inputs(self, columns) -> tuple:
        """The wet-bulb source the verdict rests on, for the trust gate (0.111, #320): a
        wet-bulb point, else the OAT and RH it is derived from (optional roles only so that a
        plant without one gets an "insufficient data" finding)."""
        if Role.WETBULB_TEMP in columns:
            return (Role.WETBULB_TEMP,)
        if Role.OAT in columns and Role.OUTDOOR_RH in columns:
            return (Role.OAT, Role.OUTDOOR_RH)
        return ()

    def analyze(self, equip: str, frame: pd.DataFrame) -> Finding:
        """Run the diagnostic on an equipment role-frame; return a Finding."""
        run_mask, run_source = self._run_gate(frame)
        if Role.CW_SUPPLY_TEMP in frame.columns and not self._has_operating_gate(frame, run_source):
            # 0.107 (#202): nothing tells an idle tower from an operating one -- idle water sits
            # wherever the weather leaves it, so it would be read as a high approach
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                metrics={"declined": True, "reason": "no_operating_signal"},
                summary=f"{equip}: approach not judged -- {NO_OPERATING_SIGNAL}",
                caveats=[f"could not evaluate tower approach: {NO_OPERATING_SIGNAL}"],
            )
        res = self._run(equip, frame)
        if res is None:
            if run_source is not None and not bool(run_mask.any()):
                return Finding(
                    rule=self.name,
                    equip=equip,
                    severity="info",
                    metrics={"declined": True, "reason": "never_running", "run_gate": run_source},
                    summary=f"{equip}: approach not judged -- the plant never read as running "
                    f"(gate: {run_source})",
                    caveats=["could not evaluate tower approach: no sample with the plant running"],
                )
            if Role.TOWER_FAN_SPEED in frame.columns and self.min_effort_pct is not None:
                tail, caveat, extra = self._decline_reason(frame)
                return Finding(
                    rule=self.name,
                    equip=equip,
                    severity="info",
                    metrics={"declined": True, **extra},
                    summary=f"{equip}: approach not judged -- {tail}",
                    caveats=[caveat],
                )
            return Finding(
                rule=self.name,
                equip=equip,
                severity="info",
                summary="insufficient data (need CW supply temp + wet-bulb or OAT+RH)",
            )
        caveats = []
        derived_at_sea_level = (
            res.wetbulb_source == "derived"
            and self.elevation_ft is None
            and self.pressure_psia is None
        )
        if derived_at_sea_level:
            caveats.append(
                "wet-bulb derived from OAT + RH assuming sea-level pressure; above sea level it "
                "reads high (~1.4 °F at 500 m, ~2.6 °F at 1600 m in hot, dry air), understating "
                "the approach -- pass elevation_ft to correct it"
            )
        if res.effort_gated is None:
            caveats.append(
                "no tower fan speed trended: hours the tower was deliberately held above its best "
                "approach (a minimum condenser-water temperature in cold weather) could not be "
                "excluded, so a high approach may be correct control"
            )
        ratio = res.approach_median_f / res.design_approach_f if res.design_approach_f else 0.0
        if ratio >= 1.7:
            severity = "fault"
        elif ratio >= 1.3:
            severity = "warn"
        else:
            severity = "ok"
        return Finding(
            rule=self.name,
            equip=equip,
            severity=severity,
            metrics={
                "approach_median_f": res.approach_median_f,
                "design_approach_f": res.design_approach_f,
                "range_median_f": res.range_median_f,
                "pct_hours_high_approach": res.pct_hours_high_approach,
                "wetbulb_source": res.wetbulb_source,
                "n_operating": res.n_operating,
                "effort_gated": res.effort_gated,
                "wetbulb_pressure_basis": (
                    "sea-level"
                    if derived_at_sea_level
                    else "site"
                    if res.wetbulb_source == "derived"
                    else "measured"
                ),
                "n_low_effort_excluded": res.n_low_effort_excluded,
                "run_gate": run_source,
            },
            caveats=caveats,
            summary=(
                f"{equip}: tower approach median {res.approach_median_f:.1f}F"
                f"{' at high fan' if res.effort_gated else ''} "
                f"vs design {res.design_approach_f:.1f}F "
                f"({res.pct_hours_high_approach:.0f}% of operating hours high; "
                f"wet-bulb {res.wetbulb_source})"
            ),
        )
