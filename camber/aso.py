"""Advisory automated system optimization (ASO) — diagnosis → suggested corrective action.

FDD says *what's wrong*; this maps an actionable Finding to a **suggested setpoint / sequence
change** an operator can review and apply. It is **advisory and read-only by construction**: it
returns structured recommendations (what to change, a target, the expected effect, and the standard
that motivates it), and never issues a command to the BAS/OT. Closed-loop write-back stays a
roadmap Horizon item — a human stays in the loop.

Each recommendation is grounded: it names the source finding + rule and cites the sequence-of-
operations guidance (ASHRAE Guideline 36 / PNNL Re-tuning) behind the correction. Targets come from
documented, override-able defaults (:data:`DEFAULT_PARAMS`) — no fabricated site-specific values;
where a defensible target can't be given, the action is described qualitatively. Dependency-light
(stdlib); pairs with `camber.rules` (findings), `camber.fault_economics` (what it's worth), and
`camber.soo` (conformance).
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict

_SEV_ORDER = {"ok": 0, "info": 1, "warn": 2, "fault": 3}

#: Documented default targets; override per call via ``params=`` (shallow-merged, top level).
DEFAULT_PARAMS = {
    "hc_deadband_F": 5.0,            # heating/cooling changeover deadband
    "econ_high_limit_F": 65.0,       # economizer OA dry-bulb high limit
    "min_oa_frac": 0.15,             # minimum outdoor-air damper fraction
    "unocc_setback_F": 5.0,          # unoccupied temperature setback/setup
    "cool_sp_raise_F": 2.0,          # zone cooling-setpoint raise to cut overcooling/reheat
    "min_flow_frac": 0.20,           # VAV minimum airflow fraction target
    "sat_reset": {"oat_lo": 0.0, "sat_hi": 65.0, "oat_hi": 60.0, "sat_lo": 55.0},
    "chw_reset_F": {"lo": 42.0, "hi": 48.0},
    "cw_approach_F": 4.0,            # target cooling-tower approach
}


@dataclass
class Recommendation:
    """One advisory corrective action for an actionable finding. Never a BAS command."""

    equip: str
    rule: str                        # the source finding's rule
    severity: str                    # from the finding
    title: str                       # short action
    action: str                      # what to change, specifically
    parameter: str = ""              # the setpoint / sequence to adjust
    suggested: str = ""              # target (may be qualitative)
    expected_effect: str = ""        # qualitative energy / comfort effect
    confidence: str = "medium"       # high | medium | low
    standard: str = ""               # sequence-of-operations citation
    caveats: list = field(default_factory=list)
    advisory: bool = True            # ALWAYS advisory — review + apply by a human; never written

    def as_dict(self) -> dict:
        return asdict(self)


def _rec(f, **kw) -> Recommendation:
    return Recommendation(equip=getattr(f, "equip", ""), rule=getattr(f, "rule", ""),
                          severity=getattr(f, "severity", ""), **kw)


# --------------------------------------------------------------------------- per-archetype recommenders

def _rec_simul_hc(f, frame, P):
    return _rec(f, title="Lock out simultaneous heating and cooling",
                action=(f"Add a heating↔cooling changeover deadband of ≥{P['hc_deadband_F']:g}°F and "
                        "verify coil-valve sequencing so both coils cannot modulate open together."),
                parameter="H/C changeover deadband", suggested=f"≥{P['hc_deadband_F']:g}°F",
                expected_effect="Removes coil-fight waste (heating and cooling cancelling).",
                confidence="high", standard="ASHRAE G36 §5.16 / PNNL Re-tuning Ch.5",
                caveats=["Confirm it isn't intended dehumidification reheat before locking out."])


def _rec_sat_reset(f, frame, P):
    s = P["sat_reset"]
    return _rec(f, title="Enable supply-air-temperature reset",
                action=(f"Reset SAT setpoint on OAT: {s['sat_hi']:g}°F at {s['oat_lo']:g}°F OAT "
                        f"ramping to {s['sat_lo']:g}°F at {s['oat_hi']:g}°F (clamped at the ends)."),
                parameter="SAT setpoint reset schedule",
                suggested=f"{s['sat_hi']:g}→{s['sat_lo']:g}°F over {s['oat_lo']:g}–{s['oat_hi']:g}°F OAT",
                expected_effect="Cuts reheat and chiller lift by raising SAT when cooling load is low.",
                confidence="medium", standard="ASHRAE G36 §5.6 (SAT reset)",
                caveats=["Keep SAT low enough for dehumidification in humid climates."])


def _rec_economizer(f, frame, P):
    return _rec(f, title="Repair / enable the economizer",
                action=(f"Verify the OA dry-bulb high limit (~{P['econ_high_limit_F']:g}°F), free-"
                        f"cooling enable, and damper travel; hold minimum OA ≈{P['min_oa_frac']:.0%} "
                        "when the economizer is locked out."),
                parameter="Economizer high limit + min OA damper",
                suggested=f"high limit ~{P['econ_high_limit_F']:g}°F, min OA ~{P['min_oa_frac']:.0%}",
                expected_effect="Recovers free cooling in mild weather; stops over-ventilation when hot.",
                confidence="medium", standard="ASHRAE G36 §5.1.7 (economizer) / 62.1 (min OA)",
                caveats=["Confirm damper/actuator mechanically travels before changing logic."])


def _rec_reheat(f, frame, P):
    return _rec(f, title="Minimize reheat (raise cooling SAT / lower min airflow)",
                action=(f"Apply G36 reheat minimization: lower the VAV minimum airflow toward "
                        f"~{P['min_flow_frac']:.0%} of max and/or raise cooling SAT before reheating; "
                        "check zones reheating at high OAT."),
                parameter="VAV min airflow / cooling SAT", suggested=f"min flow ~{P['min_flow_frac']:.0%}",
                expected_effect="Reduces the simultaneous cool-then-reheat energy penalty.",
                confidence="medium", standard="ASHRAE G36 §5.6 (trim-&-respond / reheat minimization)",
                caveats=["Keep minimum airflow at/above the ventilation (62.1) requirement."])


def _rec_overcooling(f, frame, P):
    return _rec(f, title="Reduce overcooling at minimum flow",
                action=(f"Raise the zone cooling setpoint ~{P['cool_sp_raise_F']:g}°F and/or lower the "
                        f"VAV minimum airflow toward ~{P['min_flow_frac']:.0%} so the box stops "
                        "overcooling at min flow."),
                parameter="Zone cooling setpoint / min airflow",
                suggested=f"+{P['cool_sp_raise_F']:g}°F cooling SP; min flow ~{P['min_flow_frac']:.0%}",
                expected_effect="Cuts overcooling (and any reheat that compensates).",
                confidence="medium", standard="ASHRAE G36 §5.6 / PNNL Re-tuning",
                caveats=["Respect ventilation minimum airflow (62.1) and comfort (Std-55)."])


def _rec_setback(f, frame, P):
    return _rec(f, title="Add / repair the unoccupied setback",
                action=(f"Program an occupancy schedule that stops the supply fan and setbacks temps "
                        f"~{P['unocc_setback_F']:g}°F when unoccupied (with optimal start/morning "
                        "warm-up)."),
                parameter="Unoccupied schedule + setback",
                suggested=f"fan off + ~{P['unocc_setback_F']:g}°F setback when unoccupied",
                expected_effect="Removes night/weekend runtime — often a large, low-cost saving.",
                confidence="high", standard="ASHRAE G36 §5.1 (occupancy modes) / PNNL Re-tuning",
                caveats=["Keep freeze protection and any process/IAQ purge requirements."])


def _rec_chiller_eff(f, frame, P):
    return _rec(f, title="Improve chiller efficiency (kW/ton)",
                action=("Enable condenser-water and CHW-supply-temperature reset toward design, and "
                        "review staging so machines don't run low on their efficiency curve."),
                parameter="CW / CHW reset + staging",
                suggested=f"CHW reset {P['chw_reset_F']['lo']:g}–{P['chw_reset_F']['hi']:g}°F on load",
                expected_effect="Lowers lift and part-load penalty → fewer kW/ton.",
                confidence="medium", standard="ASHRAE G36 §5.20 / PNNL Re-tuning Ch.8",
                caveats=["Hold minimum CW temp / flow the chiller requires."])


def _rec_reset_generic(f, frame, P):
    return _rec(f, title="Enable the loop reset (trim-and-respond)",
                action=("Enable a trim-and-respond reset of this loop's setpoint from actual demand "
                        "(zone/valve requests), rather than a fixed setpoint."),
                parameter="Loop setpoint reset", suggested="trim-and-respond from demand",
                expected_effect="Reduces pump/fan and plant energy at part load.",
                confidence="medium", standard="ASHRAE G36 §5.1.14 (trim-and-respond)",
                caveats=["Verify sensor calibration before tightening the reset."])


def _rec_cooling_tower(f, frame, P):
    return _rec(f, title="Reset condenser-water / stage tower cells",
                action=(f"Reset condenser-water temperature toward a ~{P['cw_approach_F']:g}°F approach "
                        "and stage additional tower cells/fans before letting the approach widen."),
                parameter="CW reset + tower staging", suggested=f"~{P['cw_approach_F']:g}°F approach",
                expected_effect="Lowers chiller lift (more free tower capacity used).",
                confidence="medium", standard="ASHRAE G36 §5.20 / PNNL Re-tuning Ch.8",
                caveats=["Respect the chiller's minimum condenser-water temperature."])


def _rec_boiler_cycle(f, frame, P):
    return _rec(f, title="Stop boiler short-cycling",
                action=("Widen the firing deadband / raise minimum on-time, stage a lag boiler, and "
                        "enable hot-water-temperature reset so the boiler isn't cycling at low load."),
                parameter="Firing deadband + HW reset", suggested="wider deadband + HW reset on OAT/demand",
                expected_effect="Fewer starts → higher seasonal efficiency and less wear.",
                confidence="medium", standard="ASHRAE G36 / PNNL Re-tuning (boiler)",
                caveats=["Keep the manufacturer's minimum on/off times."])


def _rec_leaking_valve(f, frame, P):
    return _rec(f, title="Repair the leaking valve (maintenance)",
                action=("Inspect and repair/replace the valve or actuator: it passes flow when "
                        "commanded closed. This is a maintenance fix, not a setpoint change."),
                parameter="Valve / actuator", suggested="repair or replace",
                expected_effect="Stops continuous unwanted heating/cooling through the coil.",
                confidence="high", standard="PNNL Re-tuning (valve leakage)",
                caveats=["Confirm the leak isn't a stuck command / bad feedback first."])


def _rec_sat_control(f, frame, P):
    m = getattr(f, "metrics", {}) or {}
    lean = ("under-cooling (coil/valve/airflow can't hit SAT)" if m.get("too_warm_pct", 0)
            >= m.get("too_cold_pct", 0) else "over-cooling / hunting")
    return _rec(f, title="Restore supply-air temperature control",
                action=(f"SAT isn't tracking its setpoint — likely {lean}. Check the coil valve "
                        "travels fully, the SAT sensor calibration, and the loop tuning (P/I "
                        "gains); confirm coil capacity and airflow at the operating point."),
                parameter="SAT loop tuning / coil valve / sensor",
                suggested="tune the SAT loop; verify full coil travel + sensor calibration",
                expected_effect="Stable discharge temperature → stable downstream comfort/energy.",
                confidence="medium", standard="ASHRAE G36 §5.16 (SAT control)",
                caveats=["A miscalibrated SAT sensor mimics a control fault — check it first."])


def _rec_airflow(f, frame, P):
    m = getattr(f, "metrics", {}) or {}
    lean = ("starved (undershooting — check upstream duct static / damper travel)"
            if m.get("undershoot_pct", 0) >= m.get("overshoot_pct", 0)
            else "overshooting (check flow-sensor calibration / min-max limits)")
    return _rec(f, title="Restore VAV airflow control",
                action=(f"Airflow isn't tracking its setpoint — likely {lean}. Verify the damper "
                        "actuator strokes fully, the flow sensor (pitot/ring) calibration, and that "
                        "upstream duct static meets the box's requirement."),
                parameter="Damper / actuator / flow sensor / duct static",
                suggested="verify full damper travel + flow-sensor calibration + duct static",
                expected_effect="Correct delivered airflow → recovers zone comfort and cuts reheat.",
                confidence="medium", standard="ASHRAE G36 §5.6 (VAV airflow control)",
                caveats=["A miscalibrated flow sensor mimics a tracking fault — check it first."])


def _rec_unmet(f, frame, P):
    m = getattr(f, "metrics", {}) or {}
    lean = ("cooling capacity/airflow" if m.get("too_hot_pct", 0) >= m.get("too_cold_pct", 0)
            else "heating capacity/airflow")
    return _rec(f, title="Investigate unmet-setpoint zones (capacity / airflow / control)",
                action=(f"Check {lean}: verify the coil valve reaches full travel, airflow meets the "
                        "request, the setpoint schedule is correct, and the terminal isn't starved by "
                        "low duct static or a stuck damper."),
                parameter="Terminal capacity / airflow / control",
                suggested="restore full coil travel + design airflow",
                expected_effect="Restores comfort (unmet hours) without over-driving neighbors.",
                confidence="medium", standard="ASHRAE G36 / Std-55 (comfort)",
                caveats=["Rule out a space-temp sensor error before a capacity fix."])


def _rec_dcv(f, frame, P):
    return _rec(f, title="Enable / repair demand-controlled ventilation",
                action=("Enable DCV so outdoor air modulates with CO₂ / occupancy, and verify the CO₂ "
                        "sensor calibration and the minimum-OA floor."),
                parameter="DCV control + CO₂ sensor", suggested="modulate OA on CO₂ to a setpoint",
                expected_effect="Cuts over-ventilation conditioning energy while holding IAQ.",
                confidence="medium", standard="ASHRAE 62.1 (DCV) / G36",
                caveats=["Never drop below the code minimum outdoor-air rate."])


#: rule name -> recommender. Rules without an entry yield no recommendation (nothing fabricated).
RECOMMENDERS = {
    "simultaneous_heat_cool": _rec_simul_hc,
    "supply_air_reset": _rec_sat_reset,
    "outdoor_air_fraction": _rec_economizer,
    "reheat_penalty": _rec_reheat,
    "reheat_minimization_g36": _rec_reheat,
    "overcooling_min_flow": _rec_overcooling,
    "overcooling_severity": _rec_overcooling,
    "night_weekend_setback": _rec_setback,
    "unmet_setpoint_hours": _rec_unmet,
    "supply_air_control": _rec_sat_control,
    "airflow_tracking": _rec_airflow,
    "chiller_efficiency": _rec_chiller_eff,
    "condenser_water_reset": _rec_reset_generic,
    "chw_plant_reset": _rec_reset_generic,
    "chw_pump_dp_reset": _rec_reset_generic,
    "hw_pump_dp_reset": _rec_reset_generic,
    "cooling_tower_approach": _rec_cooling_tower,
    "boiler_short_cycle": _rec_boiler_cycle,
    "leaking_valve": _rec_leaking_valve,
    "dcv_verification": _rec_dcv,
}


def recommend(finding, *, frame=None, params: dict | None = None) -> Recommendation | None:
    """Suggest an advisory corrective action for one actionable finding, or None.

    Returns None for a non-actionable finding (``ok``/``info``) or a rule with no recommender.
    ``params`` shallow-merges over :data:`DEFAULT_PARAMS`. The result is advisory — never a command.
    """
    sev = getattr(finding, "severity", "")
    if _SEV_ORDER.get(sev, 0) < _SEV_ORDER["warn"]:
        return None
    fn = RECOMMENDERS.get(getattr(finding, "rule", ""))
    if fn is None:
        return None
    P = {**DEFAULT_PARAMS, **(params or {})}
    return fn(finding, frame, P)


def recommend_findings(findings, *, frame=None, params: dict | None = None,
                       min_severity: str = "warn") -> list:
    """Advisory recommendations for a list of findings at or above ``min_severity`` (worst-first).

    Skips findings with no recommender. Findings are ordered fault-before-warn so the highest-impact
    corrections surface first.
    """
    floor = _SEV_ORDER.get(min_severity, 2)
    ordered = sorted(findings, key=lambda f: -_SEV_ORDER.get(getattr(f, "severity", ""), 0))
    out = []
    for f in ordered:
        if _SEV_ORDER.get(getattr(f, "severity", ""), 0) < floor:
            continue
        rec = recommend(f, frame=frame, params=params)
        if rec is not None:
            out.append(rec)
    return out
