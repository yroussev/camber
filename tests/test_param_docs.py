"""0.98 (#90): every tunable rule parameter is documented (camber.rules.param_docs)."""

import inspect
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cli import main  # noqa: E402
from camber.rules import param_docs as pd  # noqa: E402
from camber.rules.builtin import make_rule, rule_factories, rule_names  # noqa: E402

RULES = pd.documented_rules()


def _all_ctor_params(rule):
    cls, fixed = rule_factories()[rule]
    return {p.name: p for p in pd._signature_params(cls) if p.name not in fixed}


def test_every_registered_rule_is_covered():
    assert RULES == sorted(rule_names())  # the extra instances included
    for rule in RULES:
        params = pd.rule_params(rule)
        assert params or rule in pd.FIXED or rule in pd.DELEGATES, (
            f"{rule}: no tunables -- say what is fixed in code in FIXED"
        )


@pytest.mark.parametrize("rule", RULES)
def test_no_undocumented_param(rule):
    """Fails on any constructor parameter with no entry and no EXEMPT reason."""
    missing = [rp.name for rp in pd.rule_params(rule) if rp.doc is None]
    assert not missing, f"{rule}: document {missing} in camber/rules/param_docs.py"


@pytest.mark.parametrize("rule", RULES)
def test_no_stale_entries(rule):
    real = _all_ctor_params(rule)
    for name in pd.PARAM_DOCS.get(rule, {}):
        assert name in real, f"{rule}.{name}: documented but not a constructor parameter"
        assert name not in pd.EXEMPT.get(rule, {}), f"{rule}.{name}: documented AND exempt"
    for name, why in pd.EXEMPT.get(rule, {}).items():
        assert name in real, f"{rule}.{name}: exempt but not a constructor parameter"
        assert why.strip(), f"{rule}.{name}: an exemption needs a reason"


def test_registry_names_only_registered_rules():
    for table in (pd.PARAM_DOCS, pd.EXEMPT, pd.FIXED, pd.DELEGATES):
        assert set(table) <= set(RULES), set(table) - set(RULES)
    assert set(pd.DELEGATES.values()) <= set(RULES)


@pytest.mark.parametrize("rule", RULES)
def test_entries_are_well_formed(rule):
    for rp in pd.rule_params(rule):
        d, where = rp.doc, f"{rule}.{rp.name}"
        assert d.basis.startswith(pd.BASIS_KINDS), f"{where}: basis {d.basis!r}"
        assert d.unit.strip() and d.calibrate.strip(), where
        assert isinstance(d.range, tuple) and len(d.range) >= 2, where
        default = rp.default
        if default is None:
            continue
        if isinstance(default, bool) or isinstance(default, str):
            assert d.is_choice and default in d.range, f"{where}: default {default!r} not listed"
        elif isinstance(default, (int, float)) and not d.is_choice:
            lo, hi = d.range[0], d.range[-1]
            assert lo <= default <= hi, f"{where}: default {default} outside {d.range}"
        elif isinstance(default, dict):  # a tier map: the range is per value
            assert not d.is_choice and "per key" in d.unit, f"{where}: name the keys in the unit"
            lo, hi = d.range[0], d.range[-1]
            for k, v in default.items():
                assert lo <= v <= hi, f"{where}[{k!r}]: default {v} outside {d.range}"


def test_defaults_are_read_from_the_constructors():
    """The registry holds no defaults: rule_params reports the signature's."""
    sig = inspect.signature(make_rule("leaking_valve").__class__.__init__)
    rp = {p.name: p for p in pd.rule_params("leaking_valve")}
    assert rp["fan_heat_f"].default == sig.parameters["fan_heat_f"].default
    assert not any(hasattr(d, "default") for docs in pd.PARAM_DOCS.values() for d in docs.values())


@pytest.mark.parametrize("rule", RULES)
def test_config_snippet_builds_every_rule(rule):
    """The pasted snippet is a valid config entry: its params construct the rule."""
    (entry,) = pd.config_snippet([rule])["rules"]
    params = entry["params"] if isinstance(entry, dict) else {}
    assert make_rule(rule, **params).name == rule


