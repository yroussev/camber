"""Tests for the Sequence-of-Operations conformance engine (camber.soo)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.soo import (  # noqa: E402
    Clause, Predicate, clause_from_dict, evaluate_clause, evaluate_soo,
    soo_findings, spec_from_dicts,
)


def _idx(n):
    return pd.date_range("2025-07-07", periods=n, freq="1h")


# --- predicates --------------------------------------------------------------- #

def test_predicate_ops():
    n = 6
    f = pd.DataFrame({
        Role.OAT: [50, 60, 70, 80, np.nan, 90],
        Role.SUPPLY_AIR_TEMP: [55, 55, 55, 55, 55, 55],
        Role.SUPPLY_AIR_TEMP_SP: [54, 56, 60, 55, 55, 55],
        Role.BOILER_STATUS: [0, 0, 1, 1, 0, 1],
    }, index=_idx(n))

    valid, holds = Predicate(Role.OAT, "gt", value=65).evaluate(f)
    assert list(valid) == [True, True, True, True, False, True]
    assert list(holds.fillna(False).astype(bool)) == [False, False, True, True, False, True]

    # within: |SAT - SAT_SP| <= 2  -> rows 0,1,3,4,5 within (diff 1,1,0,0,0), row2 diff5
    _, w = Predicate(Role.SUPPLY_AIR_TEMP, "within", ref=Role.SUPPLY_AIR_TEMP_SP,
                     tol=2.0).evaluate(f)
    assert list(w.fillna(False).astype(bool)) == [True, True, False, True, True, True]

    _, off = Predicate(Role.BOILER_STATUS, "off").evaluate(f)
    assert list(off) == [True, True, False, False, True, False]


def test_predicate_missing_role_returns_none():
    f = pd.DataFrame({Role.OAT: [70]}, index=_idx(1))
    assert Predicate(Role.BOILER_STATUS, "off").evaluate(f) == (None, None)


# --- clause conformance ------------------------------------------------------- #

def test_summer_lockout_conformance_and_severity():
    # gate: OAT>65 true for first 60 rows; expect boiler off. 48 off / 12 on -> 80%.
    n = 100
    oat = np.array([70.0] * 60 + [50.0] * 40)
    boiler = np.array([0.0] * 48 + [1.0] * 12 + [0.0] * 40)
    f = pd.DataFrame({Role.OAT: oat, Role.BOILER_STATUS: boiler}, index=_idx(n))
    clause = Clause("summer_lockout",
                    when=Predicate(Role.OAT, "gt", value=65),
                    expect=Predicate(Role.BOILER_STATUS, "off"))
    r = evaluate_clause(f, clause)
    assert r.n_applicable == 60
    assert r.conformance_pct == 80.0
    assert r.severity == "warn"          # 80 < 95 (warn) but not < 80 (fault)


def test_full_conformance_is_ok():
    n = 100
    f = pd.DataFrame({
        Role.SUPPLY_FAN_STATUS: np.ones(n),
        Role.SUPPLY_AIR_TEMP: np.full(n, 55.0),
        Role.SUPPLY_AIR_TEMP_SP: np.full(n, 55.0),
    }, index=_idx(n))
    clause = Clause("sat_tracks_sp",
                    when=Predicate(Role.SUPPLY_FAN_STATUS, "on"),
                    expect=Predicate(Role.SUPPLY_AIR_TEMP, "within",
                                     ref=Role.SUPPLY_AIR_TEMP_SP, tol=2.0))
    r = evaluate_clause(f, clause)
    assert r.conformance_pct == 100.0 and r.severity == "ok"


def test_low_conformance_is_fault():
    n = 100
    sat = np.array([55.0] * 60 + [65.0] * 40)   # 40 rows off by 10F -> 60% within
    f = pd.DataFrame({
        Role.SUPPLY_AIR_TEMP: sat,
        Role.SUPPLY_AIR_TEMP_SP: np.full(n, 55.0),
    }, index=_idx(n))
    clause = Clause("sat_tracks_sp",
                    expect=Predicate(Role.SUPPLY_AIR_TEMP, "within",
                                     ref=Role.SUPPLY_AIR_TEMP_SP, tol=2.0))
    r = evaluate_clause(f, clause)
    assert r.conformance_pct == 60.0 and r.severity == "fault"


def test_too_few_applicable_is_info():
    n = 100
    oat = np.array([70.0] * 5 + [50.0] * 95)     # gate true only 5 times
    f = pd.DataFrame({Role.OAT: oat, Role.BOILER_STATUS: np.zeros(n)}, index=_idx(n))
    clause = Clause("summer_lockout", when=Predicate(Role.OAT, "gt", value=65),
                    expect=Predicate(Role.BOILER_STATUS, "off"), min_samples=10)
    assert evaluate_clause(f, clause).severity == "info"


def test_missing_role_is_info():
    n = 50
    f = pd.DataFrame({Role.OAT: np.full(n, 70.0)}, index=_idx(n))
    clause = Clause("x", expect=Predicate(Role.BOILER_STATUS, "off"))
    assert evaluate_clause(f, clause).severity == "info"


# --- report + findings + JSON ------------------------------------------------- #

def test_evaluate_soo_overall_and_worst_severity():
    n = 100
    f = pd.DataFrame({
        Role.OAT: np.array([70.0] * 60 + [50.0] * 40),
        Role.BOILER_STATUS: np.array([0.0] * 48 + [1.0] * 12 + [0.0] * 40),
        Role.SUPPLY_AIR_TEMP: np.full(n, 55.0),
        Role.SUPPLY_AIR_TEMP_SP: np.full(n, 55.0),
    }, index=_idx(n))
    spec = [
        Clause("lockout", when=Predicate(Role.OAT, "gt", value=65),
               expect=Predicate(Role.BOILER_STATUS, "off")),               # 80% warn
        Clause("sat", expect=Predicate(Role.SUPPLY_AIR_TEMP, "within",
                                       ref=Role.SUPPLY_AIR_TEMP_SP, tol=2.0)),  # 100% ok
    ]
    rep = evaluate_soo(f, spec, equip="AHU-1")
    assert rep.severity == "warn"                       # worst of {warn, ok}
    assert rep.overall_conformance == 90.0              # mean(80, 100)
    assert len(rep.clauses) == 2


def test_soo_findings_shape():
    n = 100
    f = pd.DataFrame({Role.OAT: np.array([70.0] * 60 + [50.0] * 40),
                      Role.BOILER_STATUS: np.zeros(n)}, index=_idx(n))
    spec = [Clause("lockout", when=Predicate(Role.OAT, "gt", value=65),
                   expect=Predicate(Role.BOILER_STATUS, "off"))]
    findings = soo_findings(f, spec, "BLR-1")
    assert findings[0].rule == "soo:lockout"
    assert findings[0].equip == "BLR-1"
    assert findings[0].severity == "ok"                 # boiler always off -> 100%
    assert findings[0].metrics["conformance_pct"] == 100.0


def test_json_spec_matches_programmatic():
    n = 100
    f = pd.DataFrame({Role.OAT: np.array([70.0] * 60 + [50.0] * 40),
                      Role.BOILER_STATUS: np.array([0.0] * 48 + [1.0] * 12 + [0.0] * 40)},
                     index=_idx(n))
    spec = spec_from_dicts([{
        "name": "lockout",
        "when": {"subject": "oat", "op": "gt", "value": 65},
        "expect": {"subject": "boiler_status", "op": "off"},
    }])
    assert isinstance(spec[0], Clause)
    assert evaluate_clause(f, spec[0]).conformance_pct == 80.0
    # single-predicate round-trip
    c = clause_from_dict({"name": "y",
                          "expect": {"subject": "supply_air_temp", "op": "within",
                                     "ref": "supply_air_temp_sp", "tol": 1.5}})
    assert c.expect.op == "within" and c.expect.tol == 1.5
