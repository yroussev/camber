"""0.111 (#315): config values that passed the #308 checks by another spelling.

- a rule parameter of the wrong type (``"nan"``, ``"500"``, ``true`` for a number, ``"no"`` for a
  flag, ``7.5`` for an hour) is refused by ``camber run``, the ``camber mcp --config`` startup
  check and ``rule_run``'s ``params``; an integral float runs as the integer;
- a parameter's ``basis`` must be a non-empty text;
- ``report.rcx.references``: a threshold that is not a finite number (or not in its range), a
  sensor used as its own reference, and a unit, reference equipment or file the source does not
  have are refused before anything runs;
- a ``camber mcp`` usage mistake is one line on stderr, and ``-h`` shows ``[--config PATH ...]``.

Every test builds a tiny store in its own tmp directory.
"""

import glob
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import CallContext, CapabilityError, default_registry  # noqa: E402
from camber.config import _preflight_config, _tuned_rule, load_config, run_config  # noqa: E402
from camber.report.rcx import RcxOptions  # noqa: E402
from camber.rules.param_docs import _as_documented, _value_problem  # noqa: E402

FID = fx.FID
REG = default_registry()


@pytest.fixture()
def store(tmp_path):
    return fx.make_store(tmp_path, meta=None)


def _config(rule, params, basis=None):
    entry = {"name": rule, "params": params}
    if basis is not None:
        entry["basis"] = basis
    return {
        "source": {"kind": "store", "store": "store", "facility_id": FID},
        "equipment": [{"class": "AHU"}],
        "rules": [entry],
    }


# --------------------------------------------------------------------------- parameter types

_WRONG_TYPE = [
    ("outdoor_air_fraction", {"min_oa_pct": "nan"}, "'nan' is text, not a number"),
    ("outdoor_air_fraction", {"min_oa_pct": "500"}, "'500' is text, not a number"),
    ("outdoor_air_fraction", {"min_oa_pct": "-50"}, "is text, not a number"),
    ("outdoor_air_fraction", {"min_oa_pct": True}, "True is not a number"),
    ("outdoor_air_fraction", {"min_oa_pct_by_month": [20.0] * 11 + ["9"]}, "is text"),
    ("outdoor_air_fraction", {"fan_gate": "no"}, "'no' is not true or false"),
    ("outdoor_air_fraction", {"fan_gate": 0}, "is not true or false"),
    ("night_weekend_setback", {"start_hour": 7.5}, "7.5 is not a whole number"),
    ("night_weekend_setback", {"start_hour": "7"}, "is text, not a number"),
    ("night_weekend_setback", {"start_hour": True}, "is not a number"),
    ("night_weekend_setback", {"occupied_days": [0, 1, 2.5]}, "2.5 is not a whole number"),
    ("damper_census", {"occupancy_gate": "sometimes"}, "is not one of 'trended'"),
    ("reheat_penalty", {"fan_heat_f": "automatic"}, "is text, not a number \\(or one of 'auto'"),
]


@pytest.mark.parametrize(
    "rule, params, message", _WRONG_TYPE, ids=[f"{r}-{list(p)[0]}" for r, p, _ in _WRONG_TYPE]
)
def test_camber_run_and_the_preflight_refuse_a_wrong_type(store, tmp_path, rule, params, message):
    for check in (_preflight_config, run_config):
        with pytest.raises(ValueError, match=message):
            check(_config(rule, params), base_dir=str(tmp_path))


def test_a_stated_basis_never_excuses_a_wrong_type(store, tmp_path):
    basis = {"min_oa_pct": "the building's own sequence"}
    with pytest.raises(ValueError, match="is text, not a number"):
        _preflight_config(
            _config("outdoor_air_fraction", {"min_oa_pct": "500"}, basis), base_dir="."
        )


