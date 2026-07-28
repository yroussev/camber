"""Tests for time-grid handling: interval width, de-duplication, and DST (camber.timegrid)
plus the ingest/quality wiring."""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import io as cio  # noqa: E402
from camber.ingest.quality import assess  # noqa: E402
from camber.timegrid import dst_anomalies, interval_hours, localize, regularize  # noqa: E402

_TZ = "America/Los_Angeles"


def _fallback_index():
    # US fall-back 2025-11-02: local 01:00 occurs twice
    return pd.DatetimeIndex(
        ["2025-11-02 00:00", "2025-11-02 01:00", "2025-11-02 01:00", "2025-11-02 02:00"]
    )


def test_interval_hours_regular_and_duplicate():
    assert interval_hours(pd.date_range("2025-06-01", periods=24, freq="1h")) == 1.0
    assert interval_hours(_fallback_index()) == 1.0  # 0-gap duplicate ignored


def test_regularize_dedupes_and_sorts():
    s = pd.Series([1, 2, 3, 4], index=_fallback_index())
    first = regularize(s)
    assert list(first.values) == [1, 2, 4] and first.index.is_unique
    assert list(regularize(s, dedupe="last").values) == [1, 3, 4]
    assert list(regularize(s, dedupe="mean").values) == [1.0, 2.5, 4.0]
    assert len(regularize(s, dedupe=None)) == 4  # duplicates kept
    # sorting: a shuffled index comes back monotonic
    sh = pd.Series([1, 2, 3], index=pd.DatetimeIndex(["2025-01-03", "2025-01-01", "2025-01-02"]))
    assert regularize(sh).index.is_monotonic_increasing


def test_regularize_bad_dedupe_raises():
    with pytest.raises(ValueError):
        regularize(pd.Series([1], index=pd.DatetimeIndex(["2025-01-01"])), dedupe="bogus")


def test_localize_resolves_dst_fallback_and_springforward():
    loc = localize(_fallback_index(), _TZ)
    offsets = [t.utcoffset() for t in loc]
    assert offsets[1] != offsets[2]  # repeated 01:00 -> PDT then PST
    # spring-forward 02:00 is nonexistent -> shifted forward, not dropped/raised
    sf = pd.DatetimeIndex(["2025-03-09 01:30", "2025-03-09 02:30", "2025-03-09 03:30"])
    assert len(localize(sf, _TZ)) == 3


def test_dst_anomalies_splits_fallback_and_nonexistent():
    fb = dst_anomalies(_fallback_index(), _TZ)
    assert fb["duplicate_timestamps"] == 1 and fb["fallback_ambiguous"] == 1
    assert fb["springforward_nonexistent"] == 0
    sf = pd.date_range("2025-03-09 00:00", "2025-03-09 05:00", freq="1h")  # contains 02:00
    assert dst_anomalies(sf, _TZ)["springforward_nonexistent"] == 1
    clean = pd.date_range("2025-06-01", periods=48, freq="1h")
    assert dst_anomalies(clean, _TZ) == {
        "duplicate_timestamps": 0,
        "fallback_ambiguous": 0,
        "springforward_nonexistent": 0,
    }
    assert dst_anomalies(clean) == {"duplicate_timestamps": 0}  # no tz -> duplicates only


def test_load_csv_dedupes_duplicate_timestamps(tmp_path):
    p = tmp_path / "dup.csv"
    p.write_text("ts,v\n2025-11-02 01:00,10\n2025-11-02 01:00,20\n2025-11-02 02:00,30\n")
    df = cio.load_csv(str(p))  # dedupe="first" by default
    assert df.index.is_unique and list(df["v"]) == [10, 30]
    kept = cio.load_csv(str(p), dedupe=None)
    assert not kept.index.is_unique and len(kept) == 3


def test_quality_report_surfaces_duplicate_timestamps():
    idx = _fallback_index()
    rep = assess(pd.Series([1.0, 2.0, 3.0, 4.0], index=idx))
    assert rep.n_duplicate_ts == 1 and "n_duplicate_ts" in rep.as_dict()
    clean = assess(pd.Series(range(24), index=pd.date_range("2025-06-01", periods=24, freq="1h")))
    assert clean.n_duplicate_ts == 0
