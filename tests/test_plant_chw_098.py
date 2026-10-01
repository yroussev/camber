"""0.98 (#86 items 2 and 3): the chilled-water reset's sign, constant-flow plants, and the pump's
learned VFD floor; plus the synthetic healthy reset now resetting the right way (S2)."""

import numpy as np
import pandas as pd
import pytest

from camber import faultlab
from camber.aso import recommend
from camber.chwpump import analyze_chw_pump, learn_vfd_floor
from camber.model.roles import Role
from camber.rules.base import Finding
from camber.rules.builtin import make_rule
from camber.rules.chwplant_rule import CHWPlantReset
from camber.rules.chwpump_rule import CHWPumpDPReset
from camber.rules.hwpump_rule import HWPumpDPReset


def _idx(n=24 * 21):
    return pd.date_range("2025-06-02", periods=n, freq="1h")


def _plant(slope=-0.3, dt=12.0, flow=None, n=24 * 21):
    """A plant running every hour: CHWST = 46 + slope x (OAT - 70), a constant loop delta-T."""
    idx = _idx(n)
    oat = pd.Series(70 + 15 * np.sin(np.arange(n) / 9.0), index=idx)
    chws = 46 + slope * (oat - 70)
    frame = {
        Role.CHW_SUPPLY_TEMP: chws,
        Role.CHW_RETURN_TEMP: chws + dt,
        Role.OAT: oat,
        Role.COMPRESSOR_STATUS: pd.Series(1.0, index=idx),
    }
    if flow is not None:
        frame[Role.CHW_FLOW] = pd.Series(flow, index=idx)
    return pd.DataFrame(frame, index=idx)


# --------------------------------------------------------------------------- S2: faultlab


def test_faultlab_healthy_reset_goes_down_as_oat_rises():
    idx = _idx(24 * 28)
    clean = faultlab._chw_reset(idx, faulty=False)
    f = CHWPlantReset().analyze("P", clean)
    assert f.severity == "ok"
    assert f.metrics["chwst_slope_per_F"] < -0.1
    assert f.metrics["chwst_reset_direction"] == "expected"
    faulty = CHWPlantReset().analyze("P", faultlab._chw_reset(idx, faulty=True))
    assert faulty.severity == "fault" and faulty.metrics["chwst_reset_direction"] == "flat"


# --------------------------------------------------------------------------- reset sign


def test_a_reset_the_wrong_way_is_not_a_reset():
    f = CHWPlantReset().analyze("P", _plant(slope=+0.6))
    assert f.metrics["chwst_reset_present"] is False
    assert f.metrics["chwst_reset_direction"] == "reverse"
    assert f.severity == "warn"
    assert any("wrong way" in c for c in f.caveats)
    assert "reverse of a reset" in f.summary


def test_expected_reset_sign_positive_and_any():
    up = _plant(slope=+0.6)
    assert CHWPlantReset(expected_reset_sign="any").analyze("P", up).severity == "ok"
    pos = CHWPlantReset(expected_reset_sign="positive")
    assert pos.analyze("P", up).metrics["chwst_reset_direction"] == "expected"
    assert pos.analyze("P", _plant(slope=-0.6)).metrics["chwst_reset_direction"] == "reverse"


def test_reset_not_evaluated_without_oat_has_no_direction():
    frame = _plant().drop(columns=[Role.OAT])
    f = CHWPlantReset().analyze("P", frame)
    assert f.metrics["chwst_reset_present"] is None
    assert f.metrics["chwst_reset_direction"] is None


# --------------------------------------------------------------------------- flow mode


def test_constant_flow_reports_but_does_not_judge_low_deltat():
    f = CHWPlantReset().analyze("P", _plant(dt=5.0, flow=250.0))
    assert f.metrics["flow_mode"] == "constant" and f.metrics["flow_cv"] == 0.0
    assert f.metrics["low_deltaT_pct"] == 100.0  # reported ...
    assert f.severity == "ok"  # ... not judged
    assert any("constant primary flow" in c for c in f.caveats)


def test_variable_flow_and_no_flow_judge_low_deltat():
    n = 24 * 21
    varying = 150 + 100 * np.abs(np.sin(np.arange(n) / 7.0))
    f = CHWPlantReset().analyze("P", _plant(dt=5.0, flow=varying))
    assert f.metrics["flow_mode"] == "variable" and f.severity == "fault"
    g = CHWPlantReset().analyze("P", _plant(dt=5.0))
    assert g.metrics["flow_mode"] == "unknown" and g.metrics["flow_cv"] is None
    assert g.severity == "fault"


def test_declared_flow_mode_and_too_few_flow_samples():
    f = CHWPlantReset(flow_mode="constant").analyze("P", _plant(dt=5.0))
    assert f.severity == "ok" and f.metrics["flow_mode"] == "constant"
    assert any("declared constant flow" in c for c in f.caveats)
    v = CHWPlantReset(flow_mode="variable").analyze("P", _plant(dt=5.0, flow=250.0))
    assert v.severity == "fault"
    # the occupied-hours filter leaves < 24 hours of a 3-day frame: undecided
    short = CHWPlantReset().analyze("P", _plant(dt=5.0, flow=250.0, n=36))
    assert short.metrics["flow_mode"] == "unknown"


