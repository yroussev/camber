"""Cross-sensor and provenance checks in camber.sensorhealth, each a regression from open data.

Every case reproduces, synthetically, a defect that a real open building dataset exposed and the
trust layer missed: a return-air point copied from supply air, gap-filled outdoor-air flow,
fan-speed percents mapped as airflow, warm mixed-air sensors inside the OAT/RAT envelope, and
zone CO2 reading below the outdoor sensor.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mapping_confidence import review, score_token  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.sensorhealth import (  # noqa: E402
    PHYSICAL_BOUNDS,
    co2_outdoor_consistency,
    copied_signal_consistency,
    cross_unit_identity,
    gapfill_signature,
    mixing_consistency,
    mixing_flow_consistency,
    percent_scale_suspect,
    range_violation_frac,
    sensor_trust,
)


def _idx(n, freq="1h", start="2024-01-01"):
    return pd.date_range(start, periods=n, freq=freq)


def _rtu(n=24 * 30, seed=0, mat_bias=0.0):
    """A healthy RTU: OAT/RAT, a flow-station OA fraction, and MAT from the energy balance."""
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    oat = 45 + 12 * np.sin((h % 24 - 9) / 24 * 2 * np.pi) + rng.normal(0, 1.0, n)
    rat = 72 + rng.normal(0, 0.5, n)
    sa = 15000 + 2000 * rng.random(n)
    f = np.clip(0.35 + 0.1 * np.sin(h / 50) + rng.normal(0, 0.02, n), 0.2, 0.6)
    oa = f * sa
    mat = f * oat + (1 - f) * rat + rng.normal(0, 0.4, n) + mat_bias
    sat = 55 + rng.normal(0, 0.5, n)
    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.RETURN_AIR_TEMP: rat,
            Role.MIXED_AIR_TEMP: mat,
            Role.SUPPLY_AIR_TEMP: sat,
            Role.AIRFLOW: sa,
            Role.OA_AIRFLOW: oa,
        },
        index=_idx(n),
    )


# --- item: copied point ------------------------------------------------------ #


def test_return_air_copied_from_supply_air_is_a_fault():
    """Regression: an RTU's return-air temp was an exact copy of its supply-air temp for 15
    months and nothing flagged it."""
    f = _rtu()
    f.loc[f.index[200:], Role.RETURN_AIR_TEMP] = f[Role.SUPPLY_AIR_TEMP].iloc[200:]
    r = copied_signal_consistency(f)
    assert r.severity == "fault"
    assert set(r.metrics["pairs"][0]["roles"]) == {"return_air_temp", "supply_air_temp"}
    assert r.metrics["pairs"][0]["start"] == str(f.index[200])
    assert r.caveats  # can't tell which is the copy


def test_independent_sensors_are_not_copies():
    r = copied_signal_consistency(_rtu())
    assert r.severity == "ok" and r.metrics["n_pairs_flagged"] == 0


def test_co_flat_equality_is_not_evidence():
    """Both at 0 while the unit is off, both railed -- equality there proves nothing."""
    n = 24 * 10
    zeros = np.zeros(n)
    f = pd.DataFrame({Role.AIRFLOW: zeros, Role.OA_AIRFLOW: zeros.copy()}, index=_idx(n))
    assert copied_signal_consistency(f).severity == "ok"


def test_copied_signal_needs_two_measured_roles():
    f = pd.DataFrame({Role.OAT: np.arange(30.0), Role.COOL_SP: np.arange(30.0)}, index=_idx(30))
    r = copied_signal_consistency(f)  # a setpoint is not a measurement
    assert r.severity == "info" and r.caveats


def test_flow_mixing_declines_on_a_copied_input():
    f = _rtu()
    f[Role.RETURN_AIR_TEMP] = f[Role.SUPPLY_AIR_TEMP]
    r = mixing_flow_consistency(f)
    assert r.severity == "info" and "copy" in r.caveats[0]


# --- item: gap-filled data ---------------------------------------------------- #


def _oa_flow_raw(days_filled=60, days_real=60, seed=0):
    """1-min OA flow: a smooth imputed stretch (every value unique), then a real sensor whose
    reports repeat at its resolution (quantised to 5 cfm)."""
    rng = np.random.default_rng(seed)
    n1, n2 = days_filled * 1440, days_real * 1440
    t = np.arange(n1 + n2)
    base = 6000 + 2500 * np.sin((t % 1440) / 1440 * 2 * np.pi) + 50 * np.sin(t / 7000)
    filled = base[:n1] + rng.normal(0, 3, n1)
    real = np.round((base[n1:] + rng.normal(0, 40, n2)) / 5) * 5
    return pd.Series(np.concatenate([filled, real]), index=_idx(n1 + n2, "1min"))


def test_gap_fill_granularity_change_is_flagged():
    """Regression: gap-filled OA flow scored 'trusted' 0.90-0.94; nothing saw the fill."""
    s = _oa_flow_raw()
    r = gapfill_signature(s)
    assert r.severity == "warn"
    assert "granularity" in r.summary
    classes = [w["class"] for w in r.metrics["windows"]]
    assert classes[0] == "continuous" and classes[-1] == "quantised"
    assert any("screening-grade" in c for c in r.caveats)


def test_genuine_sensor_has_no_gap_fill_signature():
    s = _oa_flow_raw(days_filled=0, days_real=120)
    assert gapfill_signature(s).severity == "ok"


def test_resampled_means_are_not_evaluable_not_clean():
    """Hourly means erase the granularity fingerprint -- say so, don't report 'clean'."""
    s = _oa_flow_raw().resample("1h").mean()
    r = gapfill_signature(s, min_window_samples=200)
    assert r.severity == "ok"
    assert any("not evaluable" in c for c in r.caveats)


