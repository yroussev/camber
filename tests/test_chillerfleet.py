"""Tests for the multi-chiller staging fleet diagnostic (over-staging census)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerstaging import analyze_chiller_staging_fleet  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import FleetRule  # noqa: E402
from camber.rules.chillerfleet_rule import ChillerStagingFleet  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


def _pair_solo_then_shared(n, solo=60, peak=480.0, shared=150.0):
    """Two chillers that each carry load solo for a window (establishing capacity),
    then both run together at low load (the over-staged condition)."""
    rest = n - 2 * solo
    p1 = np.concatenate([np.full(solo, peak), np.full(solo, 0.0), np.full(rest, shared)])
    p2 = np.concatenate([np.full(solo, 0.0), np.full(solo, peak), np.full(rest, shared)])
    return (pd.DataFrame({"Power": p1}, index=_idx(n)),
            pd.DataFrame({"Power": p2}, index=_idx(n)))


# --- diagnostic --------------------------------------------------------------- #

def test_overstaged_when_both_run_at_low_load():
    n = 24 * 14
    # each peaks ~480 solo -> capacity ~480; then both run at 150 (total 300 fits in
    # one chiller at <= 0.9*480=432) -> those shared hours are over-staged
    a, b = _pair_solo_then_shared(n)
    r = analyze_chiller_staging_fleet({"CH-1": a, "CH-2": b})
    assert r.n_chillers == 2
    assert r.pct_overstaged > 90


def test_not_overstaged_when_both_loaded():
    n = 24 * 14
    a = pd.DataFrame({"Power": np.full(n, 480.0)}, index=_idx(n))  # both near capacity
    b = pd.DataFrame({"Power": np.full(n, 480.0)}, index=_idx(n))
    r = analyze_chiller_staging_fleet({"CH-1": a, "CH-2": b})
    assert r.pct_overstaged < 5


def test_single_chiller_returns_none():
    n = 24 * 14
    a = pd.DataFrame({"Power": np.full(n, 300.0)}, index=_idx(n))
    assert analyze_chiller_staging_fleet({"CH-1": a}) is None


# --- fleet rule wrapper ------------------------------------------------------- #

def test_rule_is_a_fleet_rule_and_severity():
    assert isinstance(ChillerStagingFleet(), FleetRule)
    n = 24 * 14

    def frame(kw, peak=500.0):
        f = pd.DataFrame({Role.POWER: np.full(n, kw)}, index=_idx(n))
        f.iloc[:5] = peak
        return f

    rule = ChillerStagingFleet()
    a, b = _pair_solo_then_shared(n)
    overstaged = rule.analyze_fleet({"CH-1": a.rename(columns={"Power": Role.POWER}),
                                     "CH-2": b.rename(columns={"Power": Role.POWER})})
    assert overstaged.severity == "fault"
    assert overstaged.equip == "<fleet>"

    loaded = rule.analyze_fleet({"CH-1": frame(480.0), "CH-2": frame(480.0)})
    assert loaded.severity == "ok"


def test_rule_single_chiller_reports_info():
    n = 24 * 14
    f = pd.DataFrame({Role.POWER: np.full(n, 300.0)}, index=_idx(n))
    assert ChillerStagingFleet().analyze_fleet({"CH-1": f}).severity == "info"