def test_design_deltat_min_is_a_parameter():
    frame = _plant(dt=7.0)
    assert CHWPlantReset().analyze("P", frame).severity == "fault"
    f = CHWPlantReset(design_deltaT_min_f=6.0).analyze("P", frame)
    assert f.severity == "ok" and f.metrics["design_deltaT_min_f"] == 6.0


def test_invalid_choices_are_refused():
    with pytest.raises(ValueError, match="expected_reset_sign"):
        CHWPlantReset(expected_reset_sign="down")
    with pytest.raises(ValueError, match="flow_mode"):
        CHWPlantReset(flow_mode="primary")
    with pytest.raises(ValueError, match="near_min_pct"):
        CHWPumpDPReset(near_min_pct="floor")
    with pytest.raises(ValueError, match="near_min_pct"):
        analyze_chw_pump(pd.DataFrame({"PumpSpeed": [50.0] * 20}), "P", near_min_pct="x")


def test_config_params_reach_the_rules():
    r = make_rule("chw_plant_reset", flow_mode="variable", design_deltaT_min_f=10.0)
    assert r.flow_mode == "variable" and r.design_deltaT_min_f == 10.0
    assert make_rule("chw_pump_dp_reset", near_min_pct=30.0).near_min_pct == 30.0
    assert make_rule("hw_pump_dp_reset", near_min_pct="auto").near_min_pct == "auto"


# --------------------------------------------------------------------------- pump floor


def _speeds(floor=34.5, share_at_floor=0.4, n=2000, seed=0):
    rng = np.random.default_rng(seed)
    k = int(n * share_at_floor)
    above = rng.uniform(floor + 5, 100.0, n - k)
    return pd.Series(np.concatenate([np.full(k, floor), above]))


def test_learn_vfd_floor_finds_a_plateau():
    assert learn_vfd_floor(_speeds()) == 34.5
    assert learn_vfd_floor(_speeds(share_at_floor=0.05)) is None  # a tail, not a plateau
    assert learn_vfd_floor(pd.Series([60.0] * 100)) is None  # a fixed speed, not a floor
    assert learn_vfd_floor(pd.Series([], dtype=float)) is None


def test_chw_pump_counts_hours_at_the_learned_floor():
    idx = pd.date_range("2025-01-01", periods=2000, freq="1h")
    frame = pd.DataFrame({Role.CHW_PUMP_SPEED: _speeds(share_at_floor=0.6).to_numpy()}, index=idx)
    f = CHWPumpDPReset().analyze("P", frame)
    m = f.metrics
    assert m["near_min_source"] == "learned" and m["vfd_floor_pct"] == 34.5
    assert m["near_min_band_pct"] == 35.5
    assert m["pct_running_near_min"] >= 60.0 and f.severity == "warn"
    assert "learned VFD floor 34.5%" in f.summary
    fixed = CHWPumpDPReset(near_min_pct=25.0).analyze("P", frame)
    assert fixed.metrics["near_min_source"] == "fixed"
    assert fixed.metrics["pct_running_near_min"] == 0.0 and fixed.severity == "ok"


def test_auto_falls_back_to_25_without_a_floor():
    idx = pd.date_range("2025-01-01", periods=500, freq="1h")
    speeds = np.linspace(20.0, 100.0, 500)
    frame = pd.DataFrame({Role.CHW_PUMP_SPEED: speeds}, index=idx)
    f = CHWPumpDPReset().analyze("P", frame)
    assert f.metrics["near_min_source"] == "default" and f.metrics["near_min_band_pct"] == 25.0
    assert f.metrics["vfd_floor_pct"] is None
    assert "no VFD floor plateau found" in f.summary


def test_hw_pump_keeps_the_fixed_band_by_default():
    idx = pd.date_range("2025-01-01", periods=2000, freq="1h")
    frame = pd.DataFrame({Role.HW_PUMP_SPEED: _speeds(share_at_floor=0.6).to_numpy()}, index=idx)
    f = HWPumpDPReset().analyze("B", frame)
    assert f.metrics["near_min_source"] == "fixed" and f.metrics["near_min_band_pct"] == 25.0
    assert f.severity == "ok"
    auto = HWPumpDPReset(near_min_pct="auto").analyze("B", frame)
    assert auto.metrics["near_min_source"] == "learned" and auto.severity == "warn"


# --------------------------------------------------------------------------- advice


def _f(**m):
    return Finding(rule="chw_plant_reset", equip="P", severity="warn", metrics=m, summary="")


def test_no_low_deltat_advice_on_a_constant_flow_plant():
    rec = recommend(_f(low_deltaT_pct=80.0, chwst_reset_present=False, flow_mode="constant"))
    assert rec.title == "Reset the chilled-water supply temperature"


def test_a_reversed_reset_gets_its_own_advice():
    rec = recommend(
        _f(
            low_deltaT_pct=100.0,
            chwst_reset_present=False,
            chwst_reset_direction="reverse",
            chwst_slope_per_F=0.76,
            flow_mode="constant",
        )
    )
    assert rec.title == "Find why the chilled-water supply warms in hot weather"
    assert "+0.76" in rec.action
    # on a variable-flow plant a low deltaT still comes first
    low = recommend(
        _f(
            low_deltaT_pct=100.0,
            chwst_reset_present=False,
            chwst_reset_direction="reverse",
            flow_mode="variable",
        )
    )
    assert low.title == "Fix low chilled-water loop ΔT"
