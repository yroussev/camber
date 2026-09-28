"""Tests for the LBNL benchmark's drift-scoring plumbing (no dataset download).

The real-data drift scoring runs in the benchmark CI job with the ~580 MB LBNL CSVs present; these
lock the case-builder + scorer *logic* deterministically on synthetic SDAHU-shaped role-frames, so
the harness is validated without the download. Mirrors tests/test_bdg2_benchmark.py.
"""

import importlib.util
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.driftvalidation import evaluate  # noqa: E402
from camber.model.roles import Role  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REAL_DATA = os.path.join(_ROOT, "examples", "_data", "lbnl", "sdahu", "AHU_annual.csv")


def _bench():
    path = os.path.join(_ROOT, "examples", "lbnl_fdd", "benchmark.py")
    spec = importlib.util.spec_from_file_location("lbnl_benchmark", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _sdahu_frame(n=480, *, seed=0, coil_leak=0.0, damper_stuck=None):
    """A synthetic SDAHU role-frame with the points the AHU-drift detectors need."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    t = np.arange(n)
    valve = np.clip(50 + 45 * np.sin(t / 12) + rng.normal(0, 3, n), 0, 100)  # command sweep
    damper = (
        np.full(n, damper_stuck)
        if damper_stuck is not None
        else np.clip(30 + 25 * np.sin(t / 17) + rng.normal(0, 3, n), 0, 100)
    )
    oat = 55 + 20 * np.sin(t / 24) + rng.normal(0, 1, n)
    rat = 72 + rng.normal(0, 0.5, n)
    df = damper / 100.0
    mat = df * oat + (1 - df) * rat + rng.normal(0, 0.5, n)
    sat = mat - valve * 0.12 - coil_leak * (1 - valve / 100.0) * 8 + rng.normal(0, 0.4, n)
    return pd.DataFrame(
        {
            Role.COOL_VALVE: valve,
            Role.OA_DAMPER: damper,
            Role.OAT: oat,
            Role.RETURN_AIR_TEMP: rat,
            Role.MIXED_AIR_TEMP: mat,
            Role.SUPPLY_AIR_TEMP: sat,
            Role.DUCT_STATIC: 1.2 + rng.normal(0, 0.05, n),
            Role.AIRFLOW: np.clip(3000 + 500 * np.sin(t / 12), 1000, 5000),
        },
        index=idx,
    )


def _frames():
    return {
        "AHU_annual.csv": _sdahu_frame(seed=1),
        "coi_leakage_010_annual.csv": _sdahu_frame(seed=2, coil_leak=1.0),
        "damper_stuck_100_annual_short.csv": _sdahu_frame(seed=3, damper_stuck=95.0),
    }


def test_build_drift_cases_labels_positive_and_cross_negative():
    B = _bench()
    cases = B.build_drift_cases(_frames(), B.DRIFT_DETECTORS["economizer_damper_drift"])
    # fault-free-tail (negative) + damper_stuck (positive) + coi_leakage (cross-negative)
    assert len(cases) == 3
    by_name = {c.name: c.fault for c in cases}
    assert by_name["fault-free-tail"] is False
    assert any(c.fault for c in cases if c.name.startswith("damper_stuck"))
    assert all(not c.fault for c in cases if c.name.startswith("coi_leakage"))


def test_one_sided_detectors_do_not_claim_opposite_direction_faults():
    """coil_valve_drift is one-sided UP (fouling / starvation); a leaking valve lowers the demand,
    so the leak is a cross-negative -- scored against it only if it misfires."""
    B = _bench()
    cases = B.build_drift_cases(_frames(), B.DRIFT_DETECTORS["coil_valve_drift"])
    assert not any(c.fault for c in cases)
    assert any(c.name.startswith("coi_leakage") for c in cases)
    fpu = B.FPU_DRIFT_DETECTORS["vav_reheat_valve_drift"]
    assert "PFPU_ReheatVLVLeak" in fpu["cross_negative"]
    assert "PFPU_ReheatVLVStuck_100%" in fpu["cross_negative"]


def test_build_drift_cases_baseline_and_current_disjoint():
    B = _bench()
    cases = B.build_drift_cases(_frames(), B.DRIFT_DETECTORS["coil_valve_drift"])
    tail = next(c for c in cases if c.name == "fault-free-tail")
    # baseline is the first 60%, current the tail 40% -> no index overlap (drift freezes baseline)
    assert tail.baseline.index.max() < tail.current.index.min()


def test_build_drift_cases_empty_without_fault_free():
    B = _bench()
    assert (
        B.build_drift_cases(
            {"coi_leakage_010_annual.csv": _sdahu_frame()}, B.DRIFT_DETECTORS["coil_valve_drift"]
        )
        == []
    )


def test_evaluate_confusion_totals_match_case_count():
    B = _bench()
    det = B.DRIFT_DETECTORS["economizer_damper_drift"]  # scores every case on this fixture
    cases = B.build_drift_cases(_frames(), det)
    score = evaluate(det["build"], cases)
    assert score.n + score.n_declined == len(cases)
    c = score.confusion
    assert c.tp + c.fp + c.tn + c.fn == score.n


def test_declined_cases_are_not_scored_as_negatives():
    """coil_valve_drift can't fit this fixture's baseline and declines every case. Those declines
    must not become true negatives (a specificity it never demonstrated) or misses."""
    B = _bench()
    det = B.DRIFT_DETECTORS["coil_valve_drift"]
    cases = B.build_drift_cases(_frames(), det)
    score = evaluate(det["build"], cases)
    assert score.n == 0 and score.n_declined == len(cases) == 3
    assert score.confusion.tn == 0 and score.confusion.fn == 0


def test_score_drift_emits_valid_metric_keys():
    B = _bench()
    m = B.score_drift(_frames())
    assert any(k.startswith("drift.economizer_damper_drift.") for k in m)
    # an all-declined detector measured nothing -> it emits nothing (not a 0.0 FPR)
    assert not any(k.startswith("drift.coil_valve_drift.") for k in m)
    # every emitted value is a finite rate in [0, 1] (NaN metrics are omitted, keeping JSON valid)
    for v in m.values():
        assert isinstance(v, float) and v == v and 0.0 <= v <= 1.0


def test_all_three_drift_detectors_registered():
    B = _bench()
    assert set(B.DRIFT_DETECTORS) == {
        "coil_valve_drift",
        "economizer_damper_drift",
        "duct_static_drift",
    }
    assert B.DRIFT_DETECTORS["duct_static_drift"]["positive"] is None  # specificity-only


@pytest.mark.skipif(not os.path.exists(_REAL_DATA), reason="LBNL data absent (run fetch.py)")
def test_real_data_drift_scoring_produces_metrics():
    B = _bench()
    mapping = B.MappingProvider.from_dict(B.packaged_mapping("lbnl_sdahu.json"))
    base = os.path.join(B.DATA, "sdahu")
    frames = {
        fname: B.load_role_frame(os.path.join(base, fname), mapping)
        for fname, _ in B.FAMILIES[0]["scenarios"]
        if os.path.exists(os.path.join(base, fname))
    }
    metrics = B.score_drift(frames)
    assert any(k.startswith("drift.coil_valve_drift.") for k in metrics)


# ---- FPU (VAV zone-terminal drift) plumbing ----


def _fpu_frame(n=480, *, seed=0, damper_stuck=None, reheat_leak=0.0):
    """A synthetic FPU west-zone role-frame with the points the VAV drift detectors need."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    t = np.arange(n)
    aflow_sp = np.clip(300 + 300 * (0.5 + 0.5 * np.sin(t / 12)), 200, 700)  # swept airflow setpoint
    damper = (
        np.full(n, damper_stuck)
        if damper_stuck is not None
        else np.clip(aflow_sp / 7.0 + rng.normal(0, 3, n), 0, 100)
    )
    mat = np.full(n, 55.0) + rng.normal(0, 0.4, n)  # AHU supply (box entering, cool)
    valve = np.clip(40 + 40 * np.sin(t / 15) + rng.normal(0, 3, n), 0, 100)  # reheat valve command
    sat = mat + valve * 0.15 + reheat_leak * 6 + rng.normal(0, 0.3, n)  # discharge (warm w/ reheat)
    return pd.DataFrame(
        {
            Role.DAMPER: damper,
            Role.AIRFLOW_SP: aflow_sp,
            Role.AIRFLOW: aflow_sp + rng.normal(0, 10, n),
            Role.HEAT_VALVE: valve,
            Role.MIXED_AIR_TEMP: mat,
            Role.SUPPLY_AIR_TEMP: sat,
        },
        index=idx,
    )


def _fpu_frames():
    return {
        "PFPU_FaultFree.csv": _fpu_frame(seed=1),
        "PFPU_VAVDMPRStuck_100%.csv": _fpu_frame(seed=2, damper_stuck=95.0),
        "PFPU_ReheatVLVLeak_80%MaxFlow.csv": _fpu_frame(seed=3, reheat_leak=1.0),
    }


def test_fpu_build_drift_cases_labels():
    B = _bench()
    cases = B.build_drift_cases(
        _fpu_frames(), B.FPU_DRIFT_DETECTORS["vav_airflow_drift"], fault_free="PFPU_FaultFree.csv"
    )
    # fault-free-tail (neg) + VAVDMPRStuck (pos) + ReheatVLVLeak (cross-neg)
    assert len(cases) == 3
    by_name = {c.name: c.fault for c in cases}
    assert by_name["fault-free-tail"] is False
    assert any(c.fault for c in cases if c.name.startswith("PFPU_VAVDMPRStuck"))
    assert all(not c.fault for c in cases if c.name.startswith("PFPU_ReheatVLV"))
    assert all(c.equip == "lbnl_fpu" for c in cases)


def test_fpu_score_drift_emits_valid_keys():
    B = _bench()
    m = B.score_drift(
        _fpu_frames(), B.FPU_DRIFT_DETECTORS, fault_free="PFPU_FaultFree.csv", label="FPU"
    )
    assert any(k.startswith("drift.vav_airflow_drift.") for k in m)
    counts = ("tp", "fn", "fp", "tn", "declined")
    for k, v in m.items():
        if k.rsplit(".", 1)[1] in counts:  # the confusion counts VALIDATION.md quotes
            assert isinstance(v, int) and v >= 0
        else:
            assert isinstance(v, float) and v == v and 0.0 <= v <= 1.0


def test_fpu_detectors_registered_and_multi_positive():
    B = _bench()
    assert set(B.FPU_DRIFT_DETECTORS) == {"vav_airflow_drift", "vav_reheat_valve_drift"}
    # airflow-drift targets both damper-stuck AND airflow-sensor-bias faults (tuple of prefixes)
    assert isinstance(B.FPU_DRIFT_DETECTORS["vav_airflow_drift"]["positive"], tuple)


# --- chiller-plant plant-level detectors (baseline-calibrated snapshot path) ----------------- #


def _chiller_frame(n=48, *, seed=0, kw_per_ton=0.6, approach_f=7.0):
    """A synthetic chiller-plant role-frame with a controllable kW/ton and tower approach.

    Flow (500 gpm) and loop dT (10 F) fix the load at ~208 tons, so chiller power sets kW/ton;
    wet-bulb (75 F) is fixed, so the CW supply temp sets the tower approach. Both metrics are what
    the calibrated detectors key on.
    """
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-07-01", periods=n, freq="1h")
    chws, chwr, flow = 44.0, 54.0, 500.0  # dT = 10 F -> tons = 500*10/24 ~= 208
    tons = flow * (chwr - chws) / 24.0
    power = kw_per_ton * tons
    wetbulb = 75.0
    cws = wetbulb + approach_f
    return pd.DataFrame(
        {
            Role.POWER: power + rng.normal(0, 0.5, n),
            Role.CHW_SUPPLY_TEMP: chws + rng.normal(0, 0.1, n),
            Role.CHW_RETURN_TEMP: chwr + rng.normal(0, 0.1, n),
            Role.CHW_FLOW: flow + rng.normal(0, 2, n),
            Role.CW_SUPPLY_TEMP: cws + rng.normal(0, 0.1, n),
            Role.CW_RETURN_TEMP: cws + 10 + rng.normal(0, 0.1, n),  # range ~10 F
            Role.WETBULB_TEMP: wetbulb + rng.normal(0, 0.1, n),
            Role.TOWER_FAN_SPEED: 95.0 + rng.normal(0, 1, n),  # design weather: fan near full
            Role.OAT: 88.0 + rng.normal(0, 1, n),
        },
        index=idx,
    )


def _chiller_frames():
    # fault-free (healthy), a severe cooling-tower-fouling fault (high approach AND high kW/ton),
    # and a CHW-temp sensor-bias run that leaves the physical metrics healthy (a true negative).
    return {
        "ChillerPlant.csv": _chiller_frame(seed=1),
        "ChillerPlant_coolingtower_fouling_095.csv": _chiller_frame(
            seed=2, kw_per_ton=0.98, approach_f=15.0
        ),
        "ChillerPlant_chiller_bias_2.csv": _chiller_frame(seed=3),
    }


def test_chiller_calibrates_and_fires_on_physical_fault():
    B = _bench()
    m = B.score_chiller(_chiller_frames())
    # the fouling run raises both approach and kW/ton past the calibrated ceiling -> TPR 100%
    assert m["chiller.cooling_tower_approach.tpr"] == 1.0
    assert m["chiller.chiller_efficiency.tpr"] == 1.0
    # the healthy + sensor-bias runs stay quiet against a baseline-calibrated ceiling -> FPR 0%
    assert m["chiller.cooling_tower_approach.fpr"] == 0.0
    assert m["chiller.chiller_efficiency.fpr"] == 0.0


def test_chiller_score_metric_keys_are_valid_floats():
    B = _bench()
    m = B.score_chiller(_chiller_frames())
    for k, v in m.items():
        if k.rsplit(".", 1)[1] in ("tp", "fn", "fp", "tn", "declined"):
            assert isinstance(v, int) and v >= 0
        else:
            assert isinstance(v, float) and v == v and 0.0 <= v <= 1.0
    assert m["chiller.chiller_efficiency.tp"] == 1 and m["chiller.chiller_efficiency.tn"] == 2


def test_chiller_score_empty_without_fault_free_baseline():
    B = _bench()
    frames = {"ChillerPlant_coolingtower_fouling_095.csv": _chiller_frame(kw_per_ton=0.98)}
    assert B.score_chiller(frames) == {}


def test_chiller_detectors_registered():
    B = _bench()
    assert set(B.CHILLER_DETECTORS) == {"cooling_tower_approach", "chiller_efficiency"}
    # chiller_efficiency targets the tower-fouling/PID AND the three-way-bypass faults
    pos = B.CHILLER_DETECTORS["chiller_efficiency"]["positive"]
    assert any(p.endswith("bypass_leakage") for p in pos) and any("fouling" in p for p in pos)


# --- 0.92.0 (#11, #12): explicit chiller negatives, sensor-reference pairs, series FPU boxes --- #


def test_chiller_run_on_no_list_is_excluded_not_a_negative():
    B = _bench()
    frames = _chiller_frames()
    frames["ChillerPlant_unlisted_fault.csv"] = _chiller_frame(seed=4, kw_per_ton=0.98)
    m = B.score_chiller(frames)
    # the unlisted run would have been a false positive under the old "everything else" rule
    assert m["chiller.chiller_efficiency.fp"] == 0 and m["chiller.chiller_efficiency.tn"] == 2
    det = B.CHILLER_DETECTORS["chiller_efficiency"]
    assert B._run_class("ChillerPlant.csv", det) == "negative"
    assert B._run_class("ChillerPlant_chiller_bias_-1.csv", det) == "negative"
    assert B._run_class("ChillerPlant_chiller_fouling_065.csv", det) == "positive"
    assert B._run_class("ChillerPlant_unlisted_fault.csv", det) is None
    # the tower rule: a fouled chiller / bypassed loop are cross-negatives, every bias a negative
    tower = B.CHILLER_DETECTORS["cooling_tower_approach"]
    for run in (
        "ChillerPlant_chiller_fouling_065.csv",
        "ChillerPlant_bypass_stuck_050.csv",
        "ChillerPlant_secondary_chilled_water_pressure_bias_010.csv",
    ):
        assert B._run_class(run, tower) == "negative"


def _sensor_frame(n=400, *, chl_bias=0.0, ct_bias=0.0, bypass_mix=0.0):
    """Raw chiller-plant columns for the reference pairs: chiller 1 and tower 1 run alone."""
    idx = pd.date_range("2018-01-01", periods=n, freq="1h")
    rng = np.random.default_rng(0)
    chw = 44 + rng.normal(0, 0.5, n)
    cdw = 70 + rng.normal(0, 1.0, n)
    return pd.DataFrame(
        {
            "CHL_SW_TEMP_1": chw + chl_bias,
            "CWL_PRI_SW_TEMP": chw,
            "CT_SW_TEMP_1": cdw + ct_bias,
            "CDWL_SW_TEMP": cdw - bypass_mix,
            "TWV_CTRL": np.zeros(n),
            "CHL_POW_1": np.full(n, 100.0),
            "CHL_POW_2": np.zeros(n),
            "CHL_POW_3": np.zeros(n),
            "CT_FAN_SPD_1": np.full(n, 0.6),
            "CT_FAN_SPD_2": np.zeros(n),
            "CT_FAN_SPD_3": np.zeros(n),
        },
        index=idx,
    )


def test_chiller_sensor_pairs_separate_bias_from_physical_faults():
    B = _bench()
    frames = {
        "ChillerPlant.csv": _sensor_frame(),
        "ChillerPlant_chiller_bias_2.csv": _sensor_frame(chl_bias=3.6),
        "ChillerPlant_chiller_bias_1.csv": _sensor_frame(chl_bias=1.8),  # under the 2.0 F default
        "ChillerPlant_coolingtower_bias_-2.csv": _sensor_frame(ct_bias=-3.6),
        "ChillerPlant_chiller_fouling_065.csv": _sensor_frame(),
        # a leaking bypass mixes condenser water past the tower while its command reads shut
        "ChillerPlant_bypass_leakage_050.csv": _sensor_frame(bypass_mix=-40.0),
    }
    m = B.score_chiller_sensors(frames)
    assert (
        m["chiller.sensor.chiller_leaving_water.tp"],
        m["chiller.sensor.chiller_leaving_water.fn"],
    ) == (1, 1)
    assert m["chiller.sensor.chiller_leaving_water.fp"] == 0
    assert m["chiller.sensor.tower_leaving_water.tp"] == 1
    assert m["chiller.sensor.tower_leaving_water.fp"] == 1  # the bypass run: honestly a false alarm
    # a pair whose physics never holds (chiller 2 always on) declines instead of scoring
    busy = _sensor_frame()
    busy["CHL_POW_2"] = 50.0
    m2 = B.score_chiller_sensors({"ChillerPlant.csv": busy})
    assert m2["chiller.sensor.chiller_leaving_water.declined"] == 1
    assert "chiller.sensor.chiller_leaving_water.fpr" not in m2  # nothing scored -> no rate


def test_fpu_target_lists_follow_the_documented_direction():
    B = _bench()
    air = B.fpu_drift_detectors("SFPU")["vav_airflow_drift"]
    rh = B.fpu_drift_detectors("SFPU")["vav_reheat_valve_drift"]
    names = [
        "SFPU_VAVDMPRStuck_0%.csv",
        "SFPU_VAVDMPRStuck_20%.csv",
        "SFPU_VAVDMPRStuck_50%.csv",
        "SFPU_SensorBias_VAVAirflow_-200CFM.csv",
        "SFPU_SensorBias_VAVAirflow_+200CFM.csv",
        "SFPU_ReheatVLVStuck_20%.csv",
        "SFPU_ReheatVLVStuck_80%.csv",
        "SFPU_ReheatCoilFouling_Waterside_Minor.csv",
        "SFPU_RMTEMPUnstable.csv",
    ]

    def kind(det, n):
        if any(n.startswith(p) for p in det["positive"]):
            return "pos"
        return "neg" if any(n.startswith(p) for p in det["cross_negative"]) else None

    assert [kind(air, n) for n in names] == [
        None,  # stuck at 0 / 20 %: a DOWN drift, outside the one-sided claim -> excluded
        None,
        "pos",
        "pos",
        "neg",
        "neg",
        "neg",
        "neg",
        "neg",
    ]
    assert [kind(rh, n) for n in names] == [
        "neg",
        "neg",
        "neg",
        "neg",
        "neg",
        "pos",
        "neg",  # stuck at 80 %: over-delivers, the demand falls
        "pos",
        None,  # instability: the rule's physics predicts nothing -> excluded
    ]
    assert (
        air["equip"] == "lbnl_sfpu"
        and B.FPU_DRIFT_DETECTORS["vav_airflow_drift"]["equip"] == "lbnl_fpu"
    )


def test_series_fpu_scores_under_its_own_keys_and_frames():
    B = _bench()
    frames = {k.replace("PFPU", "SFPU"): v for k, v in _fpu_frames().items()}
    dets = B.fpu_drift_detectors("SFPU")
    dets["vav_reheat_valve_drift"]["frames"] = frames  # a per-detector frame set is honoured
    m = B.score_drift(
        frames, dets, fault_free="SFPU_FaultFree.csv", counts=True, key_prefix="drift.sfpu."
    )
    assert m and all(k.startswith("drift.sfpu.") for k in m)
    assert m["drift.sfpu.vav_airflow_drift.tp"] == 1
    mp = B.sfpu_reheat_mapping()
    assert mp.role_of("VAV_DA_CFM_S") == Role.AIRFLOW and mp.role_of("VAV_PM_CFM_S") is None
