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

from dataclasses import asdict, dataclass, field

__all__ = [
    "DEFAULT_PARAMS",
    "Recommendation",
    "RECOMMENDERS",
    "recommend",
    "recommend_findings",
]

_SEV_ORDER = {"ok": 0, "info": 1, "warn": 2, "fault": 3}

#: Documented default targets; override per call via ``params=`` (shallow-merged, top level).
DEFAULT_PARAMS = {
    "hc_deadband_F": 5.0,  # heating/cooling changeover deadband
    "econ_high_limit_F": 65.0,  # economizer OA dry-bulb high limit
    "min_oa_frac": 0.15,  # minimum outdoor-air damper fraction
    "unocc_setback_F": 5.0,  # unoccupied temperature setback/setup
    "cool_sp_raise_F": 2.0,  # zone cooling-setpoint raise to cut overcooling/reheat
    "min_flow_frac": 0.20,  # VAV minimum airflow fraction target
    "sat_reset": {"oat_lo": 0.0, "sat_hi": 65.0, "oat_hi": 60.0, "sat_lo": 55.0},
    "chw_reset_F": {"lo": 42.0, "hi": 48.0},
    "cw_approach_F": 4.0,  # target cooling-tower approach
    # 0.96 (#78): the thresholds the recommenders use to tell *which* cause fired a finding; they
    # mirror the rules' defaults (a rule configured with other thresholds still gets the right
    # cause: when no cause clears its threshold, the largest one present leads)
    "dcv_excess_warn_pct": 50.0,  # dcv_verification excess_warn_pct
    "dcv_breach_fault_pct": 10.0,  # dcv_verification breach_fault_pct
    "dcv_below_floor_fault_pct": 10.0,  # dcv_verification below_floor_fault_pct
    "dcv_unventilated_fault_hours": 4.0,  # dcv_verification unventilated_fault_hours
    "chw_low_dt_warn_pct": 20.0,  # chw_plant_reset: low loop deltaT share that warns
    "pump_near_full_warn_pct": 30.0,  # *_pump_dp_reset: share of running time near full speed
    "pump_near_min_warn_pct": 50.0,  # *_pump_dp_reset: share of running time at the VFD minimum
    # 0.98 (#88): free_cooling_missed stuck_low_oaf_pct -- a damper commanded open that delivers
    # less outdoor air than this reads "stuck low", more reads "stuck part open" (the finding's
    # own recorded threshold wins when present)
    "econ_stuck_low_oaf_pct": 30.0,
}


@dataclass
class Recommendation:
    """One advisory corrective action for an actionable finding. Never a BAS command."""

    equip: str
    rule: str  # the source finding's rule
    severity: str  # from the finding
    title: str  # short action
    action: str  # what to change, specifically
    parameter: str = ""  # the setpoint / sequence to adjust
    suggested: str = ""  # target (may be qualitative)
    expected_effect: str = ""  # qualitative energy / comfort effect
    confidence: str = "medium"  # high | medium | low
    standard: str = ""  # sequence-of-operations citation
    caveats: list = field(default_factory=list)
    advisory: bool = True  # ALWAYS advisory — review + apply by a human; never written
    # 0.96 (#78): ids of linked references (camber.references) to learn more, most specific first
    references: list = field(default_factory=list)
    # 0.98 (#88): the finding's cause in a short phrase ("Outdoor-air damper not modulating (stuck
    # low)"), built from the metrics the recommender reads; ``title`` stays the action. The RCx
    # report heads an issue with the cause.
    cause: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _rec(f, **kw) -> Recommendation:
    return Recommendation(
        equip=getattr(f, "equip", ""),
        rule=getattr(f, "rule", ""),
        severity=getattr(f, "severity", ""),
        **kw,
    )


# --------------------------------------------------------------------------- per-archetype
# recommenders


def _rec_simul_hc(f, frame, P):
    return _rec(
        f,
        title="Lock out simultaneous heating and cooling",
        cause="Heating and cooling coils open together",
        action=(
            f"Add a heating↔cooling changeover deadband of ≥{P['hc_deadband_F']:g}°F and "
            "verify coil-valve sequencing so both coils cannot modulate open together."
        ),
        parameter="H/C changeover deadband",
        suggested=f"≥{P['hc_deadband_F']:g}°F",
        expected_effect="Removes coil-fight waste (heating and cooling cancelling).",
        confidence="high",
        standard="ASHRAE G36 §5.16 / PNNL Re-tuning Ch.5",
        caveats=["Confirm it isn't intended dehumidification reheat before locking out."],
    )


def _rec_sat_reset(f, frame, P):
    """SAT-reset advice that follows why the finding fired (0.96, #78): supply air rising with
    load points at capacity, and a trended setpoint that already resets needs its range or limits
    reviewed, not a reset "enabled"."""
    s = P["sat_reset"]
    m = getattr(f, "metrics", None) or {}
    if m.get("reset_direction") == "rising_with_load":
        return _rec(
            f,
            title="Check cooling capacity (supply air rises with load)",
            cause="Supply air rises with load (short of cooling capacity)",
            action=(
                "Supply-air temperature rises as outdoor temperature rises over cooling hours, "
                "which a reset does not do: the coil or the plant is likely short of capacity on "
                "hot hours. Check the chilled-water supply temperature, whether the cooling valve "
                "sits at full open, and coil airflow before changing the SAT sequence."
            ),
            parameter="Cooling coil / chilled-water capacity",
            suggested="cooling valve below full open with SAT at setpoint on hot hours",
            expected_effect="Supply air holds its setpoint at peak load.",
            confidence="medium",
            standard="ASHRAE G36 §5.16 (SAT control) / PNNL Re-tuning Ch.5",
            caveats=["A miscalibrated SAT or OAT sensor mimics this pattern — check them first."],
        )
    if m.get("sp_behaviour") == "reset" and not m.get("sp_wrong_direction"):
        return _rec(
            f,
            title="Widen the supply-air-temperature reset range",
            cause="Supply-air reset does not reach its upper end",
            action=(
                "The trended SAT setpoint already resets, yet supply air stays cold on most "
                "cooling hours: review the reset's limits and its driver so the setpoint reaches "
                f"its upper end ({s['sat_hi']:g}°F at {s['oat_lo']:g}°F OAT, or the zone-request "
                "equivalent) when cooling load is low, and check that SAT follows the setpoint."
            ),
            parameter="SAT reset limits / driver",
            suggested=f"setpoint reaching ~{s['sat_hi']:g}°F at low load",
            expected_effect="Cuts reheat and chiller lift by raising SAT when cooling load is low.",
            confidence="medium",
            standard="ASHRAE G36 §5.6 (SAT reset)",
            caveats=["Keep SAT low enough for dehumidification in humid climates."],
        )
    return _rec(
        f,
        title="Enable supply-air-temperature reset",
        cause=(
            "Supply-air setpoint resets the wrong way"
            if m.get("sp_wrong_direction")
            else "Supply air held cold (no reset seen)"
        ),
        action=(
            f"Reset SAT setpoint on OAT: {s['sat_hi']:g}°F at {s['oat_lo']:g}°F OAT "
            f"ramping to {s['sat_lo']:g}°F at {s['oat_hi']:g}°F (clamped at the ends)."
        ),
        parameter="SAT setpoint reset schedule",
        suggested=f"{s['sat_hi']:g}→{s['sat_lo']:g}°F over {s['oat_lo']:g}–{s['oat_hi']:g}°F OAT",
        expected_effect="Cuts reheat and chiller lift by raising SAT when cooling load is low.",
        confidence="medium",
        standard="ASHRAE G36 §5.6 (SAT reset)",
        caveats=["Keep SAT low enough for dehumidification in humid climates."],
    )


