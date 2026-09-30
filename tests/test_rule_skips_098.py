"""0.98 (#86, #88, #85): ``roles_any_of``, the skipped-rule collector, and the equipment class on
role frames. Synthetic only."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.entities import missing_inputs, roles_any_of, runnable_rules  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules import base as base_mod  # noqa: E402
from camber.rules.base import Finding, Registry, _roles_to_load  # noqa: E402


class _Ref:
    def __init__(self, equip, equip_class=""):
        self.equip, self.equip_class = equip, equip_class


def _frame(*roles, n=24):
    idx = pd.date_range("2024-01-01", periods=n, freq="h")
    return pd.DataFrame({r: np.linspace(0.0, 1.0, n) for r in roles}, index=idx)


@pytest.fixture
def frames(monkeypatch):
    """``frames[equip] = DataFrame`` stands in for the store: resolve returns the requested
    roles that the equipment carries."""
    data: dict = {}

    def fake_resolve(ref, mapping, roles, resample="1h"):
        fr = data.get(ref.equip)
        if fr is None:
            return pd.DataFrame()
        return fr[[r for r in roles if r in fr.columns]].copy()

    monkeypatch.setattr(base_mod, "resolve", fake_resolve)
    return data


class _Rule:
    name = "boilerish"
    roles_required = (Role.HW_SUPPLY_TEMP,)
    roles_optional = (Role.OAT,)
    roles_any_of = ((Role.BOILER_STATUS, Role.GAS_INPUT_RATE),)

    def __init__(self):
        self.seen = {}

    def analyze(self, equip, frame):
        self.seen[equip] = frame
        return Finding(rule=self.name, equip=equip, severity="ok")


class _Plain:
    name = "plain"
    roles_required = (Role.HW_SUPPLY_TEMP,)
    roles_optional = ()

    def analyze(self, equip, frame):
        return Finding(rule=self.name, equip=equip, severity="ok")


# --------------------------------------------------------------------------- roles_any_of


def test_any_of_helpers_and_runnable():
    r = _Rule()
    assert roles_any_of(r) == ((Role.BOILER_STATUS, Role.GAS_INPUT_RATE),)
    assert roles_any_of(_Plain()) == ()
    assert missing_inputs(r, {Role.HW_SUPPLY_TEMP, Role.GAS_INPUT_RATE}) == ([], [])
    assert missing_inputs(r, {Role.HW_SUPPLY_TEMP}) == (
        [],
        [(Role.BOILER_STATUS, Role.GAS_INPUT_RATE)],
    )
    a, b, c = runnable_rules(
        {Role.HW_SUPPLY_TEMP}, [r, _Plain(), type("R", (), {"name": "x", "roles_required": ()})()]
    )
    assert not a.can_run and a.missing_any_of == ((Role.BOILER_STATUS, Role.GAS_INPUT_RATE),)
    assert a.missing_required == frozenset()
    assert b.can_run and b.missing_any_of == ()
    assert c.can_run
    (d,) = runnable_rules({Role.HW_SUPPLY_TEMP, Role.BOILER_STATUS}, [r])
    assert d.can_run


def test_roles_to_load_unchanged_without_any_of():
    assert _roles_to_load(_Plain()) == (Role.HW_SUPPLY_TEMP,)
    assert _roles_to_load(_Rule()) == (
        Role.HW_SUPPLY_TEMP,
        Role.OAT,
        Role.BOILER_STATUS,
        Role.GAS_INPUT_RATE,
    )


def test_any_of_gates_the_runner(frames):
    frames["B1"] = _frame(Role.HW_SUPPLY_TEMP, Role.GAS_INPUT_RATE)  # the fallback member only
    frames["B2"] = _frame(Role.HW_SUPPLY_TEMP)  # neither member: skipped
    frames["B3"] = _frame(Role.HW_SUPPLY_TEMP, Role.BOILER_STATUS, Role.GAS_INPUT_RATE)
    reg = Registry()
    rule = reg.register(_Rule())
    out = reg.run(rule.name, [_Ref("B1"), _Ref("B2"), _Ref("B3")], None)
    assert [f.equip for f in out] == ["B1", "B3"]
    assert Role.GAS_INPUT_RATE in rule.seen["B1"].columns
    assert set(rule.seen["B3"].columns) >= {Role.BOILER_STATUS, Role.GAS_INPUT_RATE}


def test_any_of_roles_join_the_trust_gate(frames):
    fr = _frame(Role.HW_SUPPLY_TEMP, Role.GAS_INPUT_RATE, n=200)
    fr[Role.GAS_INPUT_RATE] = 5.0  # a flat-lined input: untrusted
    frames["B1"] = fr
    reg = Registry()
    reg.register(_Rule())
    (f,) = reg.run("boilerish", [_Ref("B1")], None, min_trust=0.99)
    assert f.metrics.get("declined") and "gas_input_rate" in f.metrics["untrusted_roles"]
