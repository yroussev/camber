"""Tests for shared value/status coercion (camber.coerce)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.coerce import coerce_numeric, coerce_status  # noqa: E402


def test_numeric_strips_thousands_separators():
    out = coerce_numeric(pd.Series(["1,234", "5,678", "90"]))
    assert out.tolist() == [1234.0, 5678.0, 90.0]


def test_numeric_normalizes_null_and_quality_tokens():
    out = coerce_numeric(pd.Series(["1.0", "N/A", "---", "Bad", "Comm Fail", "", "2.0"]))
    assert out[0] == 1.0 and out.iloc[-1] == 2.0
    assert out.iloc[1:6].isna().all()  # every null/quality token -> NaN


def test_numeric_european_decimal_comma():
    out = coerce_numeric(pd.Series(["1.234,5", "2.000,0"]), thousands=".", decimal=",")
    assert out.tolist() == [1234.5, 2000.0]


def test_numeric_leaves_clean_numbers_unchanged():
    src = pd.Series([1.5, 2.0, np.nan, 4.25])
    assert coerce_numeric(src).equals(pd.to_numeric(src))


def test_status_classic_and_extended_vocab():
    out = coerce_status(
        pd.Series(["Running", "Off", "Open", "Closed", "Fault", "Normal", "Override", "Auto"])
    )
    assert out.tolist() == [1.0, 0.0, 1.0, 0.0, 1.0, 0.0, 1.0, 0.0]


def test_status_numeric_fallback_and_unknown():
    out = coerce_status(pd.Series(["85.0", "0", "gibberish"]))
    assert out[0] == 1.0 and out[1] == 0.0 and np.isnan(out[2])


def test_status_vocab_overridable():
    out = coerce_status(pd.Series(["cooling", "heating"]), on={"cooling"}, off={"heating"})
    assert out.tolist() == [1.0, 0.0]
