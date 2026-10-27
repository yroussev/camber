"""0.111 (#308): arguments and rule parameters that looked valid but broke or skewed a call.

- an integral float (``5.0``) for an integer argument runs as that integer in every capability;
  it was a slice index and failed as an internal error in the paged tools;
- a rule parameter that is not finite (NaN quietly turned every fault into "ok"), or one outside
  its documented range without a stated ``basis``, is refused by ``camber run`` and by the
  config check ``camber mcp`` makes at startup;
- a duplicate key in a JSON (or YAML) run config is refused instead of silently taking the last
  value;
- the startup check reads ``report.rcx`` the way ``camber report`` does, and names a ventilation
  zones file that cannot be read.

Every test builds a tiny store in its own tmp directory.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import CallContext, CapabilityError, default_registry  # noqa: E402
from camber.capabilities._schema import as_integers  # noqa: E402
from camber.config import _preflight_config, load_config, run_config  # noqa: E402
from camber.rules.param_docs import _value_problem  # noqa: E402

FID = fx.FID
REG = default_registry()


@pytest.fixture()
def store(tmp_path):
    return fx.make_store(tmp_path, meta=None)


def _ctx(st):
    return CallContext(store=st.root, allowed_facilities=frozenset({FID}))


# --------------------------------------------------------------------------- integral floats

_PAGED = [
    ("facilities", {}),
    ("points", {"facility_id": FID}),
    ("coverage", {"facility_id": FID}),
    ("sensor_trust", {"facility_id": FID}),
    ("rule_run", {"facility_id": FID, "rules": ["outdoor_air_fraction"]}),
    ("rcx_report", {"facility_id": FID}),
    ("rule_params", {}),
    (
        "check_citations",
        {"text": "It faulted [F1@x-abc123].", "facts": [{"id": "F1@x-abc123", "text": "y"}]},
    ),
]


@pytest.mark.parametrize("name, args", _PAGED, ids=[n for n, _ in _PAGED])
@pytest.mark.parametrize("key, value", [("limit", 5.0), ("offset", 1.0)])
def test_an_integral_float_runs_as_the_integer(store, name, args, key, value):
    ctx = _ctx(store)
    as_float = REG.call(name, {**args, key: value}, ctx)
    as_int = REG.call(name, {**args, key: int(value)}, ctx)
    assert as_float.data == as_int.data and as_float.text == as_int.text


def test_a_fractional_number_is_still_refused(store):
    with pytest.raises(CapabilityError) as e:
        REG.call("points", {"facility_id": FID, "limit": 5.5}, _ctx(store))
    assert e.value.code == "invalid_params" and "expected integer" in e.value.message


def test_as_integers_follows_the_schema_and_copies():
    schema = {
        "type": "object",
        "properties": {
            "limit": {"type": "integer"},
            "share": {"type": "number"},
            "either": {"type": ["integer", "null"]},
            "hours": {"type": "array", "items": {"type": "integer"}},
            "params": {"type": "object", "additionalProperties": {"type": "integer"}},
        },
    }
    args = {"limit": 5.0, "share": 2.0, "either": 3.0, "hours": [1.0, 2], "params": {"a": 4.0}}
    out = as_integers(args, schema)
    assert out == {"limit": 5, "share": 2.0, "either": 3, "hours": [1, 2], "params": {"a": 4}}
    assert type(out["limit"]) is int and type(out["share"]) is float
    assert type(out["hours"][0]) is int and type(out["params"]["a"]) is int
    assert args["limit"] == 5.0 and type(args["limit"]) is float  # the input is not modified


# --------------------------------------------------------------------------- rule parameters


def _config(params, basis=None):
    entry = {"name": "outdoor_air_fraction", "params": params}
    if basis:
        entry["basis"] = basis
    return {
        "source": {"kind": "store", "store": "store", "facility_id": FID},
        "equipment": [{"class": "AHU"}],
        "rules": [entry],
    }


_BAD = [
    ({"min_oa_pct": float("nan")}, "not a finite number"),
    ({"min_oa_pct": float("inf")}, "not a finite number"),
    ({"min_oa_pct": 500.0}, "outside its range 0.0 to 100.0"),
    ({"min_oa_pct": -1}, "outside its range"),
    ({"min_oa_pct_by_month": [20.0] * 11 + [float("nan")]}, "not a finite number"),
]


@pytest.mark.parametrize("params, message", _BAD, ids=["nan", "inf", "500", "-1", "month-nan"])
def test_camber_run_and_the_preflight_refuse_a_bad_parameter(store, tmp_path, params, message):
    base = str(tmp_path)
    for check in (_preflight_config, run_config):
        with pytest.raises(ValueError, match=message):
            check(_config(params), base_dir=base)


def test_a_stated_basis_keeps_a_value_outside_the_range_but_never_nan(store, tmp_path):
    base = str(tmp_path)
    basis = {"min_oa_pct": "the building's own sequence"}
    _preflight_config(_config({"min_oa_pct": 100.5}, basis), base_dir=base)
    with pytest.raises(ValueError, match="not a finite number"):
        _preflight_config(_config({"min_oa_pct": float("nan")}, basis), base_dir=base)


def test_a_value_in_range_and_keywords_pass():
    assert _value_problem("outdoor_air_fraction", "min_oa_pct", 20.0) is None
    assert _value_problem("outdoor_air_fraction", "min_oa_pct", None) is None
    assert _value_problem("reheat_penalty", "fan_heat_f", "auto") is None
    assert _value_problem("no_such_rule", "x", 1e9) is None
    assert "outside" in _value_problem("reheat_penalty", "fan_heat_f", 99.0)


def test_rule_run_refuses_an_out_of_range_parameter(store):
    with pytest.raises(CapabilityError) as e:
        REG.call(
            "rule_run",
            {
                "facility_id": FID,
                "rules": ["outdoor_air_fraction"],
                "params": {"outdoor_air_fraction": {"min_oa_pct": 500}},
            },
            _ctx(store),
        )
    assert e.value.code == "invalid_params" and "outside its range" in e.value.message


def test_every_packaged_template_passes_the_parameter_check():
    from camber.config import _rule_entries

    folder = os.path.join(os.path.dirname(os.path.dirname(__file__)), "camber", "datasets")
    folder = os.path.join(folder, "configs")
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".json"):
            continue
        config = load_config(os.path.join(folder, name))
        for rule, params, basis in _rule_entries(config):
            for key, value in params.items():
                problem = _value_problem(rule, key, value, check_range=key not in (basis or {}))
                assert problem is None, f"{name}: {problem}"


# --------------------------------------------------------------------------- config files


def test_a_duplicate_json_key_is_refused(tmp_path):
    path = tmp_path / "dup.json"
    path.write_text(
        '{"source": {"kind": "store", "store": "store", "facility_id": "a", '
        '"facility_id": "b"}, "rules": []}'
    )
    with pytest.raises(ValueError, match="duplicate key 'facility_id'"):
        load_config(str(path))


def test_a_duplicate_yaml_key_is_refused_but_a_merge_key_is_not(tmp_path):
    pytest.importorskip("yaml")
    dup = tmp_path / "dup.yaml"
    dup.write_text("source:\n  facility_id: a\n  facility_id: b\n")
    with pytest.raises(ValueError, match="duplicate key 'facility_id'"):
        load_config(str(dup))
    merged = tmp_path / "merge.yaml"
    merged.write_text("base: &b {x: 1}\nsource:\n  <<: *b\n  x: 2\n")
    assert load_config(str(merged))["source"] == {"x": 2}


def test_the_preflight_checks_rcx_references(store, tmp_path):
    config = fx.config(references={"DemoAHU": {"outdoor_air_temp": {"equip": "DemoAHU2"}}})
    with pytest.raises(ValueError, match="'outdoor_air_temp' is not a CAMBER role"):
        _preflight_config(config, base_dir=str(tmp_path))
    _preflight_config(fx.config(), base_dir=str(tmp_path))


def test_the_preflight_names_a_missing_ventilation_zones_file(store, tmp_path):
    config = {**_config({}), "rules": ["outdoor_air_fraction"]}
    config["ventilation"] = {"zones": "zones-missing.csv"}
    with pytest.raises(ValueError, match="ventilation.zones: cannot read .*zones-missing.csv"):
        _preflight_config(config, base_dir=str(tmp_path))


def test_config_file_round_trip_keeps_json_nan_refusable(store, tmp_path):
    path = tmp_path / "nan.json"
    path.write_text(json.dumps(_config({"min_oa_pct": float("nan")})))  # Python writes NaN
    with pytest.raises(ValueError, match="not a finite number"):
        _preflight_config(load_config(str(path)), base_dir=str(tmp_path))
