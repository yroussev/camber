"""Tests for the data-quality layer (ingest.quality)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ingest.quality import (  # noqa: E402
    assess, clean, gap_count, infer_freq, longest_flatline, outlier_mask,
)


def _series(values, start="2024-01-01", freq="1h"):
    idx = pd.date_range(start, periods=len(values), freq=freq)
    return pd.Series(values, index=idx, dtype="float64")


# --- primitives ------------------------------------------------------------ #

def test_infer_freq_modal_interval():
    s = _series(range(10), freq="15min")
    assert infer_freq(s.index) == pd.Timedelta("15min")


def test_outlier_mask_flags_spike_not_clean_points():
    vals = [10.0] * 20 + [10000.0]      # one gross spike
    s = _series(vals)
    m = outlier_mask(s)
    assert m.sum() == 1
    assert m.iloc[-1]


def test_outlier_mask_constant_series_has_none():
    s = _series([5.0] * 30)
    assert outlier_mask(s).sum() == 0   # MAD=0 -> no false positives


def test_longest_flatline_counts_run():
    s = _series([1, 2, 2, 2, 2, 3, 4])
    assert longest_flatline(s) == 4


def test_gap_count_detects_time_holes():
    idx = list(pd.date_range("2024-01-01", periods=5, freq="1h"))
    idx += [pd.Timestamp("2024-01-01 10:00")]   # 6h jump after the 5th sample
    s = pd.Series(range(6), index=pd.DatetimeIndex(idx), dtype="float64")
    assert gap_count(s.index, pd.Timedelta("1h")) == 1


# --- assess ---------------------------------------------------------------- #

def test_assess_clean_series_scores_high():
    s = _series(np.sin(np.linspace(0, 6, 200)) * 10 + 50)
    r = assess(s)
    assert r.coverage == 1.0
    assert r.n_outliers == 0
    assert r.score > 0.95


def test_assess_counts_missing_and_lowers_coverage():
    vals = list(range(10))
    s = _series(vals).astype("float64")
    s.iloc[2] = np.nan
    s.iloc[5] = np.nan
    r = assess(s)
    assert r.n_missing == 2
    assert r.n == 8
    assert abs(r.coverage - 0.8) < 1e-9
    assert r.score < 1.0


def test_assess_outliers_penalize_score():
    # a varying (non-flatlined) baseline so the outlier penalty is what differs
    clean_s = _series(np.sin(np.linspace(0, 6, 50)) * 5 + 50)
    dirty = clean_s.copy()
    dirty.iloc[10] = 99999.0
    dirty.iloc[20] = -99999.0
    r = assess(dirty)
    assert r.n_outliers == 2
    assert r.score < assess(clean_s).score


def test_assess_as_dict_serializable():
    r = assess(_series(range(20)))
    d = r.as_dict()
    assert isinstance(d["expected_freq"], str)
    assert "score" in d


# --- clean (with audit trail) ---------------------------------------------- #

def test_clean_drop_outliers_logs_action():
    s = _series([10.0] * 20)
    s.iloc[5] = 88888.0
    cleaned, log = clean(s, drop_outliers=True)
    assert np.isnan(cleaned.iloc[5])
    assert log.steps[0]["op"] == "drop_outliers"
    assert log.steps[0]["n_affected"] == 1
    assert log.total_changed == 1


def test_clean_fill_limit_respected():
    vals = [1.0, np.nan, np.nan, np.nan, 5.0]
    s = _series(vals)
    cleaned, log = clean(s, fill_limit=1)     # only 1 consecutive NaN filled
    assert not np.isnan(cleaned.iloc[1])      # first NaN filled
    assert np.isnan(cleaned.iloc[2])          # second NaN left as honest hole
    assert log.steps[0]["op"] == "ffill"
    assert log.steps[0]["n_affected"] == 1


def test_clean_no_ops_empty_log():
    s = _series([10.0] * 10)
    cleaned, log = clean(s)                    # no flags enabled
    assert log.steps == []
    assert cleaned.equals(s)


def test_clean_outliers_then_fill_order():
    s = _series([10.0, 10.0, 99999.0, 10.0, 10.0])
    cleaned, log = clean(s, drop_outliers=True, fill_limit=1)
    # outlier became NaN then was forward-filled from the prior value
    assert cleaned.iloc[2] == 10.0
    assert [st["op"] for st in log.steps] == ["drop_outliers", "ffill"]