def _oa_failure_mode(f) -> str:
    """Which way an outside-air finding failed: ``under_ventilation``, ``excess_oa`` or
    ``missed_free_cooling``. Read from the finding's ``failure_mode`` metric when the rule records
    one, else inferred from its metrics (older findings)."""
    m = getattr(f, "metrics", None) or {}
    mode = m.get("failure_mode")
    if mode:
        return str(mode)
    rule = getattr(f, "rule", "")
    if rule == "free_cooling_missed":
        return "missed_free_cooling"
    if rule == "outdoor_air_fraction":
        med, mn = m.get("oaf_median_pct"), m.get("min_oa_pct")
        if isinstance(med, (int, float)) and isinstance(mn, (int, float)) and med < mn - 5.0:
            return "under_ventilation"
    return "excess_oa"


def _rec_economizer(f, frame, P):
    """Economizer / outside-air advice that follows the finding's failure mode."""
    mode = _oa_failure_mode(f)
    m = getattr(f, "metrics", None) or {}
    if mode == "under_ventilation":
        mn = m.get("min_oa_pct")
        med = m.get("oaf_median_pct")
        seen = (
            f" (median OA fraction {med:.0f}% vs a {mn:.0f}% minimum)"
            if isinstance(med, (int, float)) and isinstance(mn, (int, float))
            else ""
        )
        return _rec(
            f,
            title="Restore minimum outside air",
            cause="Outside air below the ventilation minimum",
            action=(
                "Outside air is below the ventilation minimum"
                + seen
                + ". Check the minimum-OA damper position and its actuator and linkage (does it "
                "travel to the commanded minimum?), verify the minimum-OA setpoint against the "
                "design ventilation rate, and confirm the outdoor airflow (measure it, or check "
                "the OA flow station) before changing any logic."
            ),
            parameter="Minimum OA damper position / min-OA setpoint",
            suggested=(
                f"OA fraction at or above the design minimum (~{mn:.0f}%)"
                if isinstance(mn, (int, float))
                else "OA fraction at or above the design minimum"
            ),
            expected_effect="Restores code ventilation and IAQ; may raise conditioning load.",
            confidence="medium",
            standard="ASHRAE 62.1 (minimum outdoor air) / G36 §5.16.4 (minimum OA control)",
            references=["pnnl-guide-min-oa", "pnnl-retuning-ch6"],
            caveats=[
                "A stuck or disconnected damper is a mechanical repair, not a setpoint change.",
                "The temperature-balance OA fraction is noisy when outdoor and return air are "
                "within a few °F; confirm with a flow measurement.",
            ],
        )
    hl = m.get("high_limit_f")
    hl = float(hl) if isinstance(hl, (int, float)) else float(P["econ_high_limit_F"])
    if mode == "missed_free_cooling" and m.get("missed_cause") == "damper_not_delivering":
        # 0.98 (#88): the damper was commanded open and outside air did not arrive -- a repair,
        # not an economizer enable
        return _rec_damper_not_delivering(f, m, P)
    if mode == "missed_free_cooling":
        return _rec(
            f,
            title="Enable economizer free cooling",
            cause=(
                "Economizer not commanded open in free-cooling weather"
                if m.get("missed_cause") == "economizer_not_commanded"
                else "Mechanical cooling in free-cooling weather"
            ),
            action=(
                f"Mechanical cooling ran while outdoor air was below the ~{hl:g}°F high limit. "
                "Check the economizer enable logic and high-limit setting, then verify the OA "
                "damper modulates open when free cooling is available."
            ),
            parameter="Economizer enable / high limit",
            suggested=f"economize below ~{hl:g}°F OAT",
            expected_effect="Recovers free cooling in mild weather.",
            confidence="medium",
            standard="ASHRAE G36 §5.16.2 (economizer)",
            caveats=["Confirm damper/actuator mechanically travels before changing logic."],
        )
    return _rec(
        f,
        title="Lock out the economizer above the high limit",
        cause=(
            "Economizer open above the high limit"
            if getattr(f, "rule", "") == "economizer_high_limit"
            else "Excess outside air admitted"
        ),
        action=(
            f"Excess outside air is admitted when it is hot. Verify the OA dry-bulb high limit "
            f"(~{hl:g}°F, or differential against return air) actually locks the economizer out, "
            f"and that the damper returns to minimum OA (≈{P['min_oa_frac']:.0%} unless the "
            "design minimum is known) when it does."
        ),
        parameter="Economizer high limit + min OA damper",
        suggested=f"lockout above ~{hl:g}°F, damper to design minimum",
        expected_effect="Stops cooling hot outdoor air that did not need to be brought in.",
        confidence="medium",
        standard="ASHRAE G36 §5.16.2.3 (high-limit lockout) / 90.1 §6.5.1.1.3",
        caveats=[
            "Confirm damper/actuator mechanically travels before changing logic.",
            "A high-outside-air design can legitimately sit well open at its minimum.",
        ],
    )


