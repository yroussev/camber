"""0.106 (#181, #182): filter_fouling and control_hunting judge fan-on samples; control_hunting's
resolution ceiling counts a threshold as reachable only with a margin; filter_fouling's fault
multiplier is a parameter."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.filter_rule import FilterFouling  # noqa: E402
from camber.rules.hunting_rule import ControlHunting, reversals_per_hour  # noqa: E402
from camber.schedules import FAN_GATE_NONE  # noqa: E402


def _idx(n, freq="5min"):
    return pd.date_range("2025-06-02", periods=n, freq=freq)


def _fan_daytime(idx):
    """Fan on 05:00-21:00 (two-thirds of the day)."""
    return pd.Series(((idx.hour >= 5) & (idx.hour < 21)).astype(float), index=idx)


# ---------------------------------------------------------------- filter_fouling


def _filter_frame(n_days=7, run_dp=1.2, with_fan=True):
    idx = _idx(288 * n_days)
    # a unit that runs 8 h a day: ungated, its fan-off zeros are two-thirds of the samples
    on = (idx.hour >= 8) & (idx.hour < 16)
    fan = pd.Series(on.astype(float), index=idx)
    dp = pd.Series(np.where(on, run_dp, 0.02), index=idx)
    cols = {Role.FILTER_DIFF_PRESS: dp}
    if with_fan:
        cols[Role.SUPPLY_FAN_STATUS] = fan
    return pd.DataFrame(cols, index=idx)


def test_filter_judges_fan_on_samples():
    f = FilterFouling().analyze("AHU1", _filter_frame())
    assert f.severity == "warn", f
    assert f.metrics["filter_dp_median_inwc"] == 1.2
    assert f.metrics["fan_gate"] == "fan status"
    assert f.metrics["pct_over_threshold"] == 100.0
    assert f.metrics["n_masked_fan_off"] == 288 * 7 * 2 // 3
    assert "of fan-on samples above change-out" in f.summary
    assert "hours" not in f.summary
    assert not f.caveats


def test_filter_ungated_without_fan_signal_says_so():
    f = FilterFouling().analyze("AHU1", _filter_frame(with_fan=False))
    # one-third runtime: the fan-off zeros hold the median down
    assert f.severity == "ok", f
    assert f.metrics["fan_gate"] == FAN_GATE_NONE
    assert f.metrics["_missing_optional"] == [Role.SUPPLY_FAN_STATUS.value]
    assert any("not fan-gated" in c for c in f.caveats)
    assert "of samples above change-out" in f.summary


def test_filter_fan_gate_off_reads_every_sample():
    f = FilterFouling(fan_gate=False).analyze("AHU1", _filter_frame())
    assert f.severity == "ok" and f.metrics["fan_gate"] == "off" and not f.caveats


def test_filter_fault_factor_is_a_parameter():
    frame = _filter_frame(run_dp=1.7)
    assert FilterFouling().analyze("AHU1", frame).severity == "fault"
    f = FilterFouling(fault_factor=2.0).analyze("AHU1", frame)
    assert f.severity == "warn" and f.metrics["fault_dp_inwc"] == 2.0
    with pytest.raises(ValueError):
        FilterFouling(fault_factor=0.9)


def test_filter_fan_never_on_declines():
    frame = _filter_frame()
    frame[Role.SUPPLY_FAN_STATUS] = 0.0
    f = FilterFouling().analyze("AHU1", frame)
    assert f.severity == "info" and "never ran" in f.summary


# ---------------------------------------------------------------- control_hunting


def _hunting_frame(freq="2min", hours=48, with_fan=True):
    idx = _idx(int(hours * 60 / int(freq.rstrip("min"))), freq)
    fan = _fan_daytime(idx).astype(bool)
    # alternates every sample while running; shut (0) with the fan off
    alt = np.where(np.arange(len(idx)) % 2 == 0, 30.0, 70.0)
    valve = pd.Series(np.where(fan, alt, 0.0), index=idx)
    cols = {Role.COOL_VALVE: valve}
    if with_fan:
        cols[Role.SUPPLY_FAN_STATUS] = fan.astype(float)
    return pd.DataFrame(cols, index=idx)


def test_reversals_gate_excludes_fan_off_time():
    frame = _hunting_frame()
    on = frame[Role.SUPPLY_FAN_STATUS] > 0.5
    rate_all, _ = reversals_per_hour(frame[Role.COOL_VALVE], 5.0)
    rate_on, _ = reversals_per_hour(frame[Role.COOL_VALVE], 5.0, gate=on)
    assert rate_on > 25 > 21 > rate_all  # ~30/hr while running, diluted by a third off


def test_hunting_judges_fan_on_hours():
    f = ControlHunting().analyze("AHU1", _hunting_frame())
    assert f.metrics["fan_gate"] == "fan status"
    assert f.metrics["reversals_per_hr"] > 25
    assert f.metrics["fan_on_hours"] == pytest.approx(32, abs=0.2)
    assert f.severity == "fault" and not f.caveats


def test_hunting_ungated_caveat():
    f = ControlHunting().analyze("AHU1", _hunting_frame(with_fan=False))
    assert f.metrics["fan_gate"] == FAN_GATE_NONE
    assert any("not fan-gated" in c for c in f.caveats)
    assert Role.SUPPLY_FAN_STATUS.value in f.metrics["_missing_optional"]


def test_hunting_fan_never_on_declines():
    frame = _hunting_frame()
    frame[Role.SUPPLY_FAN_STATUS] = 0.0
    f = ControlHunting().analyze("AHU1", frame)
    assert f.severity == "info" and f.metrics["reversals_per_hr"] is None
    assert "never ran" in f.summary


def _alt(freq, hours=48):
    idx = _idx(int(hours * 60 / int(freq.rstrip("min"))), freq)
    sig = np.where(np.arange(len(idx)) % 2 == 0, 30.0, 70.0)
    return pd.DataFrame({Role.COOL_VALVE: pd.Series(sig, index=idx)})


def test_ceiling_at_warn_declines():
    """10-min data: the ceiling (6/hr) is the warn threshold itself -- not a pass."""
    f = ControlHunting().analyze("AHU1", _alt("10min"))
    assert f.severity == "info" and f.metrics["reversals_per_hr"] is None
    assert f.metrics["max_resolvable_per_hr"] == 6.0
    assert any("hunting not evaluated" in c and "too close to" in c for c in f.caveats)


def test_ceiling_at_fault_caps_at_warn_with_fitting_caveat():
    """5-min data: the ceiling (12/hr) is the fault threshold -- a perfect alternation warns."""
    f = ControlHunting().analyze("AHU1", _alt("5min"))
    assert f.severity == "warn" and f.metrics["fault_reachable"] is False
    assert any("severity capped at warn" in c for c in f.caveats)
    # a stable loop on the same sampling: ok, and the caveat says what was not testable
    idx = _idx(576, "5min")
    calm = pd.DataFrame({Role.COOL_VALVE: pd.Series(np.linspace(20, 60, 576), index=idx)})
    ok = ControlHunting().analyze("AHU1", calm)
    assert ok.severity == "ok"
    assert not any("capped" in c for c in ok.caveats)
    assert any("only warn-level hunting" in c for c in ok.caveats)


def test_resolution_margin():
    # 4-min data: the 15/hr ceiling is 1.25x the fault threshold, so a fault is reachable
    f = ControlHunting().analyze("AHU1", _alt("4min"))
    assert f.metrics["fault_reachable"] is True and f.severity == "fault"
    assert not any("fault threshold" in c for c in f.caveats)
    # margin 1.0 calls a 12/hr ceiling reachable, though n samples show at most
    # 12 * (n - 2) / (n - 1) an hour -- just under the threshold (#182)
    f = ControlHunting(resolution_margin=1.0).analyze("AHU1", _alt("5min"))
    assert f.metrics["fault_reachable"] is True and f.severity == "warn"
    assert 11.9 < f.metrics["reversals_per_hr"] < 12.0
    with pytest.raises(ValueError):
        ControlHunting(resolution_margin=0.5)