def test_values_of_the_right_type_pass():
    ok = [
        ("night_weekend_setback", "start_hour", 7),
        ("night_weekend_setback", "start_hour", 7.0),
        ("night_weekend_setback", "occupied_days", [0, 1, 2, 3, 4, 5]),
        ("outdoor_air_fraction", "min_oa_pct", 20),
        ("outdoor_air_fraction", "min_oa_pct", None),
        ("outdoor_air_fraction", "fan_gate", False),
        ("damper_census", "occupancy_gate", "schedule"),
        ("reheat_penalty", "fan_heat_f", "auto"),
        ("overcooling_severity", "shortfall_share_pct", {"warn": 5, "fault": 20.0}),
    ]
    for rule, name, value in ok:
        assert _value_problem(rule, name, value) is None, (rule, name, value)


def test_an_integral_float_runs_as_the_integer():
    out = _as_documented("night_weekend_setback", {"start_hour": 7.0, "occupied_days": [0.0, 1]})
    assert out == {"start_hour": 7, "occupied_days": [0, 1]}
    assert type(out["start_hour"]) is int and type(out["occupied_days"][0]) is int
    kept = _as_documented("outdoor_air_fraction", {"min_oa_pct": 20.0})
    assert type(kept["min_oa_pct"]) is float  # a number parameter keeps its float
    assert _tuned_rule("night_weekend_setback", {"start_hour": 7.0}) is not None


@pytest.mark.parametrize(
    "params, message",
    [
        ({"night_weekend_setback": {"start_hour": 7.5}}, "not a whole number"),
        ({"outdoor_air_fraction": {"min_oa_pct": "nan"}}, "is text, not a number"),
        ({"outdoor_air_fraction": {"fan_gate": "no"}}, "is not true or false"),
    ],
    ids=["fraction", "text", "flag"],
)
def test_rule_run_refuses_a_wrong_type(store, params, message):
    ctx = CallContext(store=store.root, allowed_facilities=frozenset({FID}))
    with pytest.raises(CapabilityError) as e:
        REG.call("rule_run", {"facility_id": FID, "rules": list(params), "params": params}, ctx)
    assert e.value.code == "invalid_params" and message in e.value.message


# --------------------------------------------------------------------------- basis


@pytest.mark.parametrize("basis", ["", "   ", 1, None, ["x"]], ids=repr)
def test_a_basis_must_be_non_empty_text(store, tmp_path, basis):
    config = _config("outdoor_air_fraction", {"min_oa_pct": 500}, {"min_oa_pct": basis})
    with pytest.raises(ValueError, match="'basis' of 'min_oa_pct' must be a non-empty text"):
        _preflight_config(config, base_dir=str(tmp_path))


def test_a_text_basis_keeps_an_out_of_range_value(store, tmp_path):
    config = _config("outdoor_air_fraction", {"min_oa_pct": 100.5}, {"min_oa_pct": "TAB report"})
    _preflight_config(config, base_dir=str(tmp_path))


# --------------------------------------------------------------------------- references


def _refs(spec, unit="DemoAHU", role="supply_air_temp"):
    return fx.config(references={unit: {role: spec}})


_BAD_REFS = [
    ({"equip": "DemoAHU2", "bias_warn": float("nan")}, "bias_warn must be a finite number"),
    ({"equip": "DemoAHU2", "bias_warn": "1.5"}, "bias_warn must be a finite number"),
    ({"equip": "DemoAHU2", "bias_fault": True}, "bias_fault must be a finite number"),
    ({"equip": "DemoAHU2", "bias_warn": -1}, "bias_warn must be above 0"),
    ({"equip": "DemoAHU2", "drift_warn": 0}, "drift_warn must be above 0"),
    ({"equip": "DemoAHU2", "min_correlation": 2}, "min_correlation must be from -1 to 1"),
    ({"equip": "DemoAHU2", "min_samples": 0.5}, "min_samples must be a whole number"),
    ({"equip": "DemoAHU2", "min_drift_days": -1}, "min_drift_days must be 0 or more"),
    ({"equip": "DemoAHU2", "role": "nope"}, "role 'nope' is not a CAMBER role"),
    ({"equip": 7}, "'equip' must be a non-empty text"),
    ({"equip": "DemoAHU"}, "a sensor cannot be its own reference"),
    ({"equip": "DemoAHU", "role": "supply_air_temp"}, "a sensor cannot be its own reference"),
]