def _rec_damper_not_delivering(f, m, P):
    """0.98 (#88): ``free_cooling_missed`` with the OA damper commanded open while the measured OA
    fraction stayed low -- the damper, actuator or linkage does not deliver what it is told."""
    oaf = m.get("commanded_open_oaf_median_pct")
    thr = m.get("stuck_low_oaf_pct")
    thr = float(thr) if isinstance(thr, (int, float)) else float(P["econ_stuck_low_oaf_pct"])
    if isinstance(oaf, (int, float)):
        how = "stuck low" if oaf < thr else "stuck part open"
        cause = f"Outdoor-air damper not modulating ({how})"
        seen = f", yet the measured outdoor-air fraction stayed near {oaf:.0f}%"
    else:
        cause = "Outdoor-air damper not delivering outside air"
        seen = ", yet the measured outdoor-air fraction stayed low"
    cmd = m.get("cmd_open_pct")
    cmd = f"≥{cmd:g}%" if isinstance(cmd, (int, float)) else "fully"
    share, hours = m.get("commanded_open_pct"), m.get("commanded_open_hours")
    when = (
        f" on {share:.0f}% of the missed free-cooling hours"
        + (f" ({hours:,.0f} h)" if isinstance(hours, (int, float)) else "")
        if isinstance(share, (int, float))
        else " during missed free-cooling hours"
    )
    return _rec(
        f,
        title="Repair the outdoor-air damper or actuator",
        cause=cause,
        action=(
            f"The outdoor-air damper was commanded {cmd} open{when}{seen}: the damper does not "
            "deliver the outside air it is told to. Stroke it from the BAS through its range and "
            "watch the blades, the linkage and the actuator; compare the actuator's feedback with "
            "the command. Repair the damper before changing any economizer logic."
        ),
        parameter="OA damper / actuator / linkage",
        suggested="measured outdoor-air fraction following the damper command",
        expected_effect="Recovers free cooling in mild weather once outside air arrives.",
        confidence="medium",
        standard="PNNL Re-tuning Ch.6 (air-handler economizer)",
        references=["pnnl-guide-economizer", "pnnl-retuning-ch6"],
        caveats=[
            "This is a mechanical repair, not a setpoint change.",
            "A mixed-air sensor in a poorly mixed plenum can read a low outdoor-air fraction too: "
            "check its location (or measure across the mixing box) before replacing parts.",
        ],
    )


def _rec_reheat(f, frame, P):
    if getattr(f, "rule", "") == "reheat_minimization_g36":
        # 0.96 (#78): this finding is reheat with airflow *above* its minimum -- the G36 dual-max
        # heating sequence is missing, so "lower the minimum" is not the fix
        return _rec(
            f,
            title="Implement the dual-maximum heating sequence",
            cause="Reheat with airflow above the heating minimum",
            action=(
                "The box reheats while its airflow is well above the minimum: hold airflow at the "
                "heating minimum while the heating loop raises the discharge temperature first, "
                "and raise airflow toward the heating maximum only after that (the G36 dual-max "
                "sequence). Check the box's heating-mode airflow setpoints and its sequence."
            ),
            parameter="VAV heating-mode airflow sequence",
            suggested="airflow at the heating minimum until the discharge temperature is raised",
            expected_effect="Reduces reheat and fan energy in heating mode.",
            confidence="medium",
            standard="ASHRAE G36 §5.6 (dual-maximum reheat control)",
            caveats=["Keep minimum airflow at/above the ventilation (62.1) requirement."],
        )
    return _rec(
        f,
        title="Minimize reheat (raise cooling SAT / lower min airflow)",
        cause="Cooled supply air reheated at the terminal",
        action=(
            f"Apply G36 reheat minimization: lower the VAV minimum airflow toward "
            f"~{P['min_flow_frac']:.0%} of max and/or raise cooling SAT before reheating; "
            "check zones reheating at high OAT."
        ),
        parameter="VAV min airflow / cooling SAT",
        suggested=f"min flow ~{P['min_flow_frac']:.0%}",
        expected_effect="Reduces the simultaneous cool-then-reheat energy penalty.",
        confidence="medium",
        standard="ASHRAE G36 §5.6 (trim-&-respond / reheat minimization)",
        caveats=["Keep minimum airflow at/above the ventilation (62.1) requirement."],
    )


def _rec_overcooling(f, frame, P):
    """Overcooling advice (0.96, #78): a box already at its minimum airflow cannot be helped by a
    higher cooling setpoint, so the advice is the minimum airflow and the supply-air temperature;
    ``overcooling_severity`` measures depth below setpoint and may be a heating shortfall."""
    if getattr(f, "rule", "") == "overcooling_severity":
        m = getattr(f, "metrics", None) or {}
        heat = (
            " With no reheat valve trended, a space held below its heating setpoint may be a "
            "heating shortfall rather than overcooling: check the heating capacity too."
            if m.get("shortfall_severity") is None
            else ""
        )
        return _rec(
            f,
            title="Investigate zone overcooling",
            cause="Space held below its setpoint",
            action=(
                "The space runs below its setpoint for long stretches. Check whether the box sits "
                f"at its minimum airflow while it does (then lower the minimum toward "
                f"~{P['min_flow_frac']:.0%} or raise the supply-air temperature), the zone "
                "setpoints and schedule, and the space-temperature sensor." + heat
            ),
            parameter="VAV min airflow / supply-air temperature / zone setpoints",
            suggested=f"min flow ~{P['min_flow_frac']:.0%}; SAT reset at low load",
            expected_effect="Cuts overcooling (and any reheat that compensates).",
            confidence="medium",
            standard="ASHRAE G36 §5.6 / PNNL Re-tuning Ch.7",
            caveats=["Respect ventilation minimum airflow (62.1) and comfort (Std-55)."],
        )
    return _rec(
        f,
        title="Reduce overcooling at minimum flow",
        cause="Zone overcooled at minimum airflow",
        action=(
            f"The box overcools while already at its minimum airflow: lower the VAV minimum "
            f"airflow toward ~{P['min_flow_frac']:.0%} of maximum (not below the ventilation "
            "minimum) and/or raise the supply-air temperature (SAT reset) at low load. A higher "
            "zone cooling setpoint does not help a box that is already at its minimum."
        ),
        parameter="VAV min airflow / supply-air temperature",
        suggested=f"min flow ~{P['min_flow_frac']:.0%}; SAT reset at low load",
        expected_effect="Cuts overcooling (and any reheat that compensates).",
        confidence="medium",
        standard="ASHRAE G36 §5.6 / PNNL Re-tuning Ch.7",
        caveats=["Respect ventilation minimum airflow (62.1) and comfort (Std-55)."],
    )