def test_repeated_days_are_flagged():
    rng = np.random.default_rng(1)
    day = np.round(6000 + 500 * rng.random(96))
    vals = np.concatenate([np.round(6000 + 500 * rng.random(96)) for _ in range(20)] + [day, day])
    s = pd.Series(vals, index=_idx(len(vals), "15min"))
    r = gapfill_signature(s, min_window_samples=100)
    assert r.severity == "warn" and r.metrics["repeated_days"]


def _units(n=24 * 60, seed=0, shared=True):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    common = 6000 + 3000 * np.clip(np.sin((h % 24 - 6) / 12 * np.pi), 0, None)
    out = {}
    for i, (a, b) in enumerate([(1.0, 0), (1.05, -600), (0.95, 400), (1.1, 200)]):
        if shared:  # an imputed fill: every unit an affine copy of one reconstruction
            v = a * common + b + rng.normal(0, 5, n)
        else:  # independent units: own damper/load wander and flow-station noise
            wander = 800 * np.sin(h / (30 + 7 * i) + i) + rng.normal(0, 250, n)
            v = a * common + b + wander
        out[f"RTU{i + 1}"] = pd.Series(v, index=_idx(n))
    return out


def test_cross_unit_identity_flags_a_shared_fill():
    r = cross_unit_identity(_units(), Role.OA_AIRFLOW)
    assert r.severity == "warn" and r.violation_frac > 0.9
    assert r.caveats


def test_independent_units_are_not_flagged():
    r = cross_unit_identity(_units(shared=False), Role.OA_AIRFLOW)
    assert r.severity == "ok" and not r.metrics["hits"]


def test_shared_ambient_and_commanded_roles_are_not_evaluated():
    u = _units()
    assert cross_unit_identity(u, Role.OAT).severity == "info"
    assert cross_unit_identity(u, Role.SUPPLY_FAN_SPEED).severity == "info"


# --- item: OA airflow bounds + percent mapped as airflow ---------------------- #


def test_oa_airflow_has_bounds():
    assert Role.OA_AIRFLOW in PHYSICAL_BOUNDS
    s = pd.Series([5000.0] * 90 + [-999.0] * 10, index=_idx(100))
    assert range_violation_frac(s, Role.OA_AIRFLOW) == 0.1


def test_percent_signal_mapped_to_airflow_is_suspect():
    rng = np.random.default_rng(0)
    vals = np.round(rng.choice([0, 20, 35, 52], 500) + rng.normal(0, 1, 500)).clip(0, 100)
    pct = pd.Series(vals, index=_idx(500))
    assert percent_scale_suspect(pct, Role.AIRFLOW) is True
    assert percent_scale_suspect(pct / 100.0, Role.OA_AIRFLOW) is True  # a 0-1 fraction too
    assert percent_scale_suspect(_rtu()[Role.AIRFLOW], Role.AIRFLOW) is False
    assert percent_scale_suspect(pct, Role.SUPPLY_FAN_SPEED) is None  # not an airflow role
    assert percent_scale_suspect(pct * 0, Role.AIRFLOW) is None  # all-zero: says nothing
    assert "scale_suspect" in sensor_trust(pct, Role.AIRFLOW).flags


def test_mapping_confidence_catches_percent_typed_as_airflow():
    """Regression: 51 fan-speed % points typed as supply-air-flow sensors rated 'high' 0.95."""
    m = MappingProvider(aliases={"zone_016_fan_spd": Role.AIRFLOW}, patterns=[])
    rng = np.random.default_rng(0)
    pct = pd.Series(rng.choice([0.0, 20.0, 35.0, 52.0], 300), index=_idx(300))
    c = score_token("zone_016_fan_spd", m, pct)
    assert "percent_scale" in c.flags and c.verdict != "high"
    out = review(["zone_016_fan_spd"], m, {"zone_016_fan_spd": pct})
    assert [x.token for x in out["needs_review"]] == ["zone_016_fan_spd"]
    real = score_token("zone_016_fan_spd", m, _rtu()[Role.AIRFLOW])
    assert real.verdict == "high" and real.flags == []


