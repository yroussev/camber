"""Hardening: every registered rule must survive degenerate frames (pre-1.0 stress pass).

A rule may return an `info`/`ok`/`warn`/`fault` Finding, but must NEVER raise on an empty, one-row,
all-NaN, all-equal, or duplicate-index frame that carries its required roles.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.rules.base import Finding  # noqa: E402
from camber.rules.builtin import builtin_registry, is_fleet  # noqa: E402

_REG = builtin_registry()
_NAMES = _REG.names()
_KINDS = ["empty", "one_row", "all_nan", "all_equal", "dup_index"]
_VALID = {"ok", "info", "warn", "fault"}


def _frame(roles, kind):
    idx = {
        "empty": pd.DatetimeIndex([]),
        "one_row": pd.date_range("2024-01-01", periods=1, freq="1h"),
        "all_nan": pd.date_range("2024-01-01", periods=48, freq="1h"),
        "all_equal": pd.date_range("2024-01-01", periods=48, freq="1h"),
        "dup_index": pd.DatetimeIndex(["2024-01-01"] * 4),
    }[kind]
    n = len(idx)
    if kind == "all_nan":
        vals = lambda: np.full(n, np.nan)  # noqa: E731
    elif kind == "all_equal":
        vals = lambda: np.full(n, 1.0)  # noqa: E731
    else:
        vals = lambda: np.arange(float(n))  # noqa: E731
    return pd.DataFrame({r: vals() for r in roles}, index=idx)


@pytest.mark.parametrize("name", _NAMES)
@pytest.mark.parametrize("kind", _KINDS)
def test_rule_survives_degenerate_frame(name, kind):
    rule = _REG.get(name)
    roles = list(getattr(rule, "roles_required", ())) + list(getattr(rule, "roles_optional", ()))
    frame = _frame(roles, kind)
    if is_fleet(rule):
        f = rule.analyze_fleet({"E1": frame})
    else:
        f = rule.analyze("EQUIP", frame)
    assert isinstance(f, Finding)
    assert f.severity in _VALID


def test_fleet_rules_survive_empty_fleet():
    for name in _NAMES:
        rule = _REG.get(name)
        if is_fleet(rule):
            f = rule.analyze_fleet({})
            assert isinstance(f, Finding) and f.severity in _VALID
