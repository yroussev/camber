"""Tests for the Std-55 overcooling-severity diagnostic (depth x duration)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.overcooling_severity import (  # noqa: E402
    analyze_overcooling_severity, infer_interval, _sustained_mask)
from camber.rules.base import Rule  # noqa: E402
from camber.rules.overcooling_severity_rule import OvercoolingSeverity  # noqa: E402


def _idx(n, freq="1h", start="2025-07-07 00:00"):  # Monday
    return pd.date_range(start, periods=n, freq=freq)


def _occ_frame(n, space, cool=74.0, heat=None, freq="1h"):
    """Build an absolute-mode-friendly frame at the given temps (all-occupied hours
    only matter; we force WarmUp/CoolDown off and rely on the daytime window)."""
    idx = _idx(n, freq=freq)
    data = {"SpaceTemp": np.full(n, space, dtype=float),
            "ActCoolSP": np.full(n, cool, dtype=float)}
    if heat is not None:
        data["ActHeatSP"] = np.full(n, heat, dtype=float)
    return pd.DataFrame(data, index=idx)


# --------------------------------------------------------------------------- #
# Tiers (info excluded from faults handled at the rule layer)
# --------------------------------------------------------------------------- #

def test_three_tiers_absolute():
    n = 24 * 7
    # 71 degF vs 74 SP = 3 degF below -> fault (>=3), also warn (>=2) and info (>=1)
    r = analyze_overcooling_severity(_occ_frame(n, 71.0), "VAV_fault",
                                     occupied_only=False)
    assert r.mode == "absolute"
    assert r.severity == "fault"
    assert r.tier_sustained["fault"] and r.tier_sustained["warn"] and r.tier_sustained["info"]

    # 72 vs 74 = 2 degF -> warn (not fault)
    r = analyze_overcooling_severity(_occ_frame(n, 72.0), "VAV_warn",
                                     occupied_only=False)
    assert r.severity == "warn"
    assert not r.tier_sustained["fault"] and r.tier_sustained["warn"]

    # 73 vs 74 = 1 degF -> info only
    r = analyze_overcooling_severity(_occ_frame(n, 73.0), "VAV_info",
                                     occupied_only=False)
    assert r.severity == "info"
    assert not r.tier_sustained["warn"] and r.tier_sustained["info"]

    # at setpoint -> nothing
    r = analyze_overcooling_severity(_occ_frame(n, 74.0), "VAV_ok",
                                     occupied_only=False)
    assert r.severity == "ok"
    assert not any(r.tier_sustained.values())


def test_info_is_non_actionable_at_rule_layer():
    # The rule emits the result severity directly; an info-only zone -> "info",
    # which the triage layer (SEVERITY_ORDER / _ACTIONABLE) excludes from faults.
    from camber.rules.triage import rank_findings
    n = 24 * 7
    frame = pd.DataFrame({
        Role.SPACE_TEMP: np.full(n, 73.0),
        Role.COOL_SP: np.full(n, 74.0),
    }, index=_idx(n))
    rule = OvercoolingSeverity()
    f = rule.analyze("VAV_info", frame)
    assert f.severity == "info"
    # info is dropped from the actionable (headline) list
    assert rank_findings([f], actionable_only=True) == []


# --------------------------------------------------------------------------- #
# Persistence across fine / coarse / gappy intervals
# --------------------------------------------------------------------------- #

def test_persistence_fine_interval_needs_full_window():
    # 15-min data, 60-min window -> need 4 contiguous qualifying samples.
    n = 8
    idx = _idx(n, freq="15min")
    space = np.full(n, 71.0)         # 3 degF below SP everywhere -> qualifies fault
    # but break persistence: only 3 in a row qualify, then a non-qualifying sample
    space[3] = 74.0                  # sample 3 not overcooled
    df = pd.DataFrame({"SpaceTemp": space, "ActCoolSP": np.full(n, 74.0)}, index=idx)
    r = analyze_overcooling_severity(df, "VAV", window_min=60.0, occupied_only=False)
    # samples 0,1,2 (45 min span = 3 samples, 45<60) then break, then 4,5,6,7 (4 samples=60)
    # so a sustained run exists in the tail -> fault sustained
    assert r.interval_min == 15.0
    assert r.tier_sustained["fault"]
    # exactly the 4 tail samples are sustained (the leading 3 are too short)
    assert r.tier_minutes["fault"] == 60.0


def test_persistence_fine_interval_too_short_not_flagged():
    n = 8
    idx = _idx(n, freq="15min")
    space = np.full(n, 74.0)         # comfortable
    space[0:3] = 71.0                # only 3 qualifying samples (45 min < 60)
    df = pd.DataFrame({"SpaceTemp": space, "ActCoolSP": np.full(n, 74.0)}, index=idx)
    r = analyze_overcooling_severity(df, "VAV", window_min=60.0, occupied_only=False)
    assert not r.tier_sustained["fault"]
    assert r.severity == "ok"


def test_persistence_coarse_interval_single_sample_counts():
    # 60-min data, 60-min window: interval >= window so a single qualifying sample
    # counts (one hourly reading already spans the hour).
    n = 6
    idx = _idx(n, freq="1h")
    space = np.full(n, 74.0)
    space[2] = 71.0                  # one hour overcooled by 3 degF
    df = pd.DataFrame({"SpaceTemp": space, "ActCoolSP": np.full(n, 74.0)}, index=idx)
    r = analyze_overcooling_severity(df, "VAV", window_min=60.0, occupied_only=False)
    assert r.tier_sustained["fault"]
    assert r.tier_minutes["fault"] == 60.0


def test_persistence_gap_does_not_fake_window():
    # 15-min nominal data with a big gap: two short qualifying stretches separated
    # by a multi-hour gap must NOT join into one sustained 60-min run.
    t = pd.to_datetime([
        "2025-07-07 10:00", "2025-07-07 10:15",   # 2 samples, then gap
        "2025-07-07 14:00", "2025-07-07 14:15",   # 2 samples
    ])
    df = pd.DataFrame({"SpaceTemp": [71.0, 71.0, 71.0, 71.0],
                       "ActCoolSP": [74.0, 74.0, 74.0, 74.0]}, index=t)
    r = analyze_overcooling_severity(df, "VAV", window_min=60.0,
                                     interval="15min", occupied_only=False)
    # each contiguous run is only 2x15 = 30 min of coverage -> not sustained
    assert not r.tier_sustained["fault"]
    assert r.severity == "ok"


def test_sustained_mask_helper_interval_ge_window():
    times = pd.date_range("2025-07-07", periods=4, freq="1h").to_numpy()
    q = np.array([False, True, False, True])
    out = _sustained_mask(q, times, pd.Timedelta(minutes=60), pd.Timedelta(minutes=60))
    assert list(out) == [False, True, False, True]   # singles count


# --------------------------------------------------------------------------- #
# Relative-to-deadband vs absolute
# --------------------------------------------------------------------------- #

def test_relative_mode_uses_heating_setpoint():
    # Space at 71, cool SP 74, heat SP 70. In ABSOLUTE mode that is 3 degF below
    # cool SP (fault). In RELATIVE mode the space is still ABOVE the heating SP
    # (71 > 70), i.e. inside the deadband -> not overcooled at all.
    n = 24 * 7
    frame = _occ_frame(n, 71.0, cool=74.0, heat=70.0)
    rel = analyze_overcooling_severity(frame, "VAV", relative_to_deadband=True,
                                       occupied_only=False)
    assert rel.mode == "relative_deadband"
    assert rel.severity == "ok"            # inside the deadband

    ab = analyze_overcooling_severity(frame, "VAV", relative_to_deadband=False,
                                      occupied_only=False)
    assert ab.mode == "absolute"
    assert ab.severity == "fault"          # 3 degF below the cooling SP


def test_relative_mode_flags_below_heating_setpoint():
    # Space pushed below the heating setpoint -> overcooled past the deadband floor.
    n = 24 * 7
    frame = _occ_frame(n, 67.0, cool=74.0, heat=70.0)   # 3 degF below heat SP
    rel = analyze_overcooling_severity(frame, "VAV", relative_to_deadband=True,
                                       occupied_only=False)
    assert rel.mode == "relative_deadband"
    assert rel.severity == "fault"
    assert rel.max_depth_f == 3.0


def test_falls_back_to_absolute_without_heating_setpoint():
    n = 24 * 7
    frame = _occ_frame(n, 71.0, cool=74.0)   # no ActHeatSP
    r = analyze_overcooling_severity(frame, "VAV", relative_to_deadband=True,
                                     occupied_only=False)
    assert r.mode == "absolute"              # fell back
    assert r.severity == "fault"


# --------------------------------------------------------------------------- #
# Misc
# --------------------------------------------------------------------------- #

def test_infer_interval():
    assert infer_interval(_idx(10, freq="15min")) == pd.Timedelta(minutes=15)
    assert infer_interval(_idx(1)) is None


def test_rule_protocol():
    rule = OvercoolingSeverity()
    assert isinstance(rule, Rule)
    assert rule.name == "overcooling_severity"


def test_missing_columns_returns_none():
    df = pd.DataFrame({"SpaceTemp": [70.0]}, index=_idx(1))
    assert analyze_overcooling_severity(df, "VAV") is None