def _rec_setback(f, frame, P):
    run = (getattr(f, "metrics", None) or {}).get("fan_run_unoccupied_pct")
    return _rec(
        f,
        title="Add / repair the unoccupied setback",
        cause=(
            f"Fan runs {run:.0f}% of unoccupied hours"
            if isinstance(run, (int, float)) and run == run
            else "Unoccupied setback missing or ineffective"
        ),
        action=(
            f"Program an occupancy schedule that stops the supply fan and setbacks temps "
            f"~{P['unocc_setback_F']:g}°F when unoccupied (with optimal start/morning "
            "warm-up)."
        ),
        parameter="Unoccupied schedule + setback",
        suggested=f"fan off + ~{P['unocc_setback_F']:g}°F setback when unoccupied",
        expected_effect="Removes night/weekend runtime — often a large, low-cost saving.",
        confidence="high",
        standard="ASHRAE G36 §5.1 (occupancy modes) / PNNL Re-tuning",
        caveats=["Keep freeze protection and any process/IAQ purge requirements."],
    )


def _rec_chiller_eff(f, frame, P):
    kwt = (getattr(f, "metrics", None) or {}).get("kw_per_ton_median")
    return _rec(
        f,
        title="Improve chiller efficiency (kW/ton)",
        cause=(
            f"Chiller efficiency poor (median {kwt:.2f} kW/ton)"
            if isinstance(kwt, (int, float)) and kwt == kwt
            else "Chiller efficiency poor (kW/ton)"
        ),
        action=(
            "Enable condenser-water and CHW-supply-temperature reset toward design, and "
            "review staging so machines don't run low on their efficiency curve."
        ),
        parameter="CW / CHW reset + staging",
        suggested=f"CHW reset {P['chw_reset_F']['lo']:g}–{P['chw_reset_F']['hi']:g}°F on load",
        expected_effect="Lowers lift and part-load penalty → fewer kW/ton.",
        confidence="medium",
        standard="ASHRAE G36 §5.20 / PNNL Re-tuning Ch.8",
        caveats=["Hold minimum CW temp / flow the chiller requires."],
    )


_RESET_CAUSE = {
    "static_pressure_reset": "Duct static pressure setpoint not reset",
    "condenser_water_reset": "Condenser-water temperature not reset",
}


def _rec_reset_generic(f, frame, P):
    return _rec(
        f,
        title="Enable the loop reset (trim-and-respond)",
        cause=_RESET_CAUSE.get(getattr(f, "rule", ""), "Loop setpoint not reset from demand"),
        action=(
            "Enable a trim-and-respond reset of this loop's setpoint from actual demand "
            "(zone/valve requests), rather than a fixed setpoint."
        ),
        parameter="Loop setpoint reset",
        suggested="trim-and-respond from demand",
        expected_effect="Reduces pump/fan and plant energy at part load.",
        confidence="medium",
        standard="ASHRAE G36 §5.1.14 (trim-and-respond)",
        caveats=["Verify sensor calibration before tightening the reset."],
    )


def _rec_chw_tracking(f, frame, P):
    m = getattr(f, "metrics", {}) or {}
    dt, dt_above = m.get("deltaT_median_f"), m.get("deltaT_median_above_f")
    lean = (
        "load beyond the running capacity (the loop deltaT widens while short of setpoint)"
        if dt is not None and dt_above is not None and dt_above > dt + 1.0
        else "capacity or control (check whether the loop deltaT or flow is the limit)"
    )
    return _rec(
        f,
        title="Restore chilled-water supply temperature to setpoint",
        cause="Chilled-water supply above setpoint",
        action=(
            f"Chilled-water supply runs above its setpoint while the plant is on — likely {lean}. "
            "Check chiller staging (a second machine available but not called), the chiller's "
            "own limits (demand limit, condenser conditions, refrigerant charge, fouled tubes), "
            "and whether the setpoint is below what the machine can make; then the air handlers "
            "that report warm supply air."
        ),
        parameter="Chiller staging / capacity / CHW setpoint",
        suggested="stage capacity to hold setpoint; confirm chiller limits and sensor calibration",
        expected_effect="Supply air and zones recover; coil valves stop pinning open.",
        confidence="medium",
        standard="PNNL Building Re-tuning, Ch.8 (chilled-water plant)",
        caveats=["A miscalibrated supply sensor or setpoint mimics a capacity fault."],
    )


