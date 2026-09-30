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
from camber.rules.base import Finding, Registry, RuleSkip, _roles_to_load  # noqa: E402


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


# --------------------------------------------------------------------------- the collector


def test_default_run_records_nothing_and_is_unchanged(frames):
    frames["B1"] = _frame(Role.HW_SUPPLY_TEMP)
    frames["A1"] = _frame(Role.OAT)
    reg = Registry()
    reg.register(_Plain())
    out = reg.run("plain", [_Ref("B1"), _Ref("A1")], None)
    assert [f.equip for f in out] == ["B1"]


def test_collector_records_applicable_skips_only(frames):
    frames["B1"] = _frame(Role.HW_SUPPLY_TEMP)  # has the required role, lacks the any-of group
    frames["A1"] = _frame(Role.OAT)  # none of the rule's inputs: not applicable
    frames["B3"] = _frame(Role.HW_SUPPLY_TEMP, Role.BOILER_STATUS)  # runs
    reg = Registry()
    reg.register(_Rule())
    skipped: list = []
    out = reg.run("boilerish", [_Ref("B1"), _Ref("A1"), _Ref("B3")], None, skipped=skipped)
    assert [f.equip for f in out] == ["B3"]
    assert skipped == [
        RuleSkip("boilerish", "B1", "", ["boiler_status or gas_input_rate"], "missing_inputs")
    ]


def test_collector_uses_the_class_verdict(frames):
    class Chillerish(_Plain):
        name = "chillerish"
        equip_classes = ("Chiller",)
        roles_required = (Role.CHW_SUPPLY_TEMP,)

    frames["CH1"] = _frame(Role.OAT)  # a chiller with no CHWST: applies by class -> skipped
    frames["AHU1"] = _frame(Role.OAT)  # an air handler: not applicable, not skipped
    frames["X1"] = pd.DataFrame(columns=[Role.CHW_SUPPLY_TEMP])  # recognised by role, but no rows
    reg = Registry()
    reg.register(Chillerish())
    skipped: list = []
    reg.run(
        "chillerish",
        [_Ref("CH1", "Chiller"), _Ref("AHU1", "AHU"), _Ref("X1", "Chiller")],
        None,
        skipped=skipped,
    )
    assert [(s.equip, s.equip_class, s.missing, s.reason) for s in skipped] == [
        ("CH1", "Chiller", ["chw_supply_temp"], "missing_inputs"),
        ("X1", "Chiller", [], "no_data"),
    ]


def test_collector_records_no_verdict(frames):
    class Silent(_Plain):
        name = "silent"

        def analyze(self, equip, frame):
            return None

        def analyze_periods(self, equip, b, c):
            return None

    frames["B1"] = _frame(Role.HW_SUPPLY_TEMP)
    reg = Registry()
    reg.register(Silent())
    skipped: list = []
    assert reg.run("silent", [_Ref("B1", "Boiler")], None, skipped=skipped) == []
    assert (
        reg.run_periods(
            "silent",
            [_Ref("B1")],
            None,
            baseline=(None, None),
            current=(None, None),
            skipped=skipped,
        )
        == []
    )
    assert [(s.equip, s.reason) for s in skipped] == [("B1", "no_verdict"), ("B1", "no_verdict")]


def test_run_periods_collects_missing_inputs(frames):
    class Drifty(_Rule):
        name = "drifty"

        def analyze_periods(self, equip, b, c):
            return Finding(rule=self.name, equip=equip, severity="ok")

    frames["B1"] = _frame(Role.HW_SUPPLY_TEMP)
    reg = Registry()
    reg.register(Drifty())
    skipped: list = []
    out = reg.run_periods(
        "drifty", [_Ref("B1")], None, baseline=(None, None), current=(None, None), skipped=skipped
    )
    assert out == [] and [s.missing for s in skipped] == [["boiler_status or gas_input_rate"]]


