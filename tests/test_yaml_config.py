"""0.98 (#90): YAML run configs are an optional, equivalent alternative to JSON."""

import glob
import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber._yaml import dump_yaml, loads_yaml, read_config_file  # noqa: E402
from camber.cli import main  # noqa: E402
from camber.config import load_config, run_config, run_config_file  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _write_point(folder, equip, measure, series):
    ts = series.index.strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
    pd.DataFrame({"Timestamp": ts, "Value": series.values}).to_csv(
        os.path.join(folder, f"{equip}_{measure}.csv"), index=False
    )


def _site(tmp_path):
    folder = tmp_path / "trends"
    folder.mkdir()
    idx = pd.date_range("2025-07-07", periods=24 * 14, freq="1h")
    midday = (idx.dayofweek < 5) & (idx.hour >= 11) & (idx.hour < 15)
    _write_point(folder, "AHU_1", "CHW_Valve", pd.Series(60.0, index=idx))
    _write_point(folder, "AHU_1", "HHW_Valve", pd.Series(np.where(midday, 40.0, 0.0), index=idx))
    _write_point(folder, "AHU_1", "MixedAir", pd.Series(72.0, index=idx))
    _write_point(folder, "AHU_1", "SupplyAir", pd.Series(55.0, index=idx))
    _write_point(folder, "AHU_1", "OSA", pd.Series(88.0, index=idx))
    return {
        "site": "TestHQ",
        "source": {"kind": "perpoint_csv", "folder": "trends"},
        "mapping": {
            "aliases": {
                "CHW_Valve": "cool_valve",
                "HHW_Valve": "heat_valve",
                "MixedAir": "mixed_air_temp",
                "SupplyAir": "supply_air_temp",
                "OSA": "oat",
            }
        },
        "equipment": [{"class": "AHU", "marker": "CHW_Valve"}],
        "rules": [
            {"name": "simultaneous_heat_cool", "params": {"warn_pct": 0.5, "fault_pct": 4.0}},
            {"name": "leaking_valve", "params": {"fan_heat_f": 1.5, "valve_closed_thr": 4}},
            "outdoor_air_fraction",
        ],
    }


_YAML_BY_HAND = """\
# The same config as the JSON one, written by hand, with the calibration notes as comments.
site: TestHQ
source:
  kind: perpoint_csv
  folder: trends
mapping:
  aliases:
    CHW_Valve: cool_valve
    HHW_Valve: heat_valve
    MixedAir: mixed_air_temp
    SupplyAir: supply_air_temp
    OSA: oat
equipment:
  - class: AHU
    marker: CHW_Valve
rules:
  - name: simultaneous_heat_cool
    params:
      warn_pct: 0.5   # healthy units in this portfolio sit below 0.3 %
      fault_pct: 4.0
  - name: leaking_valve
    params:
      # calibrated on a fan-on, valves-closed week: median rise 1.5 F
      fan_heat_f: 1.5
      valve_closed_thr: 4
  - outdoor_air_fraction
"""


def _as_dicts(res):
    return json.loads(json.dumps([f.as_dict() for f in res.findings], default=str))


def test_equivalent_json_and_yaml_configs_give_identical_results(tmp_path):
    pytest.importorskip("yaml")
    cfg = _site(tmp_path)
    (tmp_path / "run.json").write_text(json.dumps(cfg))
    (tmp_path / "run.yaml").write_text(_YAML_BY_HAND)
    (tmp_path / "run.yml").write_text(dump_yaml(cfg))
    assert load_config(str(tmp_path / "run.yaml")) == cfg
    assert load_config(str(tmp_path / "run.yml")) == cfg
    a = run_config_file(str(tmp_path / "run.json"))
    b = run_config_file(str(tmp_path / "run.yaml"))
    c = run_config_file(str(tmp_path / "run.yml"))
    assert a.findings and _as_dicts(a) == _as_dicts(b) == _as_dicts(c)
    assert a.rules_run == b.rules_run == c.rules_run


def test_cli_run_reads_yaml(tmp_path, capsys):
    pytest.importorskip("yaml")
    cfg = _site(tmp_path)
    (tmp_path / "run.json").write_text(json.dumps(cfg))
    (tmp_path / "run.yaml").write_text(_YAML_BY_HAND)
    assert main(["run", str(tmp_path / "run.json"), "--out", str(tmp_path / "j")]) == 0
    assert main(["run", str(tmp_path / "run.yaml"), "--out", str(tmp_path / "y")]) == 0
    capsys.readouterr()
    j = json.loads((tmp_path / "j" / "findings.json").read_text())
    y = json.loads((tmp_path / "y" / "findings.json").read_text())
    assert j == y and j


