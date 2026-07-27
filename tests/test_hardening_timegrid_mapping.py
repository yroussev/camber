"""Hardening: timegrid + mapping on adversarial inputs (pre-1.0 stress pass).

timegrid was already robust — these lock it. Mapping had a real ReDoS hole (a catastrophic-backtracking
pattern from config could hang the mapper): now rejected at load time.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.timegrid import regularize, localize, dst_anomalies, interval_hours  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.mapping_confidence import score_token, review  # noqa: E402

_EMPTY = pd.DatetimeIndex([])
_DUP = pd.DatetimeIndex(["2024-01-01"] * 5)


# --- timegrid (robustness locks) --------------------------------------------

def test_timegrid_survives_empty_and_duplicate_index():
    assert interval_hours(_EMPTY) == interval_hours(_EMPTY)          # no raise
    assert regularize(pd.Series([], index=_EMPTY, dtype=float)).empty
    r = regularize(pd.Series(np.arange(5.0), index=_DUP))
    assert r.index.is_unique and len(r) == 1
    assert dst_anomalies(_EMPTY, "America/Los_Angeles")["duplicate_timestamps"] == 0
    assert len(localize(_EMPTY, "America/Los_Angeles")) == 0


def test_regularize_mean_on_nonnumeric_does_not_raise():
    df = pd.DataFrame({"s": ["a", "b", "a"]}, index=_DUP[:3])
    out = regularize(df, dedupe="mean")                             # per-column agg, no crash
    assert out.index.is_unique


# --- mapping: ReDoS guard ----------------------------------------------------

@pytest.mark.parametrize("bad", ["(a+)+$", "(x*)+", "(ab+)*", "(.+)+"])
def test_catastrophic_regex_rejected_at_load(bad):
    with pytest.raises(ValueError, match="catastrophic"):
        MappingProvider.from_dict({"aliases": {}, "patterns": [[bad, "oat"]]})


@pytest.mark.parametrize("good", [r".*_sat$", r"(ahu|vav)\d+_temp", r"chw_.*", r"[A-Z]+\d+"])
def test_legitimate_patterns_still_compile(good):
    mp = MappingProvider.from_dict({"aliases": {}, "patterns": [[good, "oat"]]})
    assert mp is not None                                           # no false positive


# --- mapping: adversarial tokens --------------------------------------------

def test_mapping_handles_none_empty_unicode_tokens():
    mp = MappingProvider.from_dict({"aliases": {"OAT": "oat"}, "patterns": []})
    assert mp.role_of(None) is None
    assert mp.role_of("") is None
    assert mp.role_of("温度传感器") is None
    assert mp.role_of("OAT") is not None                           # normal path unaffected


def test_mapping_confidence_survives_huge_and_odd_token_sets():
    mp = MappingProvider.from_dict({"aliases": {"OAT": "oat"}, "patterns": []})
    assert score_token(None, mp).role is None
    rev = review([f"tok{i}" for i in range(5000)] + ["OAT", ""], mp)
    assert rev["n"] == 5002                                         # completes, no crash
