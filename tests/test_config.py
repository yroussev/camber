"""Tests for config-driven runs (camber.config)."""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.config import run_config, run_config_file  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402


def _write_point(folder, equip, measure, series):
    # realio.load_point expects the BAS export timestamp format with a trailing tz,
    # e.g. "07-Jul-25 11:00:00 AM PDT"
    ts = series.index.strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
    df = pd.DataFrame({"Timestamp": ts, "Value": series.values})
    df.to_csv(os.path.join(folder, f"{equip}_{measure}.csv"), index=False)


def _make_site(folder):
    """One AHU with a simultaneous-heat/cool fault during occupied hours."""
    os.makedirs(folder, exist_ok=True)
    idx = pd.date_range("2025-07-07", periods=24 * 14, freq="1h")   # 2 weeks
    weekday = idx.dayofweek < 5
    midday = (idx.hour >= 11) & (idx.hour < 15)
    cool = pd.Series(60.0, index=idx)                              # cooling all day
    heat = pd.Series(np.where(weekday & midday, 40.0, 0.0), index=idx)  # midday reheat
    _write_point(folder, "AHU_1", "CHW_Valve", cool)
    _write_point(folder, "AHU_1", "HHW_Valve", heat)
    _write_point(folder, "AHU_1", "MixedAir", pd.Series(72.0, index=idx))
    _write_point(folder, "AHU_1", "SupplyAir", pd.Series(55.0, index=idx))
    _write_point(folder, "AHU_1", "OSA", pd.Series(88.0, index=idx))


_MAPPING = {"aliases": {"CHW_Valve": "cool_valve", "HHW_Valve": "heat_valve",
                        "MixedAir": "mixed_air_temp", "SupplyAir": "supply_air_temp",
                        "OSA": "oat"}}


def test_builtin_registry_has_all_rules():
    reg = builtin_registry()
    names = rule_names()
    assert "simultaneous_heat_cool" in names and "outdoor_air_fraction" in names
    assert all(reg.get(n).name == n for n in names)     # every name resolves


def test_run_config_discovers_and_flags(tmp_path):
    folder = str(tmp_path / "trends")
    _make_site(folder)
    cfg = {
        "site": "TestHQ",
        "source": {"kind": "perpoint_csv", "folder": folder},
        "mapping": _MAPPING,
        "equipment": [{"class": "AHU", "marker": "CHW_Valve"}],
        "rules": ["simultaneous_heat_cool"],
        "report": {"level": 2, "out_text": str(tmp_path / "audit.txt")},
    }
    res = run_config(cfg, base_dir=str(tmp_path))
    assert res.equipment == 1
    assert res.rules_run == ["simultaneous_heat_cool"]
    simul = [f for f in res.findings if f.rule == "simultaneous_heat_cool"]
    assert len(simul) == 1 and simul[0].severity == "fault"
    # report written and contains the prioritized finding
    txt = open(tmp_path / "audit.txt").read()
    assert "TestHQ" in txt and "simultaneous_heat_cool" in txt


def test_run_config_file_with_mapping_path(tmp_path):
    folder = str(tmp_path / "trends")
    _make_site(folder)
    (tmp_path / "mapping.json").write_text(json.dumps(_MAPPING))
    cfg = {
        "site": "TestHQ",
        "source": {"kind": "perpoint_csv", "folder": "trends"},
        "mapping": {"path": "mapping.json"},
        "equipment": [{"class": "AHU", "marker": "CHW_Valve"}],
        "rules": ["simultaneous_heat_cool"],
    }
    (tmp_path / "run.json").write_text(json.dumps(cfg))
    res = run_config_file(str(tmp_path / "run.json"))   # paths relative to file dir
    assert res.equipment == 1
    assert any(f.severity == "fault" for f in res.findings)


def _make_split_site(folder_a, folder_b):
    """One AHU whose points are split across two source folders."""
    os.makedirs(folder_a, exist_ok=True)
    os.makedirs(folder_b, exist_ok=True)
    idx = pd.date_range("2025-07-07", periods=24 * 14, freq="1h")
    weekday = idx.dayofweek < 5
    midday = (idx.hour >= 11) & (idx.hour < 15)
    cool = pd.Series(60.0, index=idx)
    heat = pd.Series(np.where(weekday & midday, 40.0, 0.0), index=idx)
    # marker + cooling in folder A; reheat + temps in folder B
    _write_point(folder_a, "AHU_1", "CHW_Valve", cool)
    _write_point(folder_b, "AHU_1", "HHW_Valve", heat)
    _write_point(folder_b, "AHU_1", "MixedAir", pd.Series(72.0, index=idx))
    _write_point(folder_b, "AHU_1", "SupplyAir", pd.Series(55.0, index=idx))
    _write_point(folder_b, "AHU_1", "OSA", pd.Series(88.0, index=idx))


def test_run_config_multi_folder_folders_list(tmp_path):
    a = str(tmp_path / "batch_a")
    b = str(tmp_path / "batch_b")
    _make_split_site(a, b)
    cfg = {
        "site": "MultiHQ",
        "source": {"kind": "perpoint_csv", "folders": [a, b]},
        "mapping": _MAPPING,
        "equipment": [{"class": "AHU", "marker": "CHW_Valve"}],
        "rules": ["simultaneous_heat_cool"],
    }
    res = run_config(cfg, base_dir=str(tmp_path))
    assert res.equipment == 1
    # the fault is only detectable because reheat (folder b) was merged with
    # cooling (folder a) for the same equipment
    simul = [f for f in res.findings if f.rule == "simultaneous_heat_cool"]
    assert len(simul) == 1 and simul[0].severity == "fault"


def test_run_config_multi_folder_globs(tmp_path):
    _make_split_site(str(tmp_path / "sites" / "a"), str(tmp_path / "sites" / "b"))
    cfg = {
        "site": "GlobHQ",
        "source": {"kind": "perpoint_csv", "globs": ["sites/*"]},
        "mapping": _MAPPING,
        "equipment": [{"class": "AHU", "marker": "CHW_Valve"}],
        "rules": ["simultaneous_heat_cool"],
    }
    res = run_config(cfg, base_dir=str(tmp_path))
    assert res.equipment == 1
    assert any(f.severity == "fault" for f in res.findings)


def test_source_requires_a_folder_spec(tmp_path):
    cfg = {"site": "X", "source": {"kind": "perpoint_csv"},
           "mapping": _MAPPING, "equipment": [], "rules": []}
    with pytest.raises(ValueError):
        run_config(cfg, base_dir=str(tmp_path))


def test_unknown_rule_name_raises(tmp_path):
    folder = str(tmp_path / "trends")
    _make_site(folder)
    cfg = {"site": "X", "source": {"kind": "perpoint_csv", "folder": folder},
           "mapping": _MAPPING, "equipment": [{"class": "AHU", "marker": "CHW_Valve"}],
           "rules": ["no_such_rule"]}
    with pytest.raises(KeyError):
        run_config(cfg, base_dir=str(tmp_path))