def _rec_chw_plant(f, frame, P):
    """``chw_plant_reset`` fires on a low loop deltaT (with or without a reset) or on a flat
    CHWST; the advice follows which (0.96, #78)."""
    m = getattr(f, "metrics", None) or {}
    low = m.get("low_deltaT_pct")
    low = float(low) if isinstance(low, (int, float)) and low == low else 0.0
    reset = m.get("chwst_reset_present")
    low_dt = low >= float(P["chw_low_dt_warn_pct"]) or (reset is not False and low > 0.0)
    # ---- begin 098-plant-chw (#86 item 2): constant flow, reversed reset ----
    # A constant-primary-flow plant has a low loop deltaT at part load by design: no "fix low
    # deltaT" advice for it. A supply temperature that rises with the outdoor temperature is not
    # a flat reset and gets its own advice.
    if m.get("flow_mode") == "constant":
        low_dt = False
    if not low_dt and m.get("chwst_reset_direction") == "reverse":
        slope = m.get("chwst_slope_per_F")
        seen = f" ({slope:+.2f}°F per °F)" if isinstance(slope, (int, float)) else ""
        return _rec(
            f,
            title="Find why the chilled-water supply warms in hot weather",
            cause=(
                "Chilled-water supply warms in hot weather (plant capacity or a reversed reset)"
            ),
            action=(
                f"The chilled-water supply temperature rises as it gets warmer outside{seen}, the "
                "opposite of a reset. Either the plant cannot hold its supply temperature at "
                "high load (check whether it reaches its setpoint, the chillers' capacity and "
                "the condenser side: tower, condenser-water bypass, entering water "
                "temperature), or the reset schedule runs the wrong way. Fix the cause before "
                "tuning a reset."
            ),
            parameter="CHW plant capacity / CHW supply temperature reset direction",
            suggested="supply temperature held in hot weather; any reset lowers it as load rises",
            expected_effect="Coils get the chilled water they need at peak load.",
            confidence="medium",
            standard="PNNL Re-tuning Ch.8 (chilled-water plant)",
            caveats=["A warm chilled-water supply sensor reading high mimics this."],
        )
    # ---- end 098-plant-chw ----
    if low_dt:
        also = (
            " The supply temperature is also flat: once deltaT recovers, reset it on load."
            if reset is False
            else ""
        )
        dt = m.get("deltaT_median_f")
        seen = f" (median {dt:.1f}°F)" if isinstance(dt, (int, float)) else ""
        return _rec(
            f,
            title="Fix low chilled-water loop ΔT",
            cause=f"Low chilled-water loop ΔT ({low:.0f}% of running hours)",
            action=(
                f"The loop deltaT is low{seen} on {low:.0f}% of running hours: water is pumped "
                "around the loop without picking up load. Check for three-way or bypass valves "
                "and open decoupler flow, coil valves that pass water when closed or sit wide "
                "open, fouled coils, and a supply temperature set colder than the coils need."
                + also
            ),
            parameter="Coil valves / bypasses / loop flow",
            suggested="loop deltaT back to its design value",
            expected_effect="Less pumping and fewer chillers staged for the same load.",
            confidence="medium",
            standard="PNNL Re-tuning Ch.8 (chilled-water plant)",
            caveats=["A miscalibrated return-water sensor mimics a low deltaT — check it first."],
        )
    return _rec(
        f,
        title="Reset the chilled-water supply temperature",
        cause="Chilled-water supply temperature held flat",
        action=(
            "The chilled-water supply temperature is held flat. Reset it upward at part load "
            f"({P['chw_reset_F']['lo']:g}–{P['chw_reset_F']['hi']:g}°F) from cooling demand "
            "(the most-open coil valve) or outdoor temperature."
        ),
        parameter="CHW supply temperature reset",
        suggested=f"CHW reset {P['chw_reset_F']['lo']:g}–{P['chw_reset_F']['hi']:g}°F on load",
        expected_effect="Lowers chiller lift at part load.",
        confidence="medium",
        standard="ASHRAE G36 §5.20 / PNNL Re-tuning Ch.8",
        caveats=["Keep the supply cold enough for dehumidification in humid climates."],
    )


def _rec_pump_dp(f, frame, P):
    """``*_pump_dp_reset`` fires on a pump riding near full speed or pinned at its VFD minimum;
    the advice follows which, and never asks to enable a reset that is already there (0.96, #78)."""
    m = getattr(f, "metrics", None) or {}
    full = m.get("pct_running_near_full") or 0.0
    near_min = m.get("pct_running_near_min") or 0.0
    reset = m.get("dp_sp_reset_present")
    loop = "hot-water" if getattr(f, "rule", "").startswith("hw_") else "chilled-water"
    at_min = near_min >= float(P["pump_near_min_warn_pct"]) and full < float(
        P["pump_near_full_warn_pct"]
    )
    if at_min:
        return _rec(
            f,
            title="Right-size the pump (pinned at its minimum speed)",
            cause=f"{loop.capitalize()} pump pinned at its minimum speed",
            action=(
                f"The {loop} pump runs at its VFD minimum speed {near_min:.0f}% of the time: it "
                "is oversized for the load, or the differential-pressure setpoint is lower than "
                "the minimum speed can hold. Review the minimum speed, trim the impeller, or stage "
                "a smaller pump; a lower DP setpoint cannot help a pump already at its minimum."
            ),
            parameter="Pump minimum speed / impeller / staging",
            suggested="pump modulating above its minimum speed",
            expected_effect="Lower pump energy at part load.",
            confidence="medium",
            standard="PNNL Re-tuning Ch.8 (central plant)",
            caveats=["Keep the minimum flow the chillers or boilers require."],
        )
    if reset is True:
        return _rec(
            f,
            title="Find why the pump rides near full speed",
            cause=f"{loop.capitalize()} pump near full speed despite a DP reset",
            action=(
                f"The {loop} pump runs near full speed {full:.0f}% of the time although its "
                "differential-pressure setpoint already resets. Look for the coil valve that "
                "keeps requesting more pressure (a valve stuck open, an undersized coil or a "
                "rogue zone), a DP sensor mounted too close to the pump, and open bypasses."
            ),
            parameter="DP reset requests / sensor location / bypasses",
            suggested="the reset settling below its maximum at part load",
            expected_effect="Reduces pump energy at part load.",
            confidence="medium",
            standard="ASHRAE G36 §5.1.14 (trim-and-respond) / PNNL Re-tuning Ch.8",
            caveats=["Verify sensor calibration before changing the reset."],
        )
    return _rec(
        f,
        title="Reset the pump's differential-pressure setpoint",
        cause=(
            f"{loop.capitalize()} pump near full speed on a fixed DP setpoint"
            if reset is False
            else f"{loop.capitalize()} pump near full speed at part load"
        ),
        action=(
            f"The {loop} pump runs near full speed {full:.0f}% of the time. Reset its "
            "differential-pressure setpoint from demand (trim-and-respond on the most-open "
            "coil valve) rather than holding it fixed."
        ),
        parameter="Loop DP setpoint reset",
        suggested="trim-and-respond from valve demand",
        expected_effect="Reduces pump energy at part load.",
        confidence="medium",
        standard="ASHRAE G36 §5.1.14 (trim-and-respond) / PNNL Re-tuning Ch.8",
        caveats=["Verify sensor calibration before tightening the reset."],
    )


