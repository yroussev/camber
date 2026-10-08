"""0.102 evidence-chart fixes: each chart shades what its rule flags, and axes name the role.

#114 -- ``simultaneous_heat_cool`` evidence is on the percent scale with the rule's own 5 %
threshold, and its shaded points are the samples the rule counts.
#115 -- carpet colour bars and evidence axes carry the role's display name and unit.
#119 -- ``leaking_valve``, ``static_pressure_reset``, ``dcv_verification``, ``co2_ventilation``,
``cooling_tower_approach``, ``chw_plant_reset`` and ``boiler_short_cycle`` shade their flagged
samples. Rendering runs headless on Agg.
"""

import os
import sys

import matplotlib

matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.charts._labels import role_label, role_unit  # noqa: E402
from camber.charts.evidence import Evidence, finding_evidence, render_evidence  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.boilercycle_rule import BoilerShortCycle  # noqa: E402
from camber.rules.chwplant_rule import CHWPlantReset  # noqa: E402
from camber.rules.coolingtower_rule import CoolingTowerApproach  # noqa: E402
from camber.rules.iaq_rule import CO2Ventilation  # noqa: E402
from camber.rules.leakvalve_rule import LeakingValve  # noqa: E402
from camber.rules.simul_hc import SimultaneousHeatCool  # noqa: E402
from camber.rules.staticreset_rule import StaticPressureReset  # noqa: E402
from camber.rules.ventilation_rule import DemandControlledVentilation  # noqa: E402


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


def _hours(days=14, start="2025-07-07"):  # a Monday
    return pd.date_range(start, periods=days * 24, freq="1h")


def _shaded(ev: Evidence, frame: pd.DataFrame) -> pd.Series:
    """The mask the rendered chart shades."""
    _, ax = plt.subplots()
    _, mask = render_evidence(ev, frame, ax=ax)
    return pd.Series(mask).fillna(False).astype(bool)


# --------------------------------------------------------------------------- #114


def _hc_frame():
    idx = _hours(7)
    occ = ((idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 17)).astype(float)
    cool = np.where(idx.hour >= 12, 60.0, 0.0)
    heat = np.zeros(len(idx))
    heat[(idx.hour >= 13) & (idx.hour < 15)] = 40.0  # both open: counted while occupied
    heat[(idx.hour >= 15) & (idx.hour < 17)] = 3.0  # cracked open, below the 5 % threshold
    heat[(idx.hour >= 20) & (idx.hour < 22)] = 40.0  # both open, but unoccupied
    return pd.DataFrame(
        {
            Role.COOL_VALVE: cool,
            Role.HEAT_VALVE: heat,
            Role.OCCUPANCY: occ,
        },
        index=idx,
    )


def test_heat_cool_evidence_shades_exactly_the_rule_counted_samples():
    frame = _hc_frame()
    rule = SimultaneousHeatCool()
    finding = rule.analyze("AHU-1", frame)
    ev = finding_evidence(rule, "AHU-1", frame)
    assert ev is not None and ev.renderer == "diagnostic"
    shaded = _shaded(ev, frame)
    flagged = rule.violation_mask(frame)
    assert shaded[shaded].index.equals(flagged[flagged].index)
    # the rule's own count: occupied, both valves above 5 % -- the 3 % heating hours and the
    # unoccupied evening hours are plotted but not shaded
    expect = (
        (frame[Role.OCCUPANCY] > 0) & (frame[Role.COOL_VALVE] > 5) & (frame[Role.HEAT_VALVE] > 5)
    )
    assert flagged.equals(expect.reindex(flagged.index))
    n_considered = finding.metrics["n_considered"]
    assert round(100.0 * shaded.sum() / n_considered, 2) == finding.metrics["simultaneous_hc_pct"]
    assert not shaded[(frame.index.hour >= 15) & (frame.index.hour < 17)].any()


def test_heat_cool_template_is_percent_scale():
    ev = finding_evidence(SimultaneousHeatCool(), "AHU-1", _hc_frame())
    lo, hi = ev.template.expected(np.array([3.0, 60.0]))
    assert list(hi) == [100.0, 5.0] and "%" in ev.template.xlabel and "%" in ev.template.ylabel


def test_heat_cool_dehumidification_samples_are_not_shaded():
    frame = _hc_frame()
    both = (frame[Role.COOL_VALVE] > 5) & (frame[Role.HEAT_VALVE] > 5)
    # the coil leaves well below the return dew point and the heat is added after it, on half of
    # the both-open hours: dehumidification with reheat, reported but not counted
    dehum = both & (frame.index.day % 2 == 0)
    frame[Role.SUPPLY_FAN_STATUS] = 1.0
    frame[Role.SUPPLY_AIR_TEMP] = 58.0
    frame[Role.COOL_COIL_LEAVING_TEMP] = np.where(dehum, 48.0, 58.0)
    frame[Role.RETURN_AIR_TEMP] = 75.0
    frame[Role.RETURN_AIR_HUMIDITY] = 60.0  # dew point ~60 F
    rule = SimultaneousHeatCool()
    finding = rule.analyze("AHU-1", frame)
    shaded = _shaded(finding_evidence(rule, "AHU-1", frame), frame)
    assert finding.metrics["dehum_reheat_pct"] > 0
    assert not shaded[dehum].any() and shaded.any()
    pct = round(100.0 * shaded.sum() / finding.metrics["n_considered"], 2)
    assert pct == pytest.approx(finding.metrics["unexplained_hc_pct"], abs=0.01)


