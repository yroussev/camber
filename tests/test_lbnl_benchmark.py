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
        "coi_leakage_050_annual.csv": _sdahu_frame(seed=2, coil_leak=1.0),
        "damper_stuck_100_annual_short.csv": _sdahu_frame(seed=3, damper_stuck=95.0),
    }


def test_build_drift_cases_labels_positive_and_cross_negative():
    B = _bench()
    cases = B.build_drift_cases(_frames(), B.DRIFT_DETECTORS["coil_valve_drift"])
    # fault-free-tail (negative) + coi_leakage (positive) + damper_stuck (cross-negative)
    assert len(cases) == 3
    by_name = {c.name: c.fault for c in cases}
    assert by_name["fault-free-tail"] is False
    assert any(c.fault for c in cases if c.name.startswith("coi_leakage"))
    assert all(not c.fault for c in cases if c.name.startswith("damper_stuck"))


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
            {"coi_leakage_050_annual.csv": _sdahu_frame()}, B.DRIFT_DETECTORS["coil_valve_drift"]
        )
        == []
    )


def test_evaluate_confusion_totals_match_case_count():
    B = _bench()
    det = B.DRIFT_DETECTORS["coil_valve_drift"]
    cases = B.build_drift_cases(_frames(), det)
    score = evaluate(det["build"], cases)
    assert score.n == len(cases)
    c = score.confusion
    assert c.tp + c.fp + c.tn + c.fn == len(cases)


def test_score_drift_emits_valid_metric_keys():
    B = _bench()
    m = B.score_drift(_frames())
    assert any(k.startswith("drift.coil_valve_drift.") for k in m)
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
    mapping = B.MappingProvider.from_dict(
        __import__("json").load(open(os.path.join(B.HERE, "mapping.json")))
    )
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
    for v in m.values():
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
            Role.TOWER_FAN_SPEED: 80.0 + rng.normal(0, 1, n),
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
    for v in B.score_chiller(_chiller_frames()).values():
        assert isinstance(v, float) and v == v and 0.0 <= v <= 1.0


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