def _rec_cooling_tower(f, frame, P):
    m = getattr(f, "metrics", None) or {}
    if m.get("effort_gated") is True:
        # 0.96 (#78): judged only while the tower fans ran near full -- more fan or a lower
        # condenser-water setpoint cannot close the gap, the tower itself can't make approach
        return _rec(
            f,
            title="Restore cooling-tower capacity (approach high at full fan)",
            cause="Cooling-tower approach high at full fan",
            action=(
                "The approach is wide even while the tower fans run near full speed, so staging "
                "or reset cannot close it. Inspect the tower: fill fouling or scale, blocked or "
                "uneven water distribution (nozzles, basins), condenser-water flow through the "
                "cells, fan and drive condition, and recirculation of discharge air."
            ),
            parameter="Tower fill / water distribution / flow",
            suggested="approach back toward design at full fan",
            expected_effect="Lowers condenser-water temperature and chiller lift.",
            confidence="medium",
            standard="PNNL Re-tuning Ch.8 (central plant)",
            caveats=["Confirm the wet-bulb (or OAT + RH) and CW supply sensors first."],
        )
    return _rec(
        f,
        title="Reset condenser-water / stage tower cells",
        cause="Cooling-tower approach above target",
        action=(
            f"Reset condenser-water temperature toward a ~{P['cw_approach_F']:g}°F approach "
            "and stage additional tower cells/fans before letting the approach widen."
        ),
        parameter="CW reset + tower staging",
        suggested=f"~{P['cw_approach_F']:g}°F approach",
        expected_effect="Lowers chiller lift (more free tower capacity used).",
        confidence="medium",
        standard="ASHRAE G36 §5.20 / PNNL Re-tuning Ch.8",
        caveats=["Respect the chiller's minimum condenser-water temperature."],
    )


def _rec_boiler_cycle(f, frame, P):
    spd = (getattr(f, "metrics", None) or {}).get("starts_per_day")
    return _rec(
        f,
        title="Stop boiler short-cycling",
        cause=(
            f"Boiler short-cycling ({spd:.0f} starts/day)"
            if isinstance(spd, (int, float)) and spd == spd
            else "Boiler short-cycling"
        ),
        action=(
            "Widen the firing deadband / raise minimum on-time, stage a lag boiler, and "
            "enable hot-water-temperature reset so the boiler isn't cycling at low load."
        ),
        parameter="Firing deadband + HW reset",
        suggested="wider deadband + HW reset on OAT/demand",
        expected_effect="Fewer starts → higher seasonal efficiency and less wear.",
        confidence="medium",
        standard="ASHRAE G36 / PNNL Re-tuning (boiler)",
        caveats=["Keep the manufacturer's minimum on/off times."],
    )


def _leak_cause(m: dict) -> str:
    """Which valve leaks: the larger of the heating and cooling leak shares (0.98, #88)."""
    hw, chw = _num(m.get("hw_leak_pct")), _num(m.get("chw_leak_pct"))
    if hw <= 0.0 and chw <= 0.0:
        return "Coil valve passes flow when closed"
    return ("Heating" if hw >= chw else "Cooling") + " valve passes flow when closed"


def _rec_leaking_valve(f, frame, P):
    return _rec(
        f,
        title="Repair the leaking valve (maintenance)",
        cause=_leak_cause(getattr(f, "metrics", None) or {}),
        action=(
            "Inspect and repair/replace the valve or actuator: it passes flow when "
            "commanded closed. This is a maintenance fix, not a setpoint change."
        ),
        parameter="Valve / actuator",
        suggested="repair or replace",
        expected_effect="Stops continuous unwanted heating/cooling through the coil.",
        confidence="high",
        standard="PNNL Re-tuning (valve leakage)",
        caveats=["Confirm the leak isn't a stuck command / bad feedback first."],
    )


def _rec_hunting(f, frame, P):
    worst = (getattr(f, "metrics", {}) or {}).get("worst_signal")
    sig = worst or "the modulating output"
    return _rec(
        f,
        title="Retune the hunting control loop",
        cause=f"Control loop hunting ({worst})" if worst else "Control loop hunting",
        action=(
            f"{sig} reverses direction excessively (unstable loop). Slow the loop "
            "(lower proportional/integral gain) or widen the deadband so the actuator "
            "settles; check for a sticking valve/damper and a noisy sensor."
        ),
        parameter="Loop tuning (P/I gains) / deadband",
        suggested="lower loop gain / widen deadband until it settles",
        expected_effect="Stops actuator hunting — less wear and stable control downstream.",
        confidence="medium",
        standard="ASHRAE G36 (loop tuning) / PNNL Re-tuning",
        caveats=["Rule out mechanical binding or a noisy sensor before retuning."],
    )


def _rec_cohort(f, frame, P):
    outliers = (getattr(f, "metrics", {}) or {}).get("outliers", [])
    who = ", ".join(outliers[:5]) if outliers else "the deviating unit(s)"
    return _rec(
        f,
        title="Investigate the unit(s) deviating from the cohort",
        cause=f"Deviates from its cohort: {who}" if outliers else "Deviates from its cohort",
        action=(
            f"{who} run unlike their peers on this role. Compare setpoints, schedule, "
            "valve/damper travel, and sensor calibration against a typical sibling to "
            "find why this unit differs."
        ),
        parameter="Per-unit setpoints / schedule / calibration vs peers",
        suggested="align the outlier to its cohort's operating pattern",
        expected_effect="Brings a straggler back in line with a proven-good peer group.",
        confidence="low",
        standard="peer/cohort comparison (statistical)",
        caveats=["A whole cohort can share a systematic issue — confirm the peers are right."],
    )


def _rec_sat_control(f, frame, P):
    m = getattr(f, "metrics", {}) or {}
    warm = (m.get("too_warm_pct") or 0) >= (m.get("too_cold_pct") or 0)
    lean = "under-cooling (coil/valve/airflow can't hit SAT)" if warm else "over-cooling / hunting"
    return _rec(
        f,
        title="Restore supply-air temperature control",
        cause=(
            "Supply air runs warm of its setpoint"
            if warm
            else "Supply air runs cold of its setpoint or hunts"
        ),
        action=(
            f"SAT isn't tracking its setpoint — likely {lean}. Check the coil valve "
            "travels fully, the SAT sensor calibration, and the loop tuning (P/I "
            "gains); confirm coil capacity and airflow at the operating point."
        ),
        parameter="SAT loop tuning / coil valve / sensor",
        suggested="tune the SAT loop; verify full coil travel + sensor calibration",
        expected_effect="Stable discharge temperature → stable downstream comfort/energy.",
        confidence="medium",
        standard="ASHRAE G36 §5.16 (SAT control)",
        caveats=["A miscalibrated SAT sensor mimics a control fault — check it first."],
    )