# --------------------------------------------------------------------------- #115


def test_role_label_names_role_and_unit():
    assert role_label(Role.COND_APPROACH_TEMP) == "cond approach temp (°F)"
    assert role_label("COND_APPROACH_TEMP") == "cond approach temp (°F)"
    assert role_label(Role.DUCT_STATIC_SP) == "duct static sp (inH₂O)"
    assert role_label(Role.SUPPLY_FAN_STATUS) == "supply fan status"  # no unit to state
    assert role_label("tons") == "tons"  # a derived column reads as written
    assert role_unit(Role.CO2) == "ppm" and role_unit("tons") == ""


def test_evidence_carpet_colour_bar_names_the_role():
    idx = _hours(14)
    frame = pd.DataFrame({Role.SUPPLY_FAN_STATUS: (idx.hour >= 6).astype(float)}, index=idx)
    fig, ax = plt.subplots()
    render_evidence(Evidence(renderer="carpet", roles=[Role.SUPPLY_FAN_STATUS]), frame, ax=ax)
    labels = [a.get_ylabel() for a in fig.axes if a is not ax]
    assert labels == ["supply fan status"]  # not "Load (kW)"


def test_evidence_scatter_and_trend_axes_carry_units():
    idx = _hours(3)
    frame = pd.DataFrame(
        {Role.OAT: np.linspace(50, 90, len(idx)), Role.CHW_SUPPLY_TEMP: 44.0}, index=idx
    )
    _, ax = plt.subplots()
    render_evidence(Evidence(renderer="oat_scatter", roles=[Role.CHW_SUPPLY_TEMP]), frame, ax=ax)
    assert ax.get_ylabel() == "chw supply temp (°F)"
    _, ax = plt.subplots()
    render_evidence(
        Evidence(renderer="multitrend", roles=[Role.CHW_SUPPLY_TEMP, Role.OAT]), frame, ax=ax
    )
    assert ax.get_ylabel() == "°F"
    assert [t.get_text() for t in ax.get_legend().get_texts()] == [
        "chw supply temp (°F)",
        "oat (°F)",
    ]


def test_diagnostic_axis_falls_back_to_role_label():
    from camber.charts.diagnostic import band, diagnostic_scatter

    idx = _hours(1)
    frame = pd.DataFrame({Role.OAT: np.arange(24.0), Role.COND_APPROACH_TEMP: 5.0}, index=idx)
    _, ax = plt.subplots()
    diagnostic_scatter(frame, band(Role.OAT, Role.COND_APPROACH_TEMP, low=0, high=10), ax=ax)
    assert ax.get_ylabel() == "cond approach temp (°F)" and ax.get_xlabel() == "oat (°F)"


# --------------------------------------------------------------------------- #119


def _check(rule, equip, frame):
    """Evidence exists, shades a non-empty mask, and the rendered mask is the rule's mask."""
    ev = finding_evidence(rule, equip, frame)
    assert ev is not None and ev.mask is not None
    shaded = _shaded(ev, ev.frame if ev.frame is not None else frame)
    flagged = rule.violation_mask(frame)
    assert shaded.any()
    assert shaded[shaded].index.equals(flagged[flagged].index)
    return ev, flagged


def test_leaking_valve_shades_its_leak_samples():
    idx = _hours(7)
    rng = np.random.default_rng(1)
    mat = 70 + rng.normal(0, 0.3, len(idx))
    closed = idx.hour % 3 == 0  # valve shut every third hour
    cool = np.where(closed, 0.0, 60.0)
    sat = np.where(closed, mat - 6.0, 55.0)  # 6 F colder with the valve shut: a leak
    frame = pd.DataFrame(
        {
            Role.COOL_VALVE: cool,
            Role.MIXED_AIR_TEMP: mat,
            Role.SUPPLY_AIR_TEMP: sat,
            Role.SUPPLY_FAN_STATUS: 1.0,
        },
        index=idx,
    )
    rule = LeakingValve()
    f = rule.analyze("AHU-1", frame)
    ev, flagged = _check(rule, "AHU-1", frame)
    assert round(100.0 * flagged.sum() / f.metrics["n_both_closed"], 1) == f.metrics["chw_leak_pct"]
    assert flagged.equals(pd.Series(closed, index=idx))
    assert ev.masks["heating leak"].sum() == 0


