"""Tests for pump power-at-matched-flow drift (camber.rules.pump_power_rule) + its fold-in."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.pumpdrift import diagnose_pump_drift  # noqa: E402
from camber.rules.base import Finding, PeriodRule  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.pump_power_rule import PumpPowerDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_KW_PER_GPM = 0.02  # ~14 kW at 700 gpm
_SIGMA_KW = 0.4


def _flow(n, seed=0):
    rng = np.random.default_rng(seed)
    h = np.arange(n)
    f = 680 + 300 * np.sin((h % 24 - 8) / 24 * 2 * np.pi) + rng.normal(0, 40, n)
    return np.clip(f, 150.0, 1000.0)


def _frame(n=24 * 30, *, start="2025-05-01", seed=0, excess_kw=0.0, inputs=True):
    rng = np.random.default_rng(seed + 100)
    idx = pd.date_range(start, periods=n, freq="1h")
    flow = _flow(n, seed=seed)
    cols = {Role.CHW_FLOW: flow}
    if inputs:
        cols[Role.POWER] = _KW_PER_GPM * flow + excess_kw + rng.normal(0, _SIGMA_KW, n)
    return pd.DataFrame(cols, index=idx)


def _rule(store, **kw):
    return PumpPowerDrift(store, site="SITE", run_id="2025-07-01T00:00", **kw)


def _base_and(current_kw):
    return _frame(start="2025-05-01", seed=1), _frame(start="2025-06-01", seed=2, **current_kw)


# --------------------------------------------------------------------------- the detector


def test_it_is_a_period_rule_and_is_not_auto_registered():
    rule = _rule(BaselineStore())
    assert isinstance(rule, PeriodRule)
    assert "pump_power_drift" not in rule_names()
    assert "pump_power_drift" not in builtin_registry().names()
    assert rule.roles_required == (Role.POWER, Role.CHW_FLOW)


def test_excess_power_flags():
    f = _rule(BaselineStore()).analyze_periods("P_1", *_base_and({"excess_kw": 3.0}))
    assert f.rule == "pump_power_drift" and f.severity == "fault"
    assert f.metrics["pump_power_drift_kw"] > 2.0
    assert f.metrics["pump_power_drift_direction"] == "up"
    assert f.metrics["pump_power_sustained_alarm"] is True
    assert f.metrics["pump_power_alarm_direction"] == "up"


def test_it_is_one_sided_less_power_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("P_1", *_base_and({"excess_kw": -3.0}))
    assert f.severity == "ok" and f.metrics["pump_power_drift_direction"] == "down"


def test_a_steady_pump_does_not_flag():
    f = _rule(BaselineStore()).analyze_periods("P_1", *_base_and({}))
    assert f.severity == "ok" and abs(f.metrics["pump_power_drift_kw"]) < 1.0


def test_it_declines_when_power_or_flow_is_not_mapped():
    base = _frame(start="2025-05-01", seed=1, inputs=False)
    cur = _frame(start="2025-06-01", seed=2, inputs=False)
    f = _rule(BaselineStore()).analyze_periods("P_1", base, cur)
    assert f.severity == "info" and f.metrics["reason"] == "power_or_flow_not_mapped"


# --------------------------------------------------------------------------- the fold-in


def _find(rule, severity, **metrics):
    return Finding(rule=rule, equip="P_1", severity=severity, metrics=metrics)


def test_power_folds_into_the_diagnosis_as_a_pump_side_signal():
    d = diagnose_pump_drift([_find("pump_power_drift", "fault", pump_power_drift_kw=3.0)])
    assert d.locus == "pump"
    assert any("efficiency loss" in c for c in d.causes)


def test_power_corroborates_a_head_deficit_on_the_pump_side():
    d = diagnose_pump_drift(
        [
            _find("pump_head_drift", "fault", pump_head_drift_psi=-8.0),
            _find("pump_power_drift", "warn", pump_power_drift_kw=1.5),
        ]
    )
    assert d.locus == "pump" and d.corroborated is True
    assert d.loop_wide is False  # both signals are mechanical -> still just the pump


def test_a_declined_power_signal_is_ignored():
    declined = _find("pump_power_drift", "info", declined=True, reason="power_or_flow_not_mapped")
    d = diagnose_pump_drift(
        [_find("loop_dp_drift", "fault", loop_dp_drift_direction="up"), declined]
    )
    assert d.locus == "distribution"
    assert any("power_or_flow_not_mapped" in c for c in d.caveats)