def _rec_airflow(f, frame, P):
    m = getattr(f, "metrics", {}) or {}
    under = (m.get("undershoot_pct") or 0) >= (m.get("overshoot_pct") or 0)
    lean = (
        "starved (undershooting — check upstream duct static / damper travel)"
        if under
        else "overshooting (check flow-sensor calibration / min-max limits)"
    )
    return _rec(
        f,
        title="Restore VAV airflow control",
        cause="VAV airflow below its setpoint" if under else "VAV airflow above its setpoint",
        action=(
            f"Airflow isn't tracking its setpoint — likely {lean}. Verify the damper "
            "actuator strokes fully, the flow sensor (pitot/ring) calibration, and that "
            "upstream duct static meets the box's requirement."
        ),
        parameter="Damper / actuator / flow sensor / duct static",
        suggested="verify full damper travel + flow-sensor calibration + duct static",
        expected_effect="Correct delivered airflow → recovers zone comfort and cuts reheat.",
        confidence="medium",
        standard="ASHRAE G36 §5.6 (VAV airflow control)",
        caveats=["A miscalibrated flow sensor mimics a tracking fault — check it first."],
    )


def _rec_unmet(f, frame, P):
    m = getattr(f, "metrics", {}) or {}
    hot = (m.get("too_hot_pct") or 0) >= (m.get("too_cold_pct") or 0)
    lean = "cooling capacity/airflow" if hot else "heating capacity/airflow"
    return _rec(
        f,
        title="Investigate unmet-setpoint zones (capacity / airflow / control)",
        cause="Zone too warm (setpoint unmet)" if hot else "Zone too cold (setpoint unmet)",
        action=(
            f"Check {lean}: verify the coil valve reaches full travel, airflow meets the "
            "request, the setpoint schedule is correct, and the terminal isn't starved by "
            "low duct static or a stuck damper."
        ),
        parameter="Terminal capacity / airflow / control",
        suggested="restore full coil travel + design airflow",
        expected_effect="Restores comfort (unmet hours) without over-driving neighbors.",
        confidence="medium",
        standard="ASHRAE G36 / Std-55 (comfort)",
        caveats=["Rule out a space-temp sensor error before a capacity fix."],
    )


def _dcv_metrics(f) -> dict:
    """The metrics that say why a DCV finding fired. ``dcv_system_verification`` keeps them per air
    handler under ``per_ahu``; the air handler that set the finding's severity is the first (by
    name) with the worst severity, as the rule picks it."""
    m = getattr(f, "metrics", None) or {}
    per = m.get("per_ahu")
    if not isinstance(per, dict) or not per:
        return m
    worst, pick = -1, None
    for ahu in sorted(per):
        rank = _SEV_ORDER.get(str((per[ahu] or {}).get("severity")), 0)
        if rank > worst:
            worst, pick = rank, ahu
    return dict(per[pick] or {}, _ahu=pick)


def _num(v) -> float:
    return float(v) if isinstance(v, (int, float)) and v == v else 0.0


def _dcv_causes(m: dict, P, severity: str = "") -> list:
    """The causes of a DCV finding, worst first: ``unventilated``, ``below_floor``,
    ``co2_high_at_min``, ``static``, ``uncorrelated``, ``excess_at_low_demand``.

    A cause counts when it clears the rule's default threshold (:data:`DEFAULT_PARAMS`). Only the
    three under-ventilation causes raise a DCV finding to ``fault``, so a ``fault`` with none of
    them over its default threshold (a rule configured with lower thresholds) takes the largest of
    them present; a ``warn`` comes from the status or the excess at low demand."""
    under = {
        "unventilated": (
            _num(m.get("unventilated_high_co2_hours")),
            float(P["dcv_unventilated_fault_hours"]),
        ),
        "below_floor": (_num(m.get("below_floor_pct")), float(P["dcv_below_floor_fault_pct"])),
        "co2_high_at_min": (
            _num(m.get("co2_breach_at_min_pct")),
            float(P["dcv_breach_fault_pct"]),
        ),
    }
    out = [k for k, (v, thr) in under.items() if v >= thr]
    if not out and severity == "fault":
        present = [(v / thr if thr else v, k) for k, (v, thr) in under.items() if v > 0.0]
        if present:
            out.append(max(present)[1])
    status = m.get("status")
    if status in ("static", "uncorrelated"):
        out.append(status)
    excess = _num(m.get("excess_at_low_demand_pct"))
    if status == "functioning" and (
        excess >= float(P["dcv_excess_warn_pct"]) or (severity == "warn" and excess > 0.0)
    ):
        out.append("excess_at_low_demand")
    return out


_DCV_ALSO = {
    "unventilated": "occupied hours with high CO₂ and no ventilation (fan off or OA shut)",
    "below_floor": "outdoor air below its floor",
    "co2_high_at_min": "CO₂ above its setpoint while outdoor air sat at minimum",
    "static": "outdoor air that does not modulate with demand",
    "uncorrelated": "outdoor air that modulates, but not with demand",
    "excess_at_low_demand": "outdoor air above its floor at low demand",
}