def test_static_pressure_reset_shades_held_setpoint_only_when_not_reset():
    idx = _hours(14)
    fan = ((idx.hour >= 6) & (idx.hour < 20)).astype(float)
    frame = pd.DataFrame(
        {Role.DUCT_STATIC_SP: 1.5, Role.SUPPLY_FAN_STATUS: fan}, index=idx
    )  # flat setpoint
    rule = StaticPressureReset()
    assert rule.analyze("AHU-1", frame).metrics["resets"] is False
    _ev, flagged = _check(rule, "AHU-1", frame)
    assert flagged.equals(pd.Series(fan > 0.5, index=idx))  # every fan-on sample judged

    # a setpoint that moves every day with the airflow resets: nothing to shade
    flow = 4000 + 3000 * np.clip(np.sin((idx.hour - 6) / 14 * np.pi), 0, None)
    reset = frame.copy()
    reset[Role.AIRFLOW] = flow
    reset[Role.DUCT_STATIC_SP] = 0.6 + flow / 7000.0
    assert rule.analyze("AHU-1", reset).metrics["resets"] is True
    assert not rule.violation_mask(reset).any()


def test_cooling_tower_shades_the_high_approach_samples():
    idx = _hours(21)
    rng = np.random.default_rng(2)
    wb = 66 + 6 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 0.5, len(idx))
    approach = np.where(idx.day >= 14, 12.0, 6.0) + rng.normal(0, 0.3, len(idx))
    frame = pd.DataFrame({Role.WETBULB_TEMP: wb, Role.CW_SUPPLY_TEMP: wb + approach}, index=idx)
    rule = CoolingTowerApproach()
    f = rule.analyze("CT-1", frame)
    ev, flagged = _check(rule, "CT-1", frame)
    pct = round(100.0 * flagged.sum() / f.metrics["n_operating"], 1)
    assert pct == f.metrics["pct_hours_high_approach"]
    assert ev.renderer == "diagnostic" and "°F" in ev.template.xlabel


def test_chw_plant_reset_shades_held_low_hours_without_reset():
    idx = _hours(14)
    rng = np.random.default_rng(3)
    oat = 70 + 12 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, len(idx))
    chws = 44 + rng.normal(0, 0.2, len(idx))  # flat: no reset
    frame = pd.DataFrame(
        {
            Role.OAT: oat,
            Role.CHW_SUPPLY_TEMP: chws,
            Role.CHW_RETURN_TEMP: chws + 10.0,
            Role.COMPRESSOR_STATUS: 1.0,
        },
        index=idx,
    )
    rule = CHWPlantReset()
    f = rule.analyze("CHW-1", frame)
    assert f.metrics["chwst_reset_present"] is False
    ev, flagged = _check(rule, "CHW-1", frame)
    held = ev.masks["CHWST held low"]
    assert round(100.0 * held.sum() / f.metrics["n_running"], 1) == f.metrics["pct_chwst_low"]
    assert not ev.masks["low deltaT"].any()  # a healthy 10 F deltaT


def test_co2_ventilation_shades_under_ventilated_hours():
    idx = _hours(14)
    occ = (idx.dayofweek < 5) & (idx.hour >= 8) & (idx.hour < 17)
    rise = np.clip(np.sin((idx.hour - 8) / 9 * np.pi), 0, None)
    co2 = np.where(occ, 700 + 650 * rise, 450)
    frame = pd.DataFrame({Role.CO2: co2, Role.OCCUPANCY: occ.astype(float)}, index=idx)
    rule = CO2Ventilation()
    f = rule.analyze("Zone-1", frame)
    ev, flagged = _check(rule, "Zone-1", frame)
    pct = round(100.0 * flagged.sum() / f.metrics["n_occupied"], 1)
    assert pct == f.metrics["under_vent_pct"] and "under-ventilated" in ev.label


def test_dcv_static_shades_high_demand_samples():
    from camber.faultlab import dcv_sim

    frame = dcv_sim(_hours(14), control="static")
    rule = DemandControlledVentilation()
    assert rule.analyze("Room-1", frame).metrics["status"] == "static"
    ev, flagged = _check(rule, "Room-1", frame)
    # every shaded sample is one the verdict judged at high demand
    assert (frame.loc[flagged, Role.CO2] >= 800.0).all() and ev.normalize


def test_boiler_short_cycle_shades_starts_on_busy_days():
    idx = pd.date_range("2025-01-06", periods=4 * 24 * 3, freq="15min")
    cycling = (np.arange(len(idx)) % 3 == 0).astype(float)
    status = np.where(idx.day == 8, 1.0, cycling)  # the third day fires steadily: one start
    frame = pd.DataFrame({Role.BOILER_STATUS: status}, index=idx)
    rule = BoilerShortCycle()
    f = rule.analyze("BLR-1", frame)
    _ev, flagged = _check(rule, "BLR-1", frame)
    running = pd.Series(status > 0.5, index=idx)
    starts = running & ~running.shift(1, fill_value=False)
    assert flagged.equals(starts & (idx.day != 8))
    assert flagged.sum() < f.metrics["n_starts"]
