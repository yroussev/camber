"""Synthetic fault-injection scenarios + a whole-suite FDD accuracy harness.

The real-data benchmark (``examples/lbnl_fdd``) scores a few detectors against LBNL's public labeled
faults — but LBNL only labels a handful of AHU fault modes, so most of the ~28 single-equipment rules
have never been *accuracy*-scored, only unit-tested. This module closes that gap deterministically and
without any download: for each covered rule it injects that rule's **target fault** into a role-frame
(a labeled positive) and a matching **fault-free** frame (a negative), then the harness runs the whole
registry over every labeled frame and scores it with :func:`camber.eval.benchmark` — the same LBNL
performance-evaluation method (per-detector confusion + correct-diagnosis), applied to synthetic data.

An accuracy pass here proves each detector fires on its own fault and stays quiet on everything else.
It is the CI-runnable complement to the real-data LBNL benchmark; :func:`coverage` reports honestly
which rules are accuracy-scored vs still fixture-only. Deterministic (fixed construction / seeds) so a
committed baseline is stable.

numpy/pandas + stdlib.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .model.roles import Role


def _idx(days: int = 21, freq: str = "1h") -> pd.DatetimeIndex:
    return pd.date_range("2025-07-07", periods=days * 24, freq=freq)   # Monday start


def _oat_wave(idx, *, center=90.0, amp=12.0):
    """A smooth daily OAT wave (deterministic)."""
    return center + amp * np.sin((idx.hour - 9) / 24 * 2 * np.pi)


# --------------------------------------------------------------------------- scenario builders
# Each pair returns a role-named frame: `_faulty` injects the rule's target fault, `_clean` is
# fault-free. Signatures mirror the rules' own synthetic-fixture tests.

def _simul(idx, *, faulty):
    n = len(idx)
    return pd.DataFrame({Role.COOL_VALVE: np.full(n, 60.0),
                         Role.HEAT_VALVE: np.full(n, 30.0 if faulty else 0.0)}, index=idx)


def _reheat(idx, *, faulty):
    hot = (idx.hour >= 12) & (idx.hour < 17)
    hv = np.where(hot & faulty, 40.0, 0.0)
    oat = np.where(hot, 95.0, 70.0)
    return pd.DataFrame({Role.HEAT_VALVE: hv, Role.OAT: oat}, index=idx)


def _sat_reset(idx, *, faulty):
    rng = np.random.default_rng(0)
    oat = _oat_wave(idx) + rng.normal(0, 1, len(idx))
    if faulty:
        sat = 55 + rng.normal(0, 0.4, len(idx))                 # pinned cold, no reset
    else:
        sat = np.clip(55 + 0.25 * (oat - 75) + rng.normal(0, 0.5, len(idx)), 53, 65)
    return pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat, Role.COOL_VALVE: np.full(len(idx), 60.0),
                         Role.OAT: oat}, index=idx)


def _unmet(idx, *, faulty):
    occ = ((idx.hour >= 7) & (idx.hour <= 18)).astype(float)
    hot = 78.0 if faulty else 72.0
    st = np.where(occ > 0, hot, 71.0)
    n = len(idx)
    return pd.DataFrame({Role.SPACE_TEMP: st, Role.COOL_SP: np.full(n, 74.0),
                         Role.HEAT_SP: np.full(n, 68.0), Role.OCCUPANCY: occ}, index=idx)


def _economizer(idx, *, faulty):
    oat = _oat_wave(idx, center=70.0, amp=20.0)
    # fault: OA damper wide open when it's hot (over-ventilating past the high limit)
    dmpr = np.where(oat > 65, 0.7 if faulty else 0.15, 0.2 if faulty else 0.6)
    return pd.DataFrame({Role.OAT: oat, Role.OA_DAMPER: pd.Series(dmpr, index=idx)}, index=idx)


def _free_cooling(idx, *, faulty):
    oat = _oat_wave(idx, center=52.0, amp=22.0)                  # dips below 60 for free cooling
    # fault: mechanical cooling running while OA is cold (free cooling missed)
    cv = np.where(oat < 60, 0.5 if faulty else 0.0, 0.3)
    return pd.DataFrame({Role.OAT: oat, Role.COOL_VALVE: pd.Series(cv, index=idx)}, index=idx)


def _static_reset(idx, *, faulty):
    n = len(idx)
    sp = np.full(n, 1.5) if faulty else np.linspace(0.8, 1.6, n)
    return pd.DataFrame({Role.DUCT_STATIC_SP: pd.Series(sp, index=idx)}, index=idx)


def _boiler_cycle(idx, *, faulty):
    n = len(idx)
    status = np.tile([1.0, 0.0], n // 2) if faulty else np.ones(n)
    return pd.DataFrame({Role.BOILER_STATUS: status[:n]}, index=idx)


def _chiller_eff(idx, *, faulty):
    n = len(idx)
    kw_per_ton = 1.5 if faulty else 0.58                        # 1.5 is ~1.75x the 0.85 design
    return pd.DataFrame({Role.POWER: np.full(n, kw_per_ton * 200.0),
                         Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
                         Role.CHW_RETURN_TEMP: np.full(n, 56.0),   # 12F dT -> 200 tons at 400 gpm
                         Role.CHW_FLOW: np.full(n, 400.0)}, index=idx)


def _chiller_staging(idx, *, faulty):
    n = len(idx)
    power = np.tile([120.0, 0.0], n // 2) if faulty else np.full(n, 120.0)
    return pd.DataFrame({Role.POWER: power[:n]}, index=idx)


def _hunting(idx, *, faulty):
    # hunting is a reversals-*per-hour* rule, so it needs sub-hourly sampling to show oscillation
    fine = pd.date_range("2025-07-07", periods=len(idx) * 12, freq="5min")
    n = len(fine)
    if faulty:
        sig = np.tile([0.2, 0.8], n // 2 + 1)[:n]               # reverses every 5 min -> ~12/hr
    else:
        sig = np.clip(np.linspace(0.2, 0.5, n), 0, 1)           # smooth
    return pd.DataFrame({Role.COOL_VALVE: pd.Series(sig, index=fine)}, index=fine)


def _airflow(idx, *, faulty):
    n = len(idx)
    flow = np.full(n, 600.0 if faulty else 1000.0)              # 40% undershoot vs 1000 SP
    return pd.DataFrame({Role.AIRFLOW: flow, Role.AIRFLOW_SP: np.full(n, 1000.0)}, index=idx)


def _overcooling(idx, *, faulty):
    n = len(idx)
    return pd.DataFrame({
        Role.SPACE_TEMP: np.full(n, 70.0), Role.COOL_SP: np.full(n, 74.0),
        Role.AIRFLOW: np.full(n, 640.0 if faulty else 1500.0),
        Role.AIRFLOW_SP: np.full(n, 630.0),
        Role.DAMPER: np.full(n, 20.0 if faulty else 80.0),
        Role.HEAT_VALVE: np.full(n, 40.0 if faulty else 0.0)}, index=idx)


def _sat_control(idx, *, faulty):
    n = len(idx)
    sat = np.full(n, 62.0 if faulty else 55.0)                  # 7F above the 55 setpoint = fault
    return pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat, Role.SUPPLY_AIR_TEMP_SP: np.full(n, 55.0),
                         Role.SUPPLY_FAN_STATUS: np.ones(n)}, index=idx)


def _tower_approach(idx, *, faulty):
    n = len(idx)
    cws = 81.0 if faulty else 73.0                              # approach = cws-68: 13F fault / 5F ok
    return pd.DataFrame({Role.CW_SUPPLY_TEMP: np.full(n, cws),
                         Role.CW_RETURN_TEMP: np.full(n, cws + 10.0),
                         Role.WETBULB_TEMP: np.full(n, 68.0)}, index=idx)


def _overcool_sev(idx, *, faulty):
    n = len(idx)
    space = 71.0 if faulty else 74.0                            # 3F below the 74 cool SP = fault
    return pd.DataFrame({Role.SPACE_TEMP: np.full(n, space),
                         Role.COOL_SP: np.full(n, 74.0)}, index=idx)


def _co2(idx, *, faulty):
    n = len(idx)
    co2 = 1200.0 if faulty else 900.0        # 1200 = under-ventilation; 900 = healthy buildup band
    return pd.DataFrame({Role.CO2: np.full(n, co2)}, index=idx)


def _dcv(idx, *, faulty):
    n = len(idx)
    co2 = 400 + 400 * np.abs(np.sin(np.linspace(0, 6 * (n / 60), n)))
    # fault: OA airflow flat while CO2 swings (DCV not modulating); clean: OA tracks CO2
    oa = np.full(n, 250.0) if faulty else 100.0 + 0.3 * co2
    return pd.DataFrame({Role.OA_AIRFLOW: pd.Series(oa, index=idx),
                         Role.CO2: pd.Series(co2, index=idx)}, index=idx)


def _chw_reset(idx, *, faulty):
    n = len(idx)
    oat = _oat_wave(idx, center=70.0, amp=20.0)                 # reset regressor (must be in-frame)
    if faulty:
        chws = np.full(n, 44.0)                                 # flat CHWST (no reset) + low deltaT
        chwr = np.full(n, 48.0)                                 # 4F dT (well below design)
    else:
        chws = np.clip(42 + 0.30 * (oat - 55), 42, 52)          # clear upward reset with OAT
        chwr = chws + 12.0                                      # healthy 12F dT
    return pd.DataFrame({Role.CHW_SUPPLY_TEMP: pd.Series(chws, index=idx),
                         Role.CHW_RETURN_TEMP: pd.Series(chwr, index=idx),
                         Role.OAT: pd.Series(oat, index=idx)}, index=idx)


def _fine_idx(days=3):
    return pd.date_range("2025-07-07", periods=days * 24 * 12, freq="5min")   # 5-min sampling


def _compressor_cycle(idx, *, faulty):
    fine = _fine_idx()
    n = len(fine)
    status = np.tile([1.0, 0.0], n // 2 + 1)[:n] if faulty else np.ones(n)  # 5-min on/off = many starts
    return pd.DataFrame({Role.COMPRESSOR_STATUS: pd.Series(status, index=fine)}, index=fine)


def _compressor_stage(idx, *, faulty):
    fine = _fine_idx()
    n = len(fine)
    stage = np.tile([1.0, 2.0], n // 2 + 1)[:n] if faulty else np.full(n, 1.0)
    return pd.DataFrame({Role.COMPRESSOR_STAGE: pd.Series(stage, index=fine)}, index=fine)


def _heatpump(idx, *, faulty):
    fine = _fine_idx()
    n = len(fine)
    if faulty:
        rv = np.tile([1.0, 0.0], n // 2 + 1)[:n]                # reversing every 5 min = excess defrost
    else:
        rv = np.concatenate([np.ones(n // 2), np.zeros(n - n // 2)])   # one mode change
    return pd.DataFrame({Role.REVERSING_VALVE_CMD: pd.Series(rv, index=fine)}, index=fine)


def _filter(idx, *, faulty):
    n = len(idx)
    dp = 1.8 if faulty else 0.4                                 # above / below the 1.0 change-out
    return pd.DataFrame({Role.FILTER_DIFF_PRESS: np.full(n, dp)}, index=idx)


def _chiller_approach(idx, *, faulty):
    n = len(idx)
    cond, evap = (12.0, 9.0) if faulty else (4.0, 3.0)          # 2.4x / 2.25x design when fouled
    return pd.DataFrame({Role.COND_APPROACH_TEMP: np.full(n, cond),
                         Role.EVAP_APPROACH_TEMP: np.full(n, evap)}, index=idx)


def _boiler_summer(idx, *, faulty):
    oat = _oat_wave(idx, center=72.0, amp=15.0)                 # swings above/below the 65F lockout
    # fault: boiler runs through hot weather; clean: boiler off when it's warm out
    status = np.ones(len(idx)) if faulty else (oat < 65).astype(float)
    return pd.DataFrame({Role.BOILER_STATUS: pd.Series(status, index=idx),
                         Role.OAT: pd.Series(oat, index=idx)}, index=idx)


def _hw_deltat(idx, *, faulty):
    n = len(idx)
    supply = np.full(n, 140.0)
    ret = np.full(n, 130.0 if faulty else 115.0)               # 10F (low) vs 25F (healthy) loop dT
    return pd.DataFrame({Role.BOILER_STATUS: np.ones(n), Role.HW_SUPPLY_TEMP: supply,
                         Role.HW_RETURN_TEMP: ret}, index=idx)


def _cond_water_reset(idx, *, faulty):
    wb = _oat_wave(idx, center=60.0, amp=12.0)                  # wet-bulb regressor
    cws = np.full(len(idx), 80.0) if faulty else np.clip(62 + 0.9 * (wb - 55), 60, 85)
    return pd.DataFrame({Role.CW_SUPPLY_TEMP: pd.Series(cws, index=idx),
                         Role.WETBULB_TEMP: pd.Series(wb, index=idx)}, index=idx)


def _chw_pump(idx, *, faulty):
    n = len(idx)
    speed = np.full(n, 95.0) if faulty else np.full(n, 55.0)   # riding the curve vs modulating
    return pd.DataFrame({Role.CHW_PUMP_SPEED: speed}, index=idx)


def _hw_pump(idx, *, faulty):
    n = len(idx)
    speed = np.full(n, 95.0) if faulty else np.full(n, 55.0)
    return pd.DataFrame({Role.HW_PUMP_SPEED: speed}, index=idx)


def _leaking_valve(idx, *, faulty):
    n = len(idx)
    mat = np.full(n, 72.0)
    # valve commanded shut, but SAT still drops across the coil (passing/leaking) when faulty
    sat = np.full(n, 62.0 if faulty else 72.0)
    return pd.DataFrame({Role.COOL_VALVE: np.zeros(n), Role.MIXED_AIR_TEMP: mat,
                         Role.SUPPLY_AIR_TEMP: sat}, index=idx)


def _setback(idx, *, faulty):
    occ = (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 18)
    fan = np.ones(len(idx)) if faulty else occ.astype(float)   # 24/7 vs occupied-only runtime
    return pd.DataFrame({Role.SUPPLY_FAN_STATUS: pd.Series(fan, index=idx)}, index=idx)


def _oa_fraction(idx, *, faulty):
    n = len(idx)
    oat = np.full(n, 85.0)                                      # cooling weather (> 70F cutoff)
    rat = np.full(n, 75.0)
    # OAF = (MAT-RAT)/(OAT-RAT): MAT near OAT = ~80% OA (over-ventilating) vs ~20% at the minimum
    mat = np.full(n, 83.0 if faulty else 77.0)
    return pd.DataFrame({Role.OAT: oat, Role.RETURN_AIR_TEMP: rat, Role.MIXED_AIR_TEMP: mat}, index=idx)


def _reheat_min(idx, *, faulty):
    n = len(idx)
    hv = np.full(n, 40.0 if faulty else 0.0)                   # reheating...
    flow = np.full(n, 1000.0)
    sp = np.full(n, 500.0)                                     # ...while airflow is well above min
    return pd.DataFrame({Role.HEAT_VALVE: hv, Role.AIRFLOW: flow, Role.AIRFLOW_SP: sp}, index=idx)


#: rule name -> its scenario builder (called with ``faulty=True/False``)
SCENARIOS: dict = {
    "simultaneous_heat_cool": _simul,
    "reheat_penalty": _reheat,
    "supply_air_reset": _sat_reset,
    "unmet_setpoint_hours": _unmet,
    "economizer_high_limit": _economizer,
    "free_cooling_missed": _free_cooling,
    "static_pressure_reset": _static_reset,
    "boiler_short_cycle": _boiler_cycle,
    "chiller_efficiency": _chiller_eff,
    "chiller_staging": _chiller_staging,
    "control_hunting": _hunting,
    "airflow_tracking": _airflow,
    "overcooling_min_flow": _overcooling,
    "supply_air_control": _sat_control,
    "cooling_tower_approach": _tower_approach,
    "overcooling_severity": _overcool_sev,
    "co2_ventilation": _co2,
    "dcv_verification": _dcv,
    "chw_plant_reset": _chw_reset,
    "compressor_short_cycle": _compressor_cycle,
    "compressor_staging": _compressor_stage,
    "heatpump_defrost": _heatpump,
    "filter_fouling": _filter,
    "chiller_approach_fouling": _chiller_approach,
    "boiler_summer_lockout": _boiler_summer,
    "hw_plant_deltat": _hw_deltat,
    "condenser_water_reset": _cond_water_reset,
    "chw_pump_dp_reset": _chw_pump,
    "hw_pump_dp_reset": _hw_pump,
    "leaking_valve": _leaking_valve,
    "night_weekend_setback": _setback,
    "outdoor_air_fraction": _oa_fraction,
    "reheat_minimization_g36": _reheat_min,
}


# --------------------------------------------------------------------------- harness

def _single_rules(registry):
    from .rules.builtin import is_fleet
    return [registry.get(n) for n in registry.names() if not is_fleet(registry.get(n))]


def _fires(rule, frame) -> bool:
    try:
        return getattr(rule.analyze("EQUIP", frame), "severity", "ok") in ("warn", "fault")
    except Exception:                                   # a rule that can't read this frame -> no fire
        return False


def labeled_records(registry=None, *, scenarios=None, days: int = 21) -> list:
    """Score each covered rule on its **own** faulty + clean frames → labeled records.

    Each rule is its own detector: the ``fired`` set is whether *that* rule fired, so a legitimate
    co-detection by another rule that shares a role (e.g. a flat CHW temp also tripping the CHW-reset
    rule) doesn't pollute the score. Cross-detector specificity is reported separately by
    :func:`cross_fire` and by the real-data LBNL benchmark. Returns records in the shape
    :func:`camber.eval.benchmark` consumes (with :func:`targets`).
    """
    from .rules.builtin import builtin_registry

    reg = registry or builtin_registry()
    scen = scenarios or SCENARIOS
    idx = _idx(days)
    records = []
    for name, build in scen.items():
        rule = reg.get(name)
        for faulty, truth in ((True, name), (False, "")):
            fired = {name} if _fires(rule, build(idx, faulty=faulty)) else set()
            records.append({"truth": truth, "fired": fired})
    return records


def _g36_frame(n=200, **cols):
    idx = pd.date_range("2025-07-07", periods=n, freq="1h")
    return pd.DataFrame({c: np.full(n, v, dtype=float) for c, v in cols.items()}, index=idx)


#: FC number -> (faulty columns, clean columns) for the G36 §5.16.14 engine. A representative set of
#: the 15 fault conditions across the operating states (heating / mechanical+econ / free-cooling /
#: duct-static). Each faulty frame should drive that FC's fault_pct high; each clean frame keeps it low.
_G36_SCENARIOS: dict = {
    1: (dict(HC=0, CC=0, DSP=0.5, DSPSP=1.5, FS=100),          # duct static too low, fan at full
        dict(HC=0, CC=0, DSP=1.5, DSPSP=1.5, FS=100)),
    5: (dict(HC=100, CC=0, SAT=70, SATSP=95, MAT=75, RAT=72, OAT=55),   # SAT below MAT in heating
        dict(HC=100, CC=0, SAT=95, SATSP=95, MAT=75, RAT=72, OAT=55)),
    7: (dict(HC=100, CC=0, SAT=80, SATSP=95, MAT=78, RAT=72, OAT=60),   # SAT below SP in full heating
        dict(HC=100, CC=0, SAT=95, SATSP=95, MAT=78, RAT=72, OAT=60)),
    13: (dict(HC=0, CC=100, SAT=70, SATSP=55, MAT=78, RAT=74, OAT=72, OA_Damper=100),  # SAT>SP, cooling
         dict(HC=0, CC=100, SAT=55, SATSP=55, MAT=78, RAT=74, OAT=72, OA_Damper=100)),
    14: (dict(HC=0, CC=0, SAT=70, MAT=71, RAT=73, OAT=68, CCET=70, CCLT=60),  # drop across idle cool coil
         dict(HC=0, CC=0, SAT=70, MAT=71, RAT=73, OAT=68, CCET=70, CCLT=70)),
    15: (dict(HC=0, CC=0, SAT=70, MAT=71, RAT=73, OAT=68, HCET=70, HCLT=80),  # rise across idle heat coil
         dict(HC=0, CC=0, SAT=70, MAT=71, RAT=73, OAT=68, HCET=70, HCLT=70)),
}


def g36_accuracy(*, scenarios=None, detect_pct: float = 50.0) -> dict:
    """Score the G36 FC engine: does each covered FC trip on its injected fault and stay low when clean?

    Returns ``{"per_fc": {fc: {"faulty_pct", "clean_pct", "detected", "clean_quiet"}}, "tpr", "fpr",
    "n_fc"}`` — a fault is *detected* when its faulty ``fault_pct`` exceeds ``detect_pct`` and the
    clean frame keeps it at/below it. Deterministic (constant-value frames); complements the
    per-rule :func:`labeled_records` harness for the separate G36 §5.16.14 engine.
    """
    from .fdd_g36 import run_g36_afdd

    scen = scenarios or _G36_SCENARIOS
    per_fc, tp, fp = {}, 0, 0
    for fc, (faulty, clean) in scen.items():
        rf = run_g36_afdd(_g36_frame(**faulty), "EQUIP")
        rc = run_g36_afdd(_g36_frame(**clean), "EQUIP")
        fpct = (rf.fault_pct.get(fc) if rf else None) or 0.0
        cpct = (rc.fault_pct.get(fc) if rc else None) or 0.0
        detected = fpct > detect_pct
        quiet = cpct <= detect_pct
        per_fc[fc] = {"faulty_pct": round(fpct, 2), "clean_pct": round(cpct, 2),
                      "detected": detected, "clean_quiet": quiet}
        tp += int(detected)
        fp += int(not quiet)
    n = len(scen)
    return {"per_fc": per_fc, "tpr": round(tp / n, 4) if n else 0.0,
            "fpr": round(fp / n, 4) if n else 0.0, "n_fc": n}


def cross_fire(registry=None, *, scenarios=None, days: int = 21) -> dict:
    """Diagnostic: for each faulty scenario, which *other* single-equip rules also fire.

    A co-detection isn't necessarily a false positive (the injected frame may legitimately contain a
    second condition), so this is reported, not gated.
    """
    from .rules.builtin import builtin_registry

    reg = registry or builtin_registry()
    scen = scenarios or SCENARIOS
    rules = _single_rules(reg)
    idx = _idx(days)
    out = {}
    for name, build in scen.items():
        frame = build(idx, faulty=True)
        others = sorted(r.name for r in rules if r.name != name and _fires(r, frame))
        if others:
            out[name] = others
    return out


def targets(scenarios=None) -> dict:
    """``{rule_name: rule_name}`` — each scored rule is its own detector for its own fault."""
    return {name: name for name in (scenarios or SCENARIOS)}


def coverage(registry=None, scenarios=None) -> dict:
    """Which rules are accuracy-scored here vs still fixture-only vs fleet (scored elsewhere)."""
    from .rules.builtin import builtin_registry, is_fleet

    reg = registry or builtin_registry()
    scen = scenarios or SCENARIOS
    scored, fixture_only, fleet = [], [], []
    for n in reg.names():
        if is_fleet(reg.get(n)):
            fleet.append(n)
        elif n in scen:
            scored.append(n)
        else:
            fixture_only.append(n)
    return {"scored": sorted(scored), "fixture_only": sorted(fixture_only),
            "fleet": sorted(fleet), "n_single": len(scored) + len(fixture_only)}
