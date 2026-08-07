"""Metamorphic property tests (hypothesis): a transform of the input that a diagnostic must be
blind to should not change its verdict -- and, as negative controls, a transform it must *not* be
blind to should. These pin the exact caveats (whole-week not day; the flow *pair* not the absolute
damper threshold; both temps not one) that make each relation actually true.
"""

import os
import sys

import numpy as np
import pandas as pd
from hypothesis import given
from hypothesis import strategies as st

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.leakvalve import analyze_leak_valves  # noqa: E402
from camber.overcooling import analyze_overcooling  # noqa: E402
from camber.reheat import analyze_box  # noqa: E402

_N = 24 * 21  # three weeks, hourly


def _idx():
    return pd.date_range("2024-07-01", periods=_N, freq="h")  # a Monday


def _verdict(res):
    """The analyzer result minus its two timestamp fields (which legitimately move on a shift)."""
    d = res.as_dict()
    d.pop("coverage_start", None)
    d.pop("coverage_end", None)
    return d


def _leak_frame():
    # both valves shut, supply 7°F BELOW mixed -> a definite chilled-water-coil leak
    return pd.DataFrame(
        {
            "CHW_Valve": np.zeros(_N),
            "HHW_Valve": np.zeros(_N),
            "MixedAir": np.full(_N, 75.0),
            "SupplyAir": np.full(_N, 68.0),
        },
        index=_idx(),
    )


def _overcool_frame():
    return pd.DataFrame(
        {
            "SpaceTemp": np.full(_N, 70.0),
            "ActCoolSP": np.full(_N, 74.0),  # satisfied
            "ActFlow": np.full(_N, 640.0),
            "ActFlowSP": np.full(_N, 630.0),  # at min
            "Damper": np.full(_N, 20.0),  # pinned low
            "HWValve": np.full(_N, 40.0),  # reheating
        },
        index=_idx(),
    )


def _reheat_frame():
    return pd.DataFrame(
        {
            "HWValve": np.full(_N, 40.0),  # reheat firing
            "SupplyAir": np.full(_N, 55.0),  # cold supply
            "ActFlow": np.full(_N, 640.0),
            "ActFlowSP": np.full(_N, 500.0),  # above min flow
        },
        index=_idx(),
    )


# --------------------------------------------------------------------------- time-shift invariance


@given(k=st.integers(1, 8))
def test_leakvalve_invariant_under_week_shift(k):
    df = _leak_frame()
    v0 = _verdict(analyze_leak_valves(df, "AHU"))
    df2 = df.copy()
    df2.index = df.index + pd.Timedelta(weeks=k)
    assert _verdict(analyze_leak_valves(df2, "AHU")) == v0


@given(k=st.integers(1, 8))
def test_overcooling_invariant_under_week_shift(k):
    df = _overcool_frame()
    v0 = _verdict(analyze_overcooling(df, "VAV"))
    df2 = df.copy()
    df2.index = df.index + pd.Timedelta(weeks=k)
    assert _verdict(analyze_overcooling(df2, "VAV")) == v0


@given(k=st.integers(1, 8))
def test_reheat_invariant_under_week_shift(k):
    df = _reheat_frame()
    v0 = _verdict(analyze_box(df, "VAV"))
    df2 = df.copy()
    df2.index = df.index + pd.Timedelta(weeks=k)
    assert _verdict(analyze_box(df2, "VAV")) == v0


# --------------------------------------------------------------------- bounded value transforms


@given(c=st.floats(-8.0, 8.0))
def test_leakvalve_invariant_under_common_temp_offset(c):
    # the verdict is a function of (SupplyAir - MixedAir); offsetting BOTH within the [30,120]
    # plausibility guard leaves that delta -- and every metric -- unchanged.
    df = _leak_frame()
    v0 = _verdict(analyze_leak_valves(df, "AHU"))
    df2 = df.copy()
    df2["SupplyAir"] += c
    df2["MixedAir"] += c
    assert _verdict(analyze_leak_valves(df2, "AHU")) == v0


@given(k=st.floats(0.5, 5.0))
def test_overcooling_invariant_under_flow_scale(k):
    # at_min is ActFlow <= ActFlowSP*(1+tol); scaling BOTH by k>0 preserves it (and the ratio
    # metric median_minflow_fraction), so the verdict is unchanged.
    df = _overcool_frame()
    v0 = _verdict(analyze_overcooling(df, "VAV"))
    df2 = df.copy()
    df2["ActFlow"] *= k
    df2["ActFlowSP"] *= k
    assert _verdict(analyze_overcooling(df2, "VAV")) == v0


# --------------------------------------------------------------------------- negative controls
# The relations above are specific; these document exactly where they stop.


def test_leakvalve_offsetting_only_supply_air_changes_the_verdict():
    df = _leak_frame()  # chw leak (supply 7°F below mixed)
    before = analyze_leak_valves(df, "AHU").chw_leak_pct
    df2 = df.copy()
    df2["SupplyAir"] += 15.0  # now 8°F ABOVE mixed -> the delta flips; not a common offset
    assert analyze_leak_valves(df2, "AHU").chw_leak_pct < before  # relation broken (correctly)


def test_overcooling_scaling_only_the_damper_changes_the_verdict():
    df = _overcool_frame()  # damper pinned at 20 (< 30 low threshold)
    before = analyze_overcooling(df, "VAV").overcool_at_minflow_pct
    df2 = df.copy()
    df2["Damper"] *= 10.0  # 200 -> no longer "pinned low"; damper threshold is absolute, not scaled
    assert analyze_overcooling(df2, "VAV").overcool_at_minflow_pct < before
