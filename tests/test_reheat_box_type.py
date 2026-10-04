"""reheat_penalty ``box_type`` (0.100, #99): the "auto" fan-heat caps depend on the box type.

A series fan-powered box lifts its closed-valve discharge well past the 8 °F cap that a parallel
box never reaches. ``box_type="series"`` raises the no-rise cap (valve open) to 10 °F and keeps
the big-rise cap (valve shut) at 8 °F, because the estimate is learned from those same closed
samples. The default (None) is unchanged.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from camber.model.roles import Role  # noqa: E402
from camber.rules.builtin import make_rule  # noqa: E402
from camber.rules.reheat_rule import BOX_TYPES, ReheatPenalty  # noqa: E402

_NEW_KEYS = ("box_type", "fan_lift_f", "fan_heat_closed_f")


def _box(open_rise, closed_rise, *, late_closed_rise=None, n_days=14):
    """Hourly box: on occupied weekday hours (07-18) the valve is 100 % before noon, 0 % after.

    Discharge = entering air (55 F) + ``open_rise`` at 100 %, else + ``closed_rise``. With
    ``late_closed_rise`` the last two closed hours of each day (16-17 h, two of the five steady
    samples) read that rise instead, as a passing valve on part of the day would.
    """
    idx = pd.date_range("2025-01-06", periods=n_days * 24, freq="1h")  # a Monday
    occ = (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 18)
    valve = np.where(occ & (idx.hour < 12), 100.0, 0.0)
    closed = np.full(len(idx), float(closed_rise))
    if late_closed_rise is not None:
        closed = np.where(idx.hour >= 16, float(late_closed_rise), closed)
    dat = 55.0 + np.where(valve > 0, float(open_rise), closed)
    return pd.DataFrame(
        {Role.HEAT_VALVE: valve, Role.SUPPLY_AIR_TEMP: dat, Role.MIXED_AIR_TEMP: 55.0}, index=idx
    )


def _run(frame, **kw):
    return ReheatPenalty(**kw).analyze("B", frame)


def test_the_default_is_unchanged_and_adds_no_metrics():
    fr = _box(open_rise=30.0, closed_rise=14.0)
    f = _run(fr, fan_heat_f="auto")
    assert f.metrics["fan_heat_f"] == 8.0  # the 0.98 clip
    assert not any(k in f.metrics for k in _NEW_KEYS)


def test_parallel_caps_like_the_default():
    fr = _box(open_rise=12.5, closed_rise=14.0)  # declined at 5 + 8 = 13 F
    base, par = _run(fr, fan_heat_f="auto"), _run(fr, fan_heat_f="auto", box_type="parallel")
    assert par.severity == base.severity and par.summary == base.summary
    assert par.metrics["fan_heat_f"] == base.metrics["fan_heat_f"] == 8.0
    assert par.metrics["fan_heat_closed_f"] == 8.0 and par.metrics["fan_lift_f"] == 14.0
    assert par.metrics["box_type"] == "parallel"


def test_series_raises_the_no_rise_bound_to_10_f_of_fan_heat():
    # a series box whose full valve adds under 1 F over its 14 F fan lift: no heat reaches the
    # air. The 0.98 clip (bound 13 F) corroborates it; the series cap (bound 15 F) declines it.
    fr = _box(open_rise=14.5, closed_rise=14.0)
    assert _run(fr, fan_heat_f="auto").metrics["valve_dat_consistency"] == "consistent"
    f = _run(fr, fan_heat_f="auto", box_type="series")
    assert f.metrics["declined"] and f.severity == "info"
    assert f.metrics["fan_heat_f"] == 10.0 and f.metrics["fan_lift_f"] == 14.0
    assert "allowing 10°F of fan heat" in f.summary


def test_series_keeps_a_working_valve():
    f = _run(_box(open_rise=21.0, closed_rise=14.0), fan_heat_f="auto", box_type="series")
    assert f.metrics["valve_dat_consistency"] == "consistent" and "declined" not in f.metrics


def test_series_keeps_the_big_rise_cap_at_8_f():
    # two of the five steady closed hours read 19 F (a passing valve): the 12 F median is
    # learned as fan heat. With the closed allowance at 8 F the 18 F line still catches them; a
    # 12 F allowance (an uncapped estimate) would put the line at 22 F and cancel the check.
    fr = _box(open_rise=30.0, closed_rise=12.0, late_closed_rise=19.0)
    f = _run(fr, fan_heat_f="auto", box_type="series")
    assert f.metrics["fan_heat_f"] == 10.0 and f.metrics["fan_heat_closed_f"] == 8.0
    assert f.metrics["fan_lift_f"] == 12.0
    assert f.metrics["valve_dat_consistency"] == "closed_with_rise"
    assert f.metrics["valve_closed_big_rise_share"] == 0.4


def test_single_duct_caps_at_3_f():
    fr = _box(open_rise=9.0, closed_rise=6.0)
    assert _run(fr, fan_heat_f="auto").metrics["declined"]  # 9 < 5 + 6
    f = _run(fr, fan_heat_f="auto", box_type="single_duct")
    assert f.metrics["fan_heat_f"] == 3.0 and f.metrics["fan_heat_closed_f"] == 3.0
    assert f.metrics["valve_dat_consistency"] == "consistent"  # 9 >= 5 + 3


def test_box_type_leaves_a_numeric_or_absent_fan_heat_alone():
    fr = _box(open_rise=30.0, closed_rise=14.0)
    f = _run(fr, fan_heat_f=12.0, box_type="single_duct")
    assert f.metrics["fan_heat_f"] == f.metrics["fan_heat_closed_f"] == 12.0
    assert f.metrics["fan_lift_f"] is None
    g = _run(fr, box_type="series")
    assert g.metrics["fan_heat_f"] is None and g.metrics["fan_heat_closed_f"] is None


@pytest.mark.parametrize("bad", ["fpb", "Series", 1, True])
def test_box_type_is_validated(bad):
    with pytest.raises(ValueError, match="box_type"):
        ReheatPenalty(box_type=bad)


def test_box_type_is_config_tunable():
    assert BOX_TYPES == ("single_duct", "parallel", "series")
    rule = make_rule("reheat_penalty", fan_heat_f="auto", box_type="series")
    assert rule.box_type == "series" and rule.fan_heat_f == "auto"
