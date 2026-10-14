"""Tests for the chiller staging/cycling diagnostic (starts-per-day + low load)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerstaging import analyze_chiller_staging  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.chillerstaging_rule import ChillerStaging  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


# --- diagnostic --------------------------------------------------------------- #


def test_steady_run_low_cycling():
    n = 24 * 14
    df = pd.DataFrame({"Power": np.full(n, 120.0)}, index=_idx(n))  # never stops
    r = analyze_chiller_staging(df, "CH-1")
    assert r.starts_per_day < 0.2  # one start across two weeks
    assert r.runtime_pct > 99


def test_short_cycling_flagged():
    n = 24 * 14
    power = np.tile([120.0, 0.0], n // 2)  # on/off every hour -> 12 starts/day
    df = pd.DataFrame({"Power": power}, index=_idx(n))
    r = analyze_chiller_staging(df, "CH-1")
    assert r.starts_per_day >= 11.5


def test_low_part_load_detected():
    n = 24 * 14
    # always running; 10% of hours at peak (~200 tons), rest at ~50 tons (25% LF)
    flow = np.where(np.arange(n) % 10 == 0, 400.0, 100.0)  # gpm
    df = pd.DataFrame(
        {
            "Power": np.full(n, 120.0),
            "CHWS_Temp": np.full(n, 44.0),
            "CHWR_Temp": np.full(n, 56.0),  # 12F dT -> tons = gpm/2
            "CHW_Flow": flow,
        },
        index=_idx(n),
    )
    r = analyze_chiller_staging(df, "CH-1", min_load_pct=40.0)
    assert r.low_load_pct > 80  # most running hours are lightly loaded
    assert r.load_factor_median_pct < 40


def test_insufficient_data_returns_none():
    assert (
        analyze_chiller_staging(pd.DataFrame({"Power": [100.0] * 4}, index=_idx(4)), "CH-1") is None
    )


# --- rule wrapper ------------------------------------------------------------- #


def test_rule_is_a_rule_and_severity():
    assert isinstance(ChillerStaging(), Rule)
    n = 24 * 14

    steady = pd.DataFrame({Role.POWER: np.full(n, 120.0)}, index=_idx(n))
    assert ChillerStaging(max_starts_per_day=6).analyze("CH-1", steady).severity == "ok"

    cycling = pd.DataFrame({Role.POWER: np.tile([120.0, 0.0], n // 2)}, index=_idx(n))
    assert ChillerStaging(max_starts_per_day=6).analyze("CH-1", cycling).severity == "fault"


def test_rule_missing_roles_reports_info():
    n = 24
    frame = pd.DataFrame({Role.CHW_SUPPLY_TEMP: np.full(n, 44.0)}, index=_idx(n))
    assert ChillerStaging().analyze("CH-1", frame).severity == "info"


# --- 0.105 (#148): every run at one output ------------------------------------ #


def _runs_at(tons, n=96 * 14):
    """A 15-minute trend, power on/off in 4-hour blocks; each running sample carries the given
    tons (gpm = 2 * tons at a 12 F deltaT)."""
    on = (np.arange(n) // 16) % 2 == 0
    return pd.DataFrame(
        {
            Role.POWER: np.where(on, 120.0, 0.0),
            Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
            Role.CHW_RETURN_TEMP: np.full(n, 56.0),
            Role.CHW_FLOW: np.where(on, 2.0 * np.asarray(tons), 0.0),
        },
        index=pd.date_range("2025-07-07", periods=n, freq="15min"),
    )


def test_one_output_is_caveated_with_rounding_jitter():
    n = 96 * 14
    # always at its 150-ton minimum, read through a flow meter that rounds to +-1 %
    jitter = 150.0 * (1.0 + 0.01 * np.sin(np.arange(n) * 1.7))
    f = ChillerStaging().analyze("CH", _runs_at(jitter))
    assert f.metrics["load_factor_median_pct"] > 97.0 and f.metrics["load_spread_pct"] <= 5.0
    assert len(f.caveats) == 1 and "same output" in f.caveats[0]
    plain = ChillerStaging().analyze("CH", _runs_at(np.full(n, 150.0)))
    assert plain.metrics["load_spread_pct"] == 0.0 and "same output" in plain.caveats[0]


def test_varied_output_is_not_caveated_and_the_verdict_is_unchanged():
    n = 96 * 14
    varied = 60.0 + 90.0 * (np.arange(n) % 96) / 95.0  # 60..150 tons
    f = ChillerStaging().analyze("CH", _runs_at(varied))
    assert f.metrics["load_spread_pct"] > 40.0 and f.caveats == []
    same = ChillerStaging().analyze("CH", _runs_at(np.full(n, 150.0)))
    assert same.severity == ChillerStaging().analyze("CH", _runs_at(varied)).severity == "ok"
    # power alone: no tons, no spread, no caveat
    power = ChillerStaging().analyze("CH", _runs_at(varied)[[Role.POWER]])
    assert power.metrics["load_spread_pct"] != power.metrics["load_spread_pct"]  # NaN
    assert power.caveats == []