@pytest.mark.parametrize("spec, message", _BAD_REFS, ids=[m for _, m in _BAD_REFS])
def test_a_reference_that_cannot_be_used_is_refused(store, tmp_path, spec, message):
    config = _refs(spec)
    with pytest.raises(ValueError, match=message):
        _preflight_config(config, base_dir=str(tmp_path))
    with pytest.raises(ValueError, match=message):  # camber report reads it the same way
        RcxOptions.from_config(config["report"], base_dir=str(tmp_path))


def test_another_role_of_the_same_unit_and_good_thresholds_pass(store, tmp_path):
    spec = {"equip": "DemoAHU", "role": "mixed_air_temp", "bias_warn": 1.5, "min_samples": 24.0}
    _preflight_config(_refs(spec), base_dir=str(tmp_path))
    _preflight_config(_refs({"equip": "DemoAHU2", "min_correlation": -0.5}), base_dir=str(tmp_path))


@pytest.mark.parametrize(
    "config, message",
    [
        (_refs({"equip": "LOGGER-nope"}), "reference equipment 'LOGGER-nope' is not stored"),
        (_refs({"equip": "DemoAHU2"}, unit="AHU-nope"), "no equipment 'AHU-nope' is stored"),
        (_refs({"csv": "nope.csv"}), "reference file .*nope.csv.* not found"),
    ],
    ids=["reference-equipment", "unit", "csv"],
)
def test_the_preflight_refuses_what_the_source_does_not_have(store, tmp_path, config, message):
    with pytest.raises(ValueError, match=message):
        _preflight_config(config, base_dir=str(tmp_path))


def test_an_existing_reference_file_passes(store, tmp_path):
    (tmp_path / "logger.csv").write_text("t,v\n2026-03-02 00:00,70\n")
    _preflight_config(_refs({"csv": "logger.csv"}), base_dir=str(tmp_path))


# --------------------------------------------------------------------------- packaged templates


def _templates():
    folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), "camber", "datasets")
    return sorted(glob.glob(os.path.join(folder, "configs", "**", "*.json"), recursive=True))


def test_every_packaged_template_and_exercise_passes_the_type_and_reference_checks():
    from camber.config import _rule_entries

    paths = _templates()
    assert any(os.sep + "exercises" + os.sep in p for p in paths)
    for path in paths:
        config = load_config(path)
        name = os.path.relpath(path, os.path.dirname(paths[0]))
        for rule, params, basis in _rule_entries(config):
            for key, value in params.items():
                problem = _value_problem(rule, key, value, check_range=key not in (basis or {}))
                assert problem is None, f"{name}: {problem}"
        report = config.get("report")
        if isinstance(report, dict) and report.get("rcx") is not None:
            RcxOptions.from_config(report, base_dir=os.path.dirname(path))


# --------------------------------------------------------------------------- camber mcp usage


def _main(argv, capsys):
    from camber.cli import main

    with pytest.raises(SystemExit) as e:
        main(argv)
    return e.value.code, capsys.readouterr()


@pytest.mark.parametrize(
    "argv, message",
    [
        (["--store", "s", "--allow", "a", "--max-concurrent", "65"], "must be from 1 to 64"),
        (["--store", "s", "--allow", "a", "--max-queued", "x"], "not a whole number"),
        (["--allow", "a"], "the following arguments are required: --store"),
        (["--store", "s", "--allow", "a", "--bogus"], "unrecognized arguments: --bogus"),
    ],
    ids=["range", "malformed", "missing", "unknown"],
)
def test_a_mcp_usage_mistake_is_one_line(capsys, argv, message):
    code, out = _main(["mcp", *argv], capsys)
    assert code == 2 and out.out == ""
    lines = out.err.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("camber mcp: error: ") and message in lines[0]


def test_mcp_help_shows_the_repeatable_config(capsys):
    code, out = _main(["mcp", "-h"], capsys)
    assert code == 0
    assert "[--config PATH ...]" in out.out and "[--allow FACILITY ...]" in out.out


def test_other_subcommands_keep_the_usage_block(capsys):
    code, out = _main(["run", "--bogus"], capsys)
    assert code == 2 and out.err.startswith("usage: ")