# --- item: flow-based mixing balance ------------------------------------------ #


def test_warm_mat_sensor_passes_order_check_but_fails_flow_balance():
    """Regression: MAT sensors reading 2-4 F warm stayed inside [OAT, RAT] and scored ok."""
    f = _rtu(mat_bias=4.0)
    assert mixing_consistency(f).severity == "ok"  # the blind spot
    r = mixing_flow_consistency(f)
    assert r.severity == "warn"
    assert 3.5 < r.metrics["bias_f"] < 4.5
    assert any("flow-station accuracy unknown" in c for c in r.caveats)


def test_healthy_mixing_passes_flow_balance():
    r = mixing_flow_consistency(_rtu())
    assert r.severity == "ok" and abs(r.metrics["bias_f"]) < 0.5


def test_flow_balance_never_escalates_to_fault():
    r = mixing_flow_consistency(_rtu(mat_bias=15.0))
    assert r.severity == "warn"  # flow-station accuracy is unknown: screening, not a verdict


def test_flow_balance_reports_oa_exceeding_supply():
    f = _rtu()
    f.loc[f.index[:72], Role.OA_AIRFLOW] = f[Role.AIRFLOW].iloc[:72] * 1.3
    r = mixing_flow_consistency(f)
    assert r.metrics["oa_exceeds_supply_frac"] > 0.05


def test_flow_balance_without_flows_is_info_with_caveat():
    f = _rtu().drop(columns=[Role.OA_AIRFLOW])
    r = mixing_flow_consistency(f)
    assert r.severity == "info" and "oa_airflow" in r.caveats[0]


def test_flow_balance_needs_informative_temperature_split():
    f = _rtu()
    f[Role.OAT] = f[Role.RETURN_AIR_TEMP] + 2.0  # OAT ~ RAT: the balance can't tell anything
    r = mixing_flow_consistency(f)
    assert r.severity == "info"


# --- item: indoor CO2 below outdoor ------------------------------------------ #


def _co2_room(n=24 * 12 * 14, offset=0.0, seed=0, occupied_add=250.0):
    """5-min zone and outdoor CO2; the zone rises above outdoor while occupied."""
    rng = np.random.default_rng(seed)
    idx = _idx(n, "5min")
    occ = ((idx.hour >= 9) & (idx.hour < 17) & (idx.dayofweek < 5)).astype(float)
    outdoor = 470 + rng.normal(0, 8, n)
    indoor = outdoor + occupied_add * occ + rng.normal(0, 10, n) + offset
    return pd.DataFrame(
        {Role.CO2: indoor, Role.OUTDOOR_CO2: outdoor, Role.OCCUPANCY: occ}, index=idx
    )


def test_indoor_below_outdoor_is_flagged():
    """Regression: zone CO2 sat at/below the outdoor sensor on 51-79 % of samples, unflagged."""
    f = _co2_room(offset=-120.0)
    r = co2_outdoor_consistency(f.drop(columns=[Role.OCCUPANCY]))
    assert r.severity == "fault"
    assert any("no occupancy mapped" in c for c in r.caveats)


def test_healthy_indoor_outdoor_pair_is_ok():
    r = co2_outdoor_consistency(_co2_room())
    assert r.severity == "ok" and r.metrics["basis"] == "occupied"


def test_occupied_median_below_outdoor_is_an_offset_even_within_spec():
    """Each sample within the sensors' combined accuracy, but the occupied median at/below outdoor:
    occupants only add CO2, so that is a calibration offset between the two."""
    f = _co2_room(offset=-40.0, occupied_add=30.0)
    r = co2_outdoor_consistency(f)
    assert r.severity == "warn"
    assert "offset" in r.summary


def test_co2_consistency_missing_outdoor_is_info():
    f = _co2_room().drop(columns=[Role.OUTDOOR_CO2])
    assert co2_outdoor_consistency(f).severity == "info"


def test_zone_co2_below_ambient_is_flagged_on_trust():
    """A sensor reading under ~380 ppm most of the time is below any outdoor background."""
    rng = np.random.default_rng(0)
    s = pd.Series(360 + rng.normal(0, 10, 500), index=_idx(500))
    assert "below_ambient" in sensor_trust(s, Role.CO2).flags
    assert "below_ambient" not in sensor_trust(s + 100, Role.CO2).flags