def _rec_dcv(f, frame, P):
    """DCV advice that follows why the finding fired (0.96, #78): a functioning DCV whose outdoor
    air stays above its floor at low demand is told to lower that floor, and under-ventilation is
    told to restore outdoor air -- never "enable DCV" when the finding shows it working."""
    m = _dcv_metrics(f)
    causes = _dcv_causes(m, P, str(m.get("severity") or getattr(f, "severity", "")))
    lead = causes[0] if causes else m.get("status") or "static"
    where = f" ({m['_ahu']})" if m.get("_ahu") else ""
    also = [_DCV_ALSO[c] for c in causes[1:] if c in _DCV_ALSO]
    tail = (" Also seen: " + "; ".join(also) + ".") if also else ""
    never = "Never drop below the code minimum outdoor-air rate."
    if lead == "unventilated":
        h = _num(m.get("unventilated_high_co2_hours"))
        return _rec(
            f,
            title="Restore ventilation during occupied hours",
            cause="Occupied with high CO₂ and no ventilation",
            action=(
                f"The space was occupied with high CO₂ and no ventilation{where} for about "
                f"{h:.0f} h (supply fan off or outdoor-air damper shut). Check the fan schedule "
                "against actual occupancy, fan-start interlocks and alarms, and that the "
                "outdoor-air damper opens whenever the fan runs in occupied mode." + tail
            ),
            parameter="Occupied schedule / fan start / OA damper",
            suggested="fan on and outdoor air at or above the minimum whenever occupied",
            expected_effect="Restores ventilation and IAQ; may raise conditioning load.",
            confidence="medium",
            standard="ASHRAE 62.1 (minimum outdoor air) / PNNL Re-tuning",
            caveats=["Confirm the occupancy signal before changing the schedule."],
        )
    if lead == "below_floor":
        pct = _num(m.get("below_floor_pct"))
        return _rec(
            f,
            title="Restore the minimum outdoor-air floor",
            cause="Outdoor air below its floor",
            action=(
                f"Outdoor air is below its floor{where} on {pct:.0f}% of occupied samples. Check "
                "the minimum-OA damper position and actuator travel, the DCV lower limit and the "
                "minimum-OA setpoint against the design ventilation rate, and measure the "
                "outdoor airflow before changing logic." + tail
            ),
            parameter="Minimum OA setpoint / DCV lower limit / damper",
            suggested="outdoor air at or above the area-based (Ra·Az) floor when occupied",
            expected_effect="Restores code ventilation and IAQ; may raise conditioning load.",
            confidence="medium",
            standard="ASHRAE 62.1 (minimum outdoor air) / G36 §5.16.4 (minimum OA control)",
            caveats=["A stuck or disconnected damper is a mechanical repair, not a setpoint."],
        )
    if lead == "co2_high_at_min":
        pct = _num(m.get("co2_breach_at_min_pct"))
        return _rec(
            f,
            title="Make outdoor air respond to high CO₂",
            cause="CO₂ high while outdoor air sits at minimum",
            action=(
                f"CO₂ stayed above its setpoint while outdoor air sat at minimum{where} on "
                f"{pct:.0f}% of samples: DCV is not raising outdoor air when demand is high. "
                "Check that DCV is enabled, the CO₂ sensor's calibration and location, the DCV "
                "loop's setpoint and output limits, and the damper's travel." + tail
            ),
            parameter="DCV enable / CO₂ setpoint / sensor",
            suggested="outdoor air rising when CO₂ exceeds its setpoint",
            expected_effect="Restores ventilation and IAQ at high occupancy.",
            confidence="medium",
            standard="ASHRAE 62.1 (DCV) / G36 §5.16.4",
            caveats=[never],
        )
    if lead == "excess_at_low_demand":
        pct = _num(m.get("excess_at_low_demand_pct"))
        return _rec(
            f,
            title="Lower the minimum outdoor air at low demand",
            cause="Outdoor air above its floor at low demand",
            action=(
                "DCV responds to demand, but outdoor air stays above its floor at low demand"
                f"{where} on {pct:.0f}% of low-demand samples. Review the DCV lower limit and the "
                "minimum-OA setpoint against the area-based (Ra·Az) floor from the design, and "
                "the damper's minimum position, so outdoor air falls to that floor when CO₂ is "
                "low." + tail
            ),
            parameter="DCV lower limit / minimum OA setpoint",
            suggested="outdoor air at the area-based (Ra·Az) floor when CO₂ is low",
            expected_effect="Cuts over-ventilation conditioning energy at low occupancy.",
            confidence="medium",
            standard="ASHRAE 62.1 (DCV) / G36 §5.16.4",
            caveats=[
                never,
                "Confirm the floor from the design (area, space type) before lowering it.",
            ],
        )
    if lead == "uncorrelated":
        return _rec(
            f,
            title="Tie outdoor air to demand",
            cause="Outdoor air not following CO₂ demand",
            action=(
                f"Outdoor air modulates{where}, but not with CO₂ / occupancy. Check which input "
                "drives the damper (a schedule, the economizer, another sensor), the CO₂ "
                "sensor's calibration and location, and that the DCV loop is enabled." + tail
            ),
            parameter="DCV input / CO₂ sensor",
            suggested="modulate OA on CO₂ to a setpoint",
            expected_effect="Cuts over-ventilation conditioning energy while holding IAQ.",
            confidence="medium",
            standard="ASHRAE 62.1 (DCV) / G36",
            caveats=[never],
        )
    return _rec(
        f,
        title="Enable / repair demand-controlled ventilation",
        cause="Outdoor air not modulating with demand",
        action=(
            "Enable DCV so outdoor air modulates with CO₂ / occupancy, and verify the CO₂ "
            "sensor calibration and the minimum-OA floor." + tail
        ),
        parameter="DCV control + CO₂ sensor",
        suggested="modulate OA on CO₂ to a setpoint",
        expected_effect="Cuts over-ventilation conditioning energy while holding IAQ.",
        confidence="medium",
        standard="ASHRAE 62.1 (DCV) / G36",
        caveats=[never],
    )


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
    "control_hunting": _rec_hunting,
    "cohort_airflow": _rec_cohort,
    "cohort_space_temp": _rec_cohort,
    "economizer_high_limit": _rec_economizer,
    "free_cooling_missed": _rec_economizer,
    "static_pressure_reset": _rec_reset_generic,
    "chiller_efficiency": _rec_chiller_eff,
    "condenser_water_reset": _rec_reset_generic,
    "chw_plant_reset": _rec_chw_plant,
    "chw_supply_tracking": _rec_chw_tracking,
    "chw_pump_dp_reset": _rec_pump_dp,
    "hw_pump_dp_reset": _rec_pump_dp,
    "cooling_tower_approach": _rec_cooling_tower,
    "boiler_short_cycle": _rec_boiler_cycle,
    "leaking_valve": _rec_leaking_valve,
    "dcv_verification": _rec_dcv,
    "dcv_system_verification": _rec_dcv,
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
    rec = fn(finding, frame, P)
    if rec is not None and not rec.references:
        from .references import reference_ids_for

        rec.references = reference_ids_for(rec.rule)
    return rec


def recommend_findings(
    findings, *, frame=None, params: dict | None = None, min_severity: str = "warn"
) -> list:
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