def test_extra_instances_are_tunable_but_keep_their_identity():
    r = make_rule("sat_reset_effectiveness", min_cycles=5)
    assert r.name == "sat_reset_effectiveness" and r._kwargs["min_cycles"] == 5
    assert make_rule("cohort_airflow", k=2.5).name == "cohort_airflow"
    with pytest.raises(TypeError, match="fixed for this rule"):
        make_rule("sat_reset_effectiveness", reset="static")
    with pytest.raises(TypeError, match="invalid params"):
        make_rule("cohort_airflow", nope=1)


def test_delegated_params_reach_the_delegate():
    names = {rp.name: rp for rp in pd.rule_params("co2_ventilation_system")}
    assert names["econ_high_limit_f"].delegated_from == "co2_ventilation"
    make_rule("co2_ventilation_system", econ_high_limit_f=70.0)  # accepted, passed through


# --------------------------------------------------------------------------- CLI


def test_cli_rules_params_text(capsys):
    assert main(["rules", "params", "leaking_valve"]) == 0
    out = capsys.readouterr().out
    assert "fan_heat_f = 2.0" in out and "basis:" in out and "calibrate:" in out
    snippet = json.loads(out[out.index("{") :])
    assert snippet["rules"][0]["name"] == "leaking_valve"


def test_cli_rules_params_json(capsys):
    assert main(["rules", "params", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert [r["rule"] for r in doc["rules"]] == RULES
    lv = next(r for r in doc["rules"] if r["rule"] == "leaking_valve")
    p = next(p for p in lv["params"] if p["name"] == "fan_heat_f")
    assert set(p) >= {"default", "unit", "basis", "calibrate", "range"}


def test_cli_rules_params_yaml_is_the_snippet(capsys):
    pytest.importorskip("yaml")
    from camber._yaml import loads_yaml

    assert main(["rules", "params", "--yaml"]) == 0
    out = capsys.readouterr().out
    assert "# °F" in out  # the notes are comments
    assert loads_yaml(out) == pd.config_snippet(RULES)


def test_cli_rules_params_unknown_rule(capsys):
    assert main(["rules", "params", "no_such_rule"]) == 2
    assert "unknown rule" in capsys.readouterr().err


# --------------------------------------------------------------------------- THRESHOLDS.md


def test_thresholds_doc_is_current():
    """docs/THRESHOLDS.md is generated: run `python scripts/thresholds_doc.py` after a change."""
    import importlib.util

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    spec = importlib.util.spec_from_file_location(
        "thresholds_doc", os.path.join(root, "scripts", "thresholds_doc.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.main(["--check"]) == 0, "run: python scripts/thresholds_doc.py"


def test_wave1_sibling_params_are_documented():
    """0.98 wave 1: the air-gates and terminal-ventilation parameters moved into PARAM_DOCS."""
    for rule, name in (
        ("supply_air_control", "occupancy_gate"),
        ("damper_census", "occupancy_gate"),
        ("overcooling_severity", "shortfall_share_pct"),
        ("overcooling_severity", "share_pct"),
    ):
        assert name in pd.PARAM_DOCS[rule] and name not in pd._PENDING.get(rule, {})
    ss = {rp.name: rp for rp in pd.rule_params("overcooling_severity")}["shortfall_share_pct"]
    assert pd._fmt_range(ss.doc.range, ss.default, ss.doc.unit) == "each value: 0 to 100"
    sp = {rp.name: rp for rp in pd.rule_params("overcooling_severity")}["share_pct"]
    assert pd._fmt_range(sp.doc.range, sp.default, sp.doc.unit) == "each value: 0 to 100"


def test_pending_sibling_entries_are_well_formed():
    """Entries waiting for a sibling 0.98 branch: well formed, and never for a documented param."""
    for rule, docs in pd._PENDING.items():
        assert rule in RULES
        for name, d in docs.items():
            assert name not in pd.PARAM_DOCS.get(rule, {}), f"{rule}.{name}: move it, don't copy"
            assert d.basis.startswith(pd.BASIS_KINDS) and len(d.range) >= 2, f"{rule}.{name}"