def test_missing_pyyaml_says_how_to_install(tmp_path, monkeypatch, capsys):
    import camber._yaml as cy

    monkeypatch.setitem(sys.modules, "yaml", None)  # import yaml -> ImportError
    monkeypatch.setattr(cy, "_LOADER", None)
    (tmp_path / "run.yaml").write_text("site: x\n")
    with pytest.raises(ImportError, match=r"camber-toolkit\[yaml\]"):
        load_config(str(tmp_path / "run.yaml"))
    (tmp_path / "run.json").write_text('{"site": "x"}')
    assert load_config(str(tmp_path / "run.json")) == {"site": "x"}  # JSON needs nothing
    assert main(["run", str(tmp_path / "run.yaml")]) == 1  # a clear error, not a traceback
    assert "pip install 'camber-toolkit[yaml]'" in capsys.readouterr().err


def test_yaml_types_match_json():
    pytest.importorskip("yaml")
    got = loads_yaml(
        "a: 07:00\nb: no\nc: 2018-07-01\nd: 012\ne: 1e-06\nf: 3\ng: .5\nh: ~\n"
        "i: true\nj: NO\nk: on\nl: -2.5E+3\nm: 1_000\n"
    )
    assert got == {
        "a": "07:00",  # not base-60
        "b": "no",  # not a boolean
        "c": "2018-07-01",  # not a date
        "d": "012",  # not octal
        "e": 1e-06,  # a float, as in JSON
        "f": 3,
        "g": 0.5,
        "h": None,
        "i": True,
        "j": "NO",
        "k": "on",
        "l": -2500.0,
        "m": "1_000",
    }


def test_yaml_config_must_be_a_mapping(tmp_path):
    pytest.importorskip("yaml")
    (tmp_path / "bad.yaml").write_text("- a\n- b\n")
    with pytest.raises(ValueError, match="mapping"):
        read_config_file(str(tmp_path / "bad.yaml"))
    (tmp_path / "broken.yaml").write_text("a: [1, 2\n")
    with pytest.raises(ValueError, match="invalid YAML"):
        read_config_file(str(tmp_path / "broken.yaml"))


def test_dump_yaml_round_trips_every_shipped_template():
    yaml = pytest.importorskip("yaml")
    paths = glob.glob(os.path.join(_ROOT, "camber", "datasets", "configs", "**", "*.json"))
    assert paths
    for path in paths:
        with open(path, encoding="utf-8") as fh:
            cfg = json.load(fh)
        text = dump_yaml(cfg)
        want = {k: v for k, v in cfg.items() if k != "_comment"}  # the note became a comment
        assert loads_yaml(text) == want, path
        assert yaml.safe_load(text) == want, path  # plain PyYAML reads it the same way
        if "_comment" in cfg:
            assert text.startswith("# ")


def test_dump_yaml_notes_and_scalars():
    pytest.importorskip("yaml")
    data = {"a": [1, [2, 3], {"x": 1e-6, "y": "07:00", "z": "NO"}, []], "b": {}, "c": None}
    text = dump_yaml(data, notes={("a", 2, "x"): "why x"}, header="top")
    assert text.startswith("# top\n") and "# why x" in text
    assert loads_yaml(text) == data


def test_datasets_config_format_yaml(tmp_path, capsys):
    pytest.importorskip("yaml")
    store = str(tmp_path / "store")
    assert main(["datasets", "config", "lbnl-sdahu", "--store", store]) == 0
    as_json = json.loads(capsys.readouterr().out)
    assert main(["datasets", "config", "lbnl-sdahu", "--store", store, "--format", "yaml"]) == 0
    text = capsys.readouterr().out
    assert text.startswith("# Run template for the lbnl-sdahu")  # the note is a comment now
    as_json.pop("_comment")
    assert loads_yaml(text) == as_json
    out = str(tmp_path / "cfg.yaml")  # the suffix picks the format
    assert main(["datasets", "config", "lbnl-sdahu", "--store", store, "--out", out]) == 0
    capsys.readouterr()
    assert load_config(out) == as_json


def test_rule_basis_travels_into_the_findings(tmp_path):
    cfg = _site(tmp_path)
    cfg["rules"] = [
        {
            "name": "leaking_valve",
            "params": {"fan_heat_f": 1.5},
            "basis": {"fan_heat_f": "calibrated on AHU_1 week 1", "delta_thr_f": "default kept"},
        }
    ]
    res = run_config(cfg, base_dir=str(tmp_path))
    (f,) = [f for f in res.findings if f.rule == "leaking_valve"]
    assert f.metrics["param_basis"] == {
        "fan_heat_f": {"value": 1.5, "basis": "calibrated on AHU_1 week 1"},
        "delta_thr_f": {"value": 3.0, "basis": "default kept"},
    }
    cfg["rules"][0]["basis"] = {"fan_heat": "typo"}
    with pytest.raises(ValueError, match="unknown parameter"):
        run_config(cfg, base_dir=str(tmp_path))
