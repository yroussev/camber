"""Tests for the G36 SAT-reset-compliance rule (camber.rules.satreset_compliance_rule)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.g36_reset import oat_sat_setpoint  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Rule  # noqa: E402
from camber.rules.builtin import builtin_registry, make_rule, rule_names  # noqa: E402
from camber.rules.satreset_compliance_rule import SupplyAirResetCompliance  # noqa: E402


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")  # a Monday


# --------------------------------------------------------------------------- interface


def test_compliance_rule_protocol():
    assert isinstance(SupplyAirResetCompliance(), Rule)


def test_auto_registered():
    assert "supply_air_reset_compliance" in rule_names()
    inst = builtin_registry().get("supply_air_reset_compliance")
    assert isinstance(inst, SupplyAirResetCompliance)
    # constructible with overridden params via make_rule
    r = make_rule("supply_air_reset_compliance", oat_min=58.0, warn_pct=25.0)
    assert isinstance(r, SupplyAirResetCompliance) and r.warn_pct == 25.0


# --------------------------------------------------------------------------- the detector


def test_ok_when_sat_tracks_g36_target():
    n = 24 * 14
    rng = np.random.default_rng(2)
    oat = np.linspace(55, 75, n)
    sat = oat_sat_setpoint(oat) + rng.normal(0, 0.3, n)  # SAT rides the G36 target
    frame = pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat, Role.OAT: oat}, index=_idx(n))
    f = SupplyAirResetCompliance().analyze("AHU_1", frame)
    assert f.severity == "ok" and f.metrics["pct_below_g36_target"] < 15


def test_warn_when_pinned_cold_vs_target():
    n = 24 * 14
    rng = np.random.default_rng(0)
    oat = rng.uniform(60.0, 72.0, n)  # mild -> the G36 target sits above 55
    sat = np.full(n, 54.0)  # pinned cold, below the target most hours
    frame = pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat, Role.OAT: oat}, index=_idx(n))
    f = SupplyAirResetCompliance().analyze("AHU_1", frame)
    assert f.severity == "warn"
    assert f.metrics["pct_below_g36_target"] >= 40 and f.metrics["mean_gap_f"] > 0
    assert "reheat/energy opportunity" in f.summary


def test_g36_params_honored_change_the_verdict():
    """A colder site reset schedule (lower target) turns the same cold SAT compliant."""
    n = 24 * 14
    rng = np.random.default_rng(0)
    oat = rng.uniform(60.0, 72.0, n)
    sat = np.full(n, 54.0)
    frame = pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat, Role.OAT: oat}, index=_idx(n))
    # a site whose G36 target is ~54 across this OAT band -> no gap -> ok
    rule = SupplyAirResetCompliance(min_clg_sat=54.0, t_max=54.0)
    f = rule.analyze("AHU_1", frame)
    assert f.severity == "ok" and f.metrics["pct_below_g36_target"] < 15


def test_gap_floor_suppresses_a_trivial_warn():
    """Below target most hours but by less than the mean-gap floor -> not flagged."""
    n = 24 * 14
    rng = np.random.default_rng(3)
    oat = rng.uniform(60.0, 72.0, n)
    target = oat_sat_setpoint(oat)
    sat = target - 1.2  # below target, but under the 2.0°F floor
    frame = pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat, Role.OAT: oat}, index=_idx(n))
    f = SupplyAirResetCompliance().analyze("AHU_1", frame)
    assert f.severity == "ok" and f.metrics["mean_gap_f"] < 2.0


# --------------------------------------------------------------------------- declines


def test_declines_loudly_when_oat_unmapped():
    n = 24 * 14
    sat = np.full(n, 54.0)
    frame = pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat}, index=_idx(n))
    f = SupplyAirResetCompliance().analyze("AHU_1", frame)
    assert f.severity == "info" and f.metrics["declined"] is True
    assert "OAT" in f.metrics["reason"] and f.caveats


def test_declines_when_too_few_rows():
    frame = pd.DataFrame(
        {Role.SUPPLY_AIR_TEMP: np.full(5, 55.0), Role.OAT: np.linspace(60, 70, 5)}, index=_idx(5)
    )
    f = SupplyAirResetCompliance().analyze("AHU_1", frame)
    assert f.severity == "info" and f.metrics["declined"] is True
    assert "insufficient" in f.metrics["reason"]
