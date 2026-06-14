"""Tests for the plugin API (camber.plugins) — fake entry points, no package install."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd  # noqa: E402

from camber.plugins import LoadedPlugin, PluginRegistry, apply_rules, discover  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Finding, Registry  # noqa: E402


# --- plugin objects a third-party package might ship -------------------------------------

class MyRule:
    name = "my_custom_rule"
    roles_required = (Role.SUPPLY_AIR_TEMP,)
    roles_optional = ()

    def analyze(self, equip, frame):
        return Finding(rule=self.name, equip=equip, severity="ok", summary="ok")


class MyAdapter:
    def point_names(self): return ["a"]
    def load_points(self, names, resample=None): return pd.DataFrame()
    def units(self): return {}


def my_report(findings):           # a report plugin can simply be a callable
    return "report"


class _NotARule:                   # missing analyze / roles_required
    name = "bad"


class _FakeEP:
    """An entry-point-like object for injection (.name, .load())."""
    def __init__(self, name, obj, *, boom=False):
        self.name, self._obj, self._boom = name, obj, boom

    def load(self):
        if self._boom:
            raise ImportError("could not import plugin")
        return self._obj


# --- discover ----------------------------------------------------------------------------

def test_discover_loads_and_validates():
    eps = [_FakeEP("good", MyRule), _FakeEP("bad", _NotARule), _FakeEP("boom", None, boom=True)]
    loaded = discover("rules", source=eps)
    by = {lp.name: lp for lp in loaded}
    assert by["good"].obj is MyRule and not by["good"].error
    assert by["bad"].obj is None and "valid rules plugin" in by["bad"].error
    assert by["boom"].obj is None and "load failed" in by["boom"].error


def test_discover_unknown_kind_raises():
    try:
        discover("widgets", source=[])
        assert False
    except ValueError:
        pass


def test_validators_per_kind():
    assert discover("adapters", source=[_FakeEP("a", MyAdapter)])[0].obj is MyAdapter
    assert discover("reports", source=[_FakeEP("r", my_report)])[0].obj is my_report
    # an adapter offered as a rule fails rule validation
    assert discover("rules", source=[_FakeEP("x", MyAdapter)])[0].error


# --- registry ----------------------------------------------------------------------------

def test_register_in_process_and_query():
    reg = PluginRegistry()
    reg.register("rules", MyRule)
    reg.register("adapters", MyAdapter, name="my_source")
    reg.register("reports", my_report, name="my_report")
    assert "my_custom_rule" in reg.rules()          # name taken from the rule's .name
    assert "my_source" in reg.adapters() and "my_report" in reg.reports()
    assert reg.get("rules", "my_custom_rule") is MyRule


def test_register_rejects_invalid():
    reg = PluginRegistry()
    try:
        reg.register("rules", _NotARule)
        assert False
    except TypeError:
        pass


def test_load_entrypoints_with_injected_source():
    sources = {"rules": [_FakeEP("good", MyRule), _FakeEP("boom", None, boom=True)],
               "adapters": [_FakeEP("a", MyAdapter)],
               "reports": []}
    reg = PluginRegistry().load_entrypoints(source_for=lambda k: sources[k])
    assert "good" in reg.rules() and "a" in reg.adapters()
    assert len(reg.errors) == 1 and reg.errors[0].name == "boom"   # bad one captured, not raised


def test_apply_rules_into_registry_runs_plugin_rule():
    reg = PluginRegistry()
    reg.register("rules", MyRule)
    rules = apply_rules(reg, Registry())
    assert "my_custom_rule" in rules.names()
    # the plugin rule is a usable, instantiated rule
    out = rules.get("my_custom_rule").analyze("AHU-1", pd.DataFrame())
    assert out.rule == "my_custom_rule" and out.severity == "ok"


def test_apply_rules_accepts_instances_too():
    reg = PluginRegistry()
    reg.register("rules", MyRule(), name="inst_rule")     # an instance, not a class
    rules = apply_rules(reg, Registry())
    assert "my_custom_rule" in rules.names()              # registered under the instance's .name
