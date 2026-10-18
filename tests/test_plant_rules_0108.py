"""0.108 regressions for the plant rules: #234 (tower drift load source), #235 (chiller staging run
gate), #236 (control_hunting per-signal resolution), #237 (short-cycle observed days) and #240
(defrost / compressor staging short trends). Each test is the issue's repro."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from camber.chillerbaseline import LoadBaseline
from camber.chillerstaging import analyze_chiller_staging_fleet, chiller_power_running
from camber.model.roles import Role
from camber.rules.boilercycle_rule import BoilerShortCycle
from camber.rules.chillerfleet_rule import ChillerStagingFleet
from camber.rules.chillerstaging_rule import ChillerStaging
from camber.rules.compressor_cycle_rule import CompressorShortCycle
from camber.rules.compressor_stage_rule import CompressorStaging
from camber.rules.coolingtower_drift_rule import CoolingTowerApproachDrift
from camber.rules.heatpump_rule import HeatPumpDefrost
from camber.rules.hunting_rule import ControlHunting
from camber.rules.tower_fan_effort_rule import CoolingTowerFanEffortDrift
from camber.store.modelstore import BaselineStore

# --------------------------------------------------------------------------------------- #234

_CHW = [Role.CHW_FLOW, Role.CHW_SUPPLY_TEMP, Role.CHW_RETURN_TEMP]


def _tower_period(rng, start, n=24 * 30):
    idx = pd.date_range(start, periods=n, freq="1h")
    tons = rng.uniform(100, 400, n)
    chwr = 54.0
    wb = rng.uniform(60, 75, n)
    cws = wb + 5 + 0.01 * tons + rng.normal(0, 0.4, n)  # a healthy, unchanged tower
    return pd.DataFrame(
        {
            Role.CHW_FLOW: 900.0,
            Role.CHW_SUPPLY_TEMP: chwr - tons * 24 / 900.0,
            Role.CHW_RETURN_TEMP: chwr,
            Role.CW_SUPPLY_TEMP: cws,
            Role.CW_RETURN_TEMP: cws + tons * 1.25 * 24 / 1200.0,
            Role.WETBULB_TEMP: wb,
        },
        index=idx,
    )


def test_tower_approach_declines_a_baseline_fitted_on_another_load():
    rng = np.random.default_rng(1)
    b, c = _tower_period(rng, "2026-06-01"), _tower_period(rng, "2026-07-01")
    store = BaselineStore(None)
    rule = CoolingTowerApproachDrift(store)
    first = rule.analyze_periods("CT1", b, c)
    assert first.severity == "ok" and first.metrics["tower_approach_load_source"] == "chw_tons"
    assert store.model_for("", "CT1", "cooling_tower_approach").load_source == "chw_tons"
    # later, the tower runs as its own equipment (no CHW points): the load is the range
    f = rule.analyze_periods("CT1", b.drop(columns=_CHW), c.drop(columns=_CHW))
    assert f.severity == "info" and f.metrics["reason"] == "load_source_mismatch"
    assert "baseline fitted on chw_tons, current load is cw_range; refreeze" in f.summary
    assert any("refreeze" in x for x in f.caveats)
    # the cw_flow variant: frozen on the range, then cw_flow is mapped
    store2 = BaselineStore(None)
    b2, c2 = b.drop(columns=_CHW), c.drop(columns=_CHW)
    CoolingTowerApproachDrift(store2).analyze_periods("CT2", b2, c2)
    b3, c3 = b2.assign(**{Role.CW_FLOW: 1200.0}), c2.assign(**{Role.CW_FLOW: 1200.0})
    f = CoolingTowerApproachDrift(store2).analyze_periods("CT2", b3, c3)
    assert f.severity == "info" and "narrowed" not in f.summary
    assert f.metrics["tower_approach_baseline_load_source"] == "cw_range"


def test_tower_approach_legacy_record_is_read_with_a_caveat():
    rng = np.random.default_rng(1)
    b, c = _tower_period(rng, "2026-06-01"), _tower_period(rng, "2026-07-01")
    store = BaselineStore(None)
    CoolingTowerApproachDrift(store).analyze_periods("CT1", b, c)
    rec = store.get("", "CT1", "cooling_tower_approach")
    rec.coefficients.pop("load_source")  # as frozen before 0.108
    f = CoolingTowerApproachDrift(store).analyze_periods("CT1", b, c)
    assert f.severity == "ok"
    assert any("before 0.108" in x for x in f.caveats)


def test_load_baseline_dict_leaves_an_empty_load_source_out():
    lb = LoadBaseline(
        n=1, slope_f_per_ton=0, intercept_f=0, sigma_f=1, r2=0, tons_min=0, tons_max=1
    )
    assert "load_source" not in lb.as_dict()
    lb.load_source = "cw_range"
    assert LoadBaseline.from_dict(lb.as_dict()).load_source == "cw_range"


def test_tower_fan_effort_declines_a_baseline_fitted_on_another_load():
    rng = np.random.default_rng(2)

    def period(start, n=24 * 30):
        idx = pd.date_range(start, periods=n, freq="1h")
        rngf, wb = rng.uniform(4, 10, n), rng.uniform(60, 75, n)
        fan = np.clip(20 + 7 * rngf + 0.5 * (wb - 60) + rng.normal(0, 2, n), 0, 100)
        cws = wb + 7
        return pd.DataFrame(
            {
                Role.TOWER_FAN_SPEED: fan,
                Role.CW_SUPPLY_TEMP: cws,
                Role.CW_RETURN_TEMP: cws + rngf,
                Role.WETBULB_TEMP: wb,
            },
            index=idx,
        )

    b, c = period("2026-06-01"), period("2026-07-01")
    store = BaselineStore(None)
    first = CoolingTowerFanEffortDrift(store).analyze_periods("CT1", b, c)
    assert first.severity == "ok" and first.metrics["load_source"] == "cw_range"
    b[Role.CW_FLOW] = 1200.0
    c[Role.CW_FLOW] = 1200.0
    f = CoolingTowerFanEffortDrift(store).analyze_periods("CT1", b, c)
    assert f.severity == "info" and f.metrics["reason"] == "load_source_mismatch"
    assert "current load is cw_heat_rejection; refreeze" in f.summary
    assert f.metrics["baseline_load_source"] == "cw_range"


# --------------------------------------------------------------------------------------- #235


def _cycling_chiller(standby_kw=8.0):
    idx = pd.date_range("2026-07-06", periods=7 * 96, freq="15min")
    on = (np.arange(len(idx)) // 4) % 2 == 0  # 1 h on, 1 h off: 12 starts a day
    return pd.DataFrame({Role.POWER: np.where(on, 300.0, standby_kw)}, index=idx), on


def test_chiller_staging_standby_draw_is_not_running():
    frame, _on = _cycling_chiller()
    f = ChillerStaging().analyze("CH1", frame)
    assert f.severity == "fault"
    assert f.metrics["starts_per_day"] == pytest.approx(12.0, abs=0.2)
    assert f.metrics["runtime_pct"] == pytest.approx(50.0, abs=1.0)
    assert f.metrics["run_source"] == "power_p95"
    assert f.metrics["run_threshold_kw"] == pytest.approx(30.0)
    # the pre-0.108 fixed 2 kW floor, given explicitly, reads standby as running
    old = ChillerStaging(min_power_kw=2.0).analyze("CH1", frame)
    assert old.severity == "ok" and old.metrics["run_source"] == "power_kw"


def test_chiller_staging_uses_compressor_status_when_mapped():
    frame, on = _cycling_chiller(standby_kw=40.0)  # standby above a tenth of p95
    assert ChillerStaging().analyze("CH1", frame).metrics["starts_per_day"] < 1
    frame[Role.COMPRESSOR_STATUS] = on.astype(float)
    f = ChillerStaging().analyze("CH1", frame)
    assert f.metrics["run_source"] == "status"
    assert f.metrics["starts_per_day"] == pytest.approx(12.0, abs=0.2)


def test_a_lag_chiller_at_zero_kw_most_of_the_time_still_runs():
    idx = pd.date_range("2026-07-06", periods=7 * 96, freq="15min")
    p = pd.Series(0.0, index=idx)
    p.iloc[100:120] = 250.0  # 3 % of the week
    run, src, thr = chiller_power_running(p)
    assert src == "power_p95" and thr == pytest.approx(25.0) and int(run.sum()) == 20


def test_chiller_staging_fleet_standby_draw_is_not_running():
    a, _ = _cycling_chiller()
    b = a.copy()
    # the second chiller runs 4 h each afternoon, off its lead's cycle; standby draw otherwise
    b[Role.POWER] = 8.0
    b.loc[(b.index.hour >= 13) & (b.index.hour < 17), Role.POWER] = 300.0
    frames = {"CH1": a, "CH2": b}
    f = ChillerStagingFleet().analyze_fleet(frames)
    assert f.metrics["run_source"] == "power_p95"
    assert f.metrics["n_multi_samples"] == 7 * 8  # both run only in CH1's on-hours of 13-17h
    old = analyze_chiller_staging_fleet(
        {e: x.rename(columns={Role.POWER: "Power"}) for e, x in frames.items()}, min_power_kw=2.0
    )
    assert old.n_multi_samples > 300 and old.run_source == "power_kw"  # standby read as running


# --------------------------------------------------------------------------------------- #236


def test_a_coarse_damper_does_not_hide_a_hunting_valve():
    idx = pd.date_range("2026-07-06", periods=7 * 1440, freq="1min")
    hunt = 50 + 20 * np.sign(np.sin(np.arange(len(idx)) * 2 * np.pi / 6))  # 20 reversals/hr
    df = pd.DataFrame({Role.COOL_VALVE: hunt, Role.SUPPLY_FAN_STATUS: 1.0}, index=idx)
    assert ControlHunting().analyze("AHU", df).severity == "fault"
    oa = pd.Series(np.nan, index=idx)
    oa.iloc[::15] = 30.0  # a 15-minute damper: at most 4 reversals/hr
    df[Role.OA_DAMPER] = oa
    f = ControlHunting().analyze("AHU", df)
    assert f.severity == "fault" and f.metrics["worst_signal"] == "cool_valve"
    assert f.metrics["max_resolvable_per_hr"] == pytest.approx(60.0)
    assert f.metrics["not_evaluated_signals"] == ["oa_damper"]
    assert f.metrics["per_signal_rate"]["oa_damper"] is None
    assert any(c.startswith("oa_damper not evaluated") for c in f.caveats)
    assert not any("500%" in c for c in f.caveats)


def test_all_coarse_signals_still_decline_against_their_own_ceiling():
    idx = pd.date_range("2026-07-06", periods=800, freq="15min")
    vals = np.tile([20.0, 54.0, 20.0, 51.0], 200)
    f = ControlHunting().analyze("AHU", pd.DataFrame({Role.OA_DAMPER: vals}, index=idx))
    assert f.severity == "info" and f.metrics["reversal_share_of_max"] <= 1.0


# --------------------------------------------------------------------------------------- #237


def _gappy_status():
    a = pd.date_range("2026-01-01", periods=2 * 288, freq="5min")
    va = np.tile([1] * 3 + [0] * 9, len(a) // 12)  # 24 starts a day
    b = pd.date_range("2026-01-30", periods=288, freq="5min")  # a 28-day gap, then a day off
    return pd.Series(np.r_[va, np.zeros(288)], index=a.append(b))


def test_short_cycle_divides_by_observed_days():
    s = _gappy_status()
    f = BoilerShortCycle().analyze("B1", pd.DataFrame({Role.BOILER_STATUS: s}))
    assert f.severity == "fault"
    assert f.metrics["starts_per_day"] == pytest.approx(16.0, abs=0.1)
    assert f.metrics["n_days"] == pytest.approx(30.0, abs=0.1)
    assert f.metrics["n_days_observed"] == pytest.approx(3.0, abs=0.05)
    assert any("observed on only 3.0 of the 30.0 days" in c for c in f.caveats)
    full = pd.date_range("2026-01-01", "2026-01-31", freq="5min", inclusive="left")
    f = CompressorShortCycle().analyze(
        "C1", pd.DataFrame({Role.COMPRESSOR_STATUS: s.reindex(full)})
    )
    assert f.severity == "warn" and f.metrics["n_days_observed"] == pytest.approx(3.0, abs=0.05)
    assert any("compressor status was observed" in c for c in f.caveats)


def test_a_gapless_trend_has_no_coverage_caveat():
    idx = pd.date_range("2026-01-01", periods=3 * 288, freq="5min")
    s = pd.Series(np.tile([1] * 3 + [0] * 9, len(idx) // 12), index=idx)
    f = BoilerShortCycle().analyze("B1", pd.DataFrame({Role.BOILER_STATUS: s}))
    assert f.metrics["n_days_observed"] == pytest.approx(f.metrics["n_days"], abs=0.05)
    assert not any("observed on only" in c for c in f.caveats)


# --------------------------------------------------------------------------------------- #240


def test_defrost_short_trend_is_not_extrapolated_to_a_day():
    idx = pd.date_range("2026-01-05 06:00", periods=120, freq="1min")
    v = np.zeros(120)
    v[60:65] = 1  # one defrost: two reversals
    f = HeatPumpDefrost().analyze("HP1", pd.DataFrame({Role.REVERSING_VALVE_CMD: v}, index=idx))
    assert f.severity == "ok" and f.metrics["reversals_per_day"] == pytest.approx(2.0)
    assert f.metrics["n_days"] == pytest.approx(0.08, abs=0.01)
    assert any("spans only 2.0 h" in c for c in f.caveats)


def test_defrost_ok_summary_names_no_cause():
    idx = pd.date_range("2026-07-05", periods=7 * 288, freq="5min")
    frame = pd.DataFrame({Role.REVERSING_VALVE_CMD: np.ones(len(idx))}, index=idx)
    f = HeatPumpDefrost().analyze("HP1", frame)
    assert f.severity == "ok" and "defrost termination" not in f.summary
    assert not f.caveats
    v = np.tile([0.0] * 3 + [1.0] * 3, len(idx) // 6)  # reversing every 15 min
    f = HeatPumpDefrost().analyze("HP1", pd.DataFrame({Role.REVERSING_VALVE_CMD: v}, index=idx))
    assert f.severity == "fault" and "check defrost termination" in f.summary


def test_compressor_staging_short_trend_is_not_extrapolated_to_a_day():
    idx = pd.date_range("2026-07-05 12:00", periods=120, freq="1min")
    st = np.ones(120)
    st[30:60] = 2
    f = CompressorStaging().analyze("RTU", pd.DataFrame({Role.COMPRESSOR_STAGE: st}, index=idx))
    assert f.severity == "ok" and f.metrics["stage_changes_per_day"] == pytest.approx(2.0)
    assert any("spans only 2.0 h" in c for c in f.caveats)