def test_run_fleet_collects(frames):
    class Fleet:
        name = "fleetish"
        roles_required = (Role.HW_SUPPLY_TEMP,)
        roles_optional = ()
        roles_any_of = ((Role.BOILER_STATUS, Role.GAS_INPUT_RATE),)

        def analyze_fleet(self, frames, topology=None):
            if not frames:
                return None
            return Finding(rule=self.name, equip="fleet", severity="ok", metrics={"n": len(frames)})

    frames["B1"] = _frame(Role.HW_SUPPLY_TEMP, Role.GAS_INPUT_RATE)
    frames["B2"] = _frame(Role.HW_SUPPLY_TEMP)
    reg = Registry()
    reg.register(Fleet())
    skipped: list = []
    f = reg.run_fleet("fleetish", [_Ref("B1"), _Ref("B2")], None, skipped=skipped)
    assert f.metrics["n"] == 1
    assert [(s.equip, s.reason) for s in skipped] == [("B2", "missing_inputs")]
    skipped.clear()
    del frames["B1"]
    assert reg.run_fleet("fleetish", [_Ref("B2")], None, skipped=skipped) is None
    assert [(s.equip, s.reason) for s in skipped] == [("B2", "missing_inputs"), ("", "no_verdict")]
    assert reg.run_fleet("fleetish", [_Ref("B2")], None) is None  # default: nothing recorded


def test_skip_as_dict():
    assert RuleSkip("r", "e").as_dict() == {
        "rule": "r",
        "equip": "e",
        "equip_class": "",
        "missing": [],
        "reason": "missing_inputs",
    }


# --------------------------------------------------------------------------- run_config + RCx


def test_run_config_stores_skips_and_the_report_lists_them(tmp_path):
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import _rcx_fixture as fx

    from camber.config import run_config
    from camber.report import build_rcx_report

    fx.make_store(tmp_path)
    plain = run_config(fx.config(), base_dir=str(tmp_path))
    assert plain.rules_skipped == []
    html0 = build_rcx_report(plain).to_html()
    assert "Checks not evaluated" not in html0

    cfg = fx.config()
    cfg["rules"] = list(cfg["rules"]) + ["chw_plant_reset", "static_pressure_reset"]
    res = run_config(cfg, base_dir=str(tmp_path))
    got = [(s.rule, s.equip, s.missing, s.reason) for s in res.rules_skipped]
    assert ("chw_plant_reset", "", ["chw_supply_temp"], "missing_inputs") in got  # rule level
    assert ("static_pressure_reset", "DemoAHU", ["duct_static_sp"], "missing_inputs") in got
    # the findings themselves are exactly the default run's plus the new rule's (none here)
    assert [(f.rule, f.equip) for f in res.findings] == [(f.rule, f.equip) for f in plain.findings]
    html = build_rcx_report(res).to_html()
    assert "Checks not evaluated (missing inputs)" in html
    assert "<td>Checks not evaluated</td><td>2 (missing inputs; see Appendix A)</td>" in html
    assert "<td>static_pressure_reset</td><td>duct_static_sp</td><td>DemoAHU, DemoAHU2</td>" in html


def test_skip_rows_group_and_cap():
    from types import SimpleNamespace

    from camber.report.rcx import _skip_rows

    sk = [RuleSkip("r", f"E{i}", "VAV", ["a", "b or c"]) for i in range(8)]
    sk += [RuleSkip("r", "E0", "VAV", ["a"]), RuleSkip("q", "Z", "", [], "no_data")]
    sk += [RuleSkip("n", "Z", "", [], "no_verdict")]
    rows = _skip_rows(SimpleNamespace(rules_skipped=sk))
    assert rows == [
        ["r", "a, b or c", "E0, E1, E2, E3, E4, E5 and 2 more"],
        ["r", "a", "E0"],
        ["q", "no data", "Z"],
    ]
    assert _skip_rows(SimpleNamespace()) == []
