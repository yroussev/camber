"""Tests for the control-hunting rule + cohort/hunting registration in the builtin registry."""

import os
import sys

import matplotlib

matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


from camber.charts.evidence import finding_evidence  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.hunting_rule import ControlHunting, reversals_per_hour  # noqa: E402


def _series(vals, start="2024-07-01", freq="2min"):
    return pd.Series(vals, index=pd.date_range(start, periods=len(vals), freq=freq))


def test_reversals_per_hour_counts_direction_changes():
    # alternating every 2-min sample -> a reversal each step -> ~30/hr
    alt = _series([0.2, 0.8] * 60)
    rate, n = reversals_per_hour(alt, deadband=0.05)
    assert rate > 20 and n == 118  # 120 samples -> 119 diffs -> 118 sign-change comparisons
    # a monotone ramp has no reversals
    rate2, n2 = reversals_per_hour(_series(np.linspace(0, 1, 100)), deadband=0.01)
    assert rate2 == 0.0 and n2 == 0


def test_hunting_valve_faults_stable_is_ok():
    rule = ControlHunting()
    hunt = ControlHunting().analyze(
        "VAV-1", pd.DataFrame({Role.COOL_VALVE: _series([0.2, 0.8] * 60)})
    )
    stable = rule.analyze(
        "VAV-2", pd.DataFrame({Role.COOL_VALVE: _series(np.linspace(0.2, 0.5, 120))})
    )
    assert hunt.severity == "fault" and hunt.metrics["reversals_per_hr"] >= 12
    assert hunt.metrics["worst_signal"] == Role.COOL_VALVE.value
    assert stable.severity == "ok"


def test_hunting_info_when_no_modulating_output():
    rule = ControlHunting()
    f = rule.analyze("VAV-3", pd.DataFrame({Role.OAT: _series([70.0] * 50)}))
    assert f.severity == "info"


def test_hunting_picks_the_worst_of_several_signals():
    rule = ControlHunting()
    frame = pd.DataFrame(
        {
            Role.HEAT_VALVE: _series(np.linspace(0, 0.3, 120)),  # calm
            Role.COOL_VALVE: _series([0.1, 0.9] * 60),  # hunting
        }
    )
    f = rule.analyze("VAV-4", frame)
    assert f.metrics["worst_signal"] == Role.COOL_VALVE.value and f.severity == "fault"


def test_hunting_evidence_hook():
    rule = ControlHunting()
    frame = pd.DataFrame({Role.COOL_VALVE: _series([0.2, 0.8] * 60)})
    ev = finding_evidence(rule, "VAV-1", frame)
    assert ev is not None and ev.renderer == "multitrend"


def test_new_rules_registered_in_builtin():
    names = set(rule_names())
    assert {"control_hunting", "cohort_airflow", "cohort_space_temp"} <= names
    reg = builtin_registry()
    assert reg.get("control_hunting").name == "control_hunting"
    assert reg.get("cohort_airflow").role == Role.AIRFLOW  # a ready-made cohort fleet rule


# ---------------------------------------------------------------- real-data regressions (0.82.0)


def test_deadband_is_in_percent_so_percent_noise_is_not_hunting():
    # a 0-100 % valve holding ~40 % with +/-1 % measurement noise: the old 0.05 deadband (a 0-1
    # fraction) counted every noise wiggle as a reversal and faulted a steady valve
    rng = np.random.default_rng(3)
    steady = _series(40.0 + rng.uniform(-1.0, 1.0, 600), freq="1min")
    f = ControlHunting().analyze("AHU", pd.DataFrame({Role.COOL_VALVE: steady}))
    assert f.severity == "ok", f.summary
    # ... while a real 20<->60 % limit cycle on the same clock still faults
    hunt = _series([20.0, 60.0] * 300, freq="1min")
    assert ControlHunting().analyze("AHU", pd.DataFrame({Role.COOL_VALVE: hunt})).severity == (
        "fault"
    )


def test_data_gap_is_not_counted_as_calm_time():
    # two days of hunting at 2-min, a 90-day logger outage, two more days of hunting: the rate
    # must be the hunting rate, not diluted by the outage
    a = _series([20.0, 60.0] * 720, start="2024-01-01", freq="2min")
    b = _series([20.0, 60.0] * 720, start="2024-04-01", freq="2min")
    s = pd.concat([a, b])
    rate, _ = reversals_per_hour(s, deadband=5.0)
    rate_a, _ = reversals_per_hour(a, deadband=5.0)
    assert rate == pytest.approx(rate_a, rel=0.02) and rate > 25
    f = ControlHunting().analyze("AHU", pd.DataFrame({Role.OA_DAMPER: s}))
    assert f.severity == "fault"


def test_unsorted_input_counts_the_same():
    s = _series([20.0, 60.0] * 100)
    shuffled = s.sample(frac=1.0, random_state=0)
    assert reversals_per_hour(shuffled, 5.0) == reversals_per_hour(s, 5.0)


def test_coarse_sampling_declines_instead_of_reporting_stable():
    # the real case: a 15-min AHU trend whose OA damper limit-cycles 20->54->20->51 every sample
    # was reported "stable" -- 15-min data can show at most 4 reversals/hr, below the 6/hr warn
    vals = [20.0, 54.0, 20.0, 51.0] * 200
    f = ControlHunting().analyze("AHU", pd.DataFrame({Role.OA_DAMPER: _series(vals, freq="15min")}))
    assert f.severity == "info"
    assert f.metrics["reversals_per_hr"] is None
    assert f.metrics["max_resolvable_per_hr"] == pytest.approx(4.0)
    assert f.metrics["reversal_share_of_max"] > 0.9
    assert any("not evaluated" in c for c in f.caveats)
    assert "stable" not in f.summary


def test_fault_threshold_out_of_reach_caps_at_warn_with_caveat():
    # 8-min data resolves 7.5/hr (above the 6/hr warn) but not 12/hr (fault)
    f = ControlHunting().analyze(
        "AHU", pd.DataFrame({Role.COOL_VALVE: _series([20.0, 60.0] * 200, freq="8min")})
    )
    assert f.severity == "warn"
    assert any("capped at warn" in c for c in f.caveats)
