"""Tests for ASHRAE 62.1 ventilation-rate + DCV verification (camber.ventilation + rule)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.ventilation_rule import (  # noqa: E402
    VentilationRateProcedure,
)
from camber.ventilation import (  # noqa: E402
    assess_62_1,
    oa_rates_for,
    required_oa_cfm,
)

# --------------------------------------------------------------------------- VRP math


def test_required_oa_cfm_vrp_formula():
    # office: Rp=5 cfm/person, Ra=0.06 cfm/ft²; 2000 ft², 10 people -> 5*10 + 0.06*2000 = 170
    assert required_oa_cfm(2000, 10, rp=5.0, ra=0.06) == 170.0
    # Ez halves effectiveness -> doubles required
    assert required_oa_cfm(2000, 10, rp=5.0, ra=0.06, ez=0.5) == 340.0


def test_oa_rates_lookup_and_unknown():
    assert oa_rates_for("Office") == (5.0, 0.06)
    assert oa_rates_for("classroom") == (10.0, 0.12)
    try:
        oa_rates_for("submarine")
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_assess_62_1_under_adequate_over():
    # required = 170; measured below/at/above
    under = assess_62_1(120.0, area_sqft=2000, population=10, space_type="office")
    assert under.status == "under" and under.required_cfm == 170.0
    assert under.deficit_cfm == 50.0 and under.ratio < 0.9

    ok = assess_62_1(175.0, area_sqft=2000, population=10, space_type="office")
    assert ok.status == "adequate" and ok.deficit_cfm == 0.0

    over = assess_62_1(400.0, area_sqft=2000, population=10, space_type="office")
    assert over.status == "over" and over.ratio > 1.5


def test_assess_62_1_series_aggregate_and_occupied_mask():
    idx = pd.date_range("2024-06-03", periods=24, freq="1h")  # a Monday
    oa = pd.Series([300.0] * 24, index=idx)
    oa.iloc[7:18] = 120.0  # occupied hours run low
    occ = pd.Series(idx.hour.isin(range(7, 18)), index=idx)
    # median over occupied hours = 120 -> under; without the mask, median ~300 -> over
    r_occ = assess_62_1(
        oa,
        area_sqft=2000,
        population=10,
        space_type="office",
        occupied_mask=occ,
        aggregate="median",
    )
    assert r_occ.status == "under" and r_occ.measured_cfm == 120.0 and r_occ.n == 11
    r_all = assess_62_1(oa, area_sqft=2000, population=10, space_type="office", aggregate="median")
    assert r_all.status == "over"


def test_assess_62_1_explicit_rates_override_table():
    r = assess_62_1(100.0, area_sqft=1000, population=5, rp=10.0, ra=0.12)  # 10*5+0.12*1000=170
    assert r.required_cfm == 170.0 and r.rp == 10.0 and r.ra == 0.12


# --------------------------------------------------------------------------- rules


def test_vrp_rule_under_ventilation_is_fault():
    idx = pd.date_range("2024-06-03", periods=48, freq="1h")
    frame = pd.DataFrame({Role.OA_AIRFLOW: np.full(48, 120.0)}, index=idx)
    rule = VentilationRateProcedure(
        area_sqft=2000, population=10, space_type="office", occupied_only=False
    )
    f = rule.analyze("AHU-1", frame)
    assert f.rule == "ventilation_rate_62_1" and f.severity == "fault"
    assert f.metrics["required_cfm"] == 170.0 and f.metrics["status"] == "under"


def test_dcv_rule_auto_registered_but_not_vrp():
    from camber.rules.builtin import rule_names

    names = rule_names()
    assert "dcv_verification" in names  # config-free -> auto-registered
    assert "dcv_system_verification" in names  # fleet twin: zone CO2 joined to the AHU's OA
    assert "ventilation_rate_62_1" not in names  # needs design inputs -> explicit only
