"""Tests for point-mapping confidence scoring (camber.mapping_confidence)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mapping_confidence import review, score_token  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402


def _mapping():
    return MappingProvider(
        aliases={"HHW_Valve": Role.HEAT_VALVE, "OSA": Role.OAT},
        patterns=[(r".*Temp$", Role.SPACE_TEMP), (r".*SAT.*", Role.SUPPLY_AIR_TEMP)],
    )


def _series(vals, n=200):
    return pd.Series(
        np.resize(np.asarray(vals, dtype=float), n),
        index=pd.date_range("2025-07-07", periods=n, freq="1h"),
    )


# --- match basis -------------------------------------------------------------- #


def test_alias_is_high_confidence():
    c = score_token("HHW_Valve", _mapping())
    assert c.basis == "alias" and c.role == "heat_valve"
    assert c.verdict == "high" and c.flags == []


def test_pattern_is_medium_confidence():
    c = score_token("ZoneTemp", _mapping())  # matches .*Temp$ -> space_temp
    assert c.basis == "pattern" and c.role == "space_temp"
    assert c.verdict == "medium" and not c.ambiguous


def test_unmapped_token():
    c = score_token("MysteryPoint", _mapping())
    assert c.basis == "unmapped" and c.role is None
    assert c.confidence == 0.0 and "unmapped" in c.flags


# --- ambiguity ---------------------------------------------------------------- #


def test_ambiguous_pattern_lowers_confidence():
    # "SAT_Temp" matches BOTH .*Temp$ (space_temp) and .*SAT.* (supply_air_temp)
    c = score_token("SAT_Temp", _mapping())
    assert c.ambiguous and "ambiguous" in c.flags
    assert c.confidence < score_token("ZoneTemp", _mapping()).confidence


def test_alias_overrides_ambiguity():
    m = MappingProvider(
        aliases={"SAT_Temp": Role.SUPPLY_AIR_TEMP},
        patterns=[(r".*Temp$", Role.SPACE_TEMP), (r".*SAT.*", Role.SUPPLY_AIR_TEMP)],
    )
    c = score_token("SAT_Temp", m)
    assert c.basis == "alias" and not c.ambiguous and c.verdict == "high"


# --- data fit ----------------------------------------------------------------- #


def test_data_fit_confirms_good_mapping():
    # OSA->oat, data is plausible outdoor temps -> high confidence, data_fit ~1
    c = score_token("OSA", _mapping(), _series([60, 75, 90, 105]))
    assert c.data_fit > 0.95 and c.verdict == "high"


def test_data_mismatch_drops_confidence():
    # OSA->oat but the data sits at 250F (impossible for outdoor air) -> mismapped
    c = score_token("OSA", _mapping(), _series([250.0]))
    assert "data_mismatch" in c.flags
    assert c.verdict == "low" and c.data_fit < 0.1


# --- review roll-up ----------------------------------------------------------- #


def test_review_partitions_tokens():
    tokens = ["HHW_Valve", "ZoneTemp", "SAT_Temp", "MysteryPoint", "OSA"]
    sbt = {"OSA": _series([250.0])}  # OSA data is impossible -> flagged
    rep = review(tokens, _mapping(), sbt, min_confidence=0.5)
    assert rep["n"] == 5
    unmapped = {s.token for s in rep["unmapped"]}
    needs = {s.token for s in rep["needs_review"]}
    assert unmapped == {"MysteryPoint"}
    assert "SAT_Temp" in needs  # ambiguous
    assert "OSA" in needs  # data mismatch
    assert "HHW_Valve" not in needs  # solid alias


# --- unit / scale evidence ------------------------------------------------------------------- #


def _alias(token, role):
    return MappingProvider.from_dict({"aliases": {token: role}, "patterns": []})


def test_percent_scaled_series_under_a_cfm_role_is_not_high_confidence():
    # a fan-speed % point typed as a supply-airflow sensor: 0-100 data sits inside the airflow
    # bounds (-1..1e6), so the range check alone rated it "high" 0.95
    idx = pd.date_range("2024-01-01", periods=200, freq="h")
    pct = pd.Series(np.clip(np.random.default_rng(0).normal(45, 25, 200), 0, 100), index=idx)
    s = score_token("zone_1_fan_spd", _alias("zone_1_fan_spd", "airflow"), pct)
    assert "percent_scale" in s.flags and s.verdict != "high" and s.confidence < 0.5
    cfm = pct * 12 + 150  # a real VAV airflow, hundreds of cfm
    assert score_token("vav_1_flow", _alias("vav_1_flow", "airflow"), cfm).verdict == "high"


def test_declared_units_drive_the_scale_and_range_checks():
    idx = pd.date_range("2024-01-01", periods=48, freq="h")
    pct = pd.Series(np.linspace(0, 100, 48), index=idx)
    mp = _alias("sf_spd", "airflow")
    assert "unit_mismatch" in score_token("sf_spd", mp, pct, unit="%").flags
    # a declared cfm unit is trusted: a small box can legitimately read 0-100 cfm
    assert score_token("sf_spd", mp, pct, unit="cfm").flags == []
    # a 13-15 degC supply air is fine once the unit is known (it is below freezing read as degF)
    sat_c = pd.Series(np.linspace(13, 15, 48), index=idx)
    mp2 = _alias("sat", "supply_air_temp")
    assert "data_mismatch" in score_token("sat", mp2, sat_c).flags
    assert score_token("sat", mp2, sat_c, unit="degC").verdict == "high"
    assert score_token("sat", mp2, sat_c + 273.15, unit="K").verdict == "high"


def test_review_routes_scale_and_unit_flags_to_needs_review():
    idx = pd.date_range("2024-01-01", periods=48, freq="h")
    pct = pd.Series(np.linspace(0, 100, 48), index=idx)
    mp = MappingProvider.from_dict({"aliases": {"a": "airflow", "b": "airflow"}, "patterns": []})
    rev = review(["a", "b"], mp, {"a": pct, "b": pct}, units={"b": "%"})
    assert {s.token for s in rev["needs_review"]} == {"a", "b"}
