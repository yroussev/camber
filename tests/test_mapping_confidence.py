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
    return pd.Series(np.resize(np.asarray(vals, dtype=float), n),
                     index=pd.date_range("2025-07-07", periods=n, freq="1h"))


# --- match basis -------------------------------------------------------------- #

def test_alias_is_high_confidence():
    c = score_token("HHW_Valve", _mapping())
    assert c.basis == "alias" and c.role == "heat_valve"
    assert c.verdict == "high" and c.flags == []


def test_pattern_is_medium_confidence():
    c = score_token("ZoneTemp", _mapping())          # matches .*Temp$ -> space_temp
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
    m = MappingProvider(aliases={"SAT_Temp": Role.SUPPLY_AIR_TEMP},
                        patterns=[(r".*Temp$", Role.SPACE_TEMP), (r".*SAT.*", Role.SUPPLY_AIR_TEMP)])
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
    sbt = {"OSA": _series([250.0])}                  # OSA data is impossible -> flagged
    rep = review(tokens, _mapping(), sbt, min_confidence=0.5)
    assert rep["n"] == 5
    unmapped = {s.token for s in rep["unmapped"]}
    needs = {s.token for s in rep["needs_review"]}
    assert unmapped == {"MysteryPoint"}
    assert "SAT_Temp" in needs        # ambiguous
    assert "OSA" in needs             # data mismatch
    assert "HHW_Valve" not in needs   # solid alias
