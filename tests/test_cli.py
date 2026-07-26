"""Tests for the subcommand CLI (camber.cli): run / report / explain / ask / fleet / charts."""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cli import main  # noqa: E402

_MAPPING = {"aliases": {"CHW_Valve": "cool_valve", "HHW_Valve": "heat_valve",
                        "MixedAir": "mixed_air_temp", "SupplyAir": "supply_air_temp",
                        "OSA": "oat"}}


def _write_point(folder, equip, measure, series):
    ts = series.index.strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
    pd.DataFrame({"Timestamp": ts, "Value": series.values}).to_csv(
        os.path.join(folder, f"{equip}_{measure}.csv"), index=False)


def _make_config(root, site="Demo"):
    """A runnable config over a synthetic AHU with a simultaneous-heat/cool fault."""
    trends = os.path.join(root, "trends")
    os.makedirs(trends, exist_ok=True)
    idx = pd.date_range("2025-07-07", periods=24 * 14, freq="1h")
    midday = (idx.dayofweek < 5) & (idx.hour >= 11) & (idx.hour < 15)
    _write_point(trends, "AHU_1", "CHW_Valve", pd.Series(60.0, index=idx))
    _write_point(trends, "AHU_1", "HHW_Valve", pd.Series(np.where(midday, 40.0, 0.0), index=idx))
    _write_point(trends, "AHU_1", "MixedAir", pd.Series(72.0, index=idx))
    _write_point(trends, "AHU_1", "SupplyAir", pd.Series(55.0, index=idx))
    _write_point(trends, "AHU_1", "OSA", pd.Series(88.0, index=idx))
    cfg = {"site": site, "source": {"kind": "perpoint_csv", "folder": "trends"},
           "mapping": _MAPPING, "equipment": [{"class": "AHU", "marker": "CHW_Valve"}],
           "rules": ["simultaneous_heat_cool", "reheat_penalty"]}
    path = os.path.join(root, "config.json")
    open(path, "w").write(json.dumps(cfg))
    return path


def test_run_prints_findings_and_writes_json(tmp_path, capsys):
    cfg = _make_config(str(tmp_path))
    rc = main(["run", cfg, "--out", str(tmp_path / "out")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "equipment" in out and "findings" in out
    saved = json.load(open(tmp_path / "out" / "findings.json"))
    assert isinstance(saved, list) and saved


def test_report_writes_html(tmp_path):
    cfg = _make_config(str(tmp_path))
    dest = str(tmp_path / "site.html")
    assert main(["report", cfg, "--out", dest]) == 0
    html = open(dest).read()
    assert "<h" in html.lower() and "findings" in html.lower()   # an HTML audit report body


def test_explain_is_grounded_template(tmp_path, capsys):
    cfg = _make_config(str(tmp_path))
    assert main(["explain", cfg]) == 0
    out = capsys.readouterr().out
    assert "template" in out and "grounded=True" in out


def test_ask_is_grounded_and_cites(tmp_path, capsys):
    cfg = _make_config(str(tmp_path))
    assert main(["ask", "what should I do?", "--config", cfg]) == 0
    out = capsys.readouterr().out
    assert "grounded=True" in out and "[" in out          # a citation token present


def test_ask_with_llm_cmd_runs(tmp_path, capsys):
    cfg = _make_config(str(tmp_path))
    # a vendor-neutral shell "model": echo the prompt back (cat). Must run and stay grounded.
    rc = main(["ask", "summarize", "--config", cfg, "--llm-cmd", "cat"])
    assert rc == 0
    assert capsys.readouterr().out.strip()                # produced an answer


def test_ask_llm_cmd_failure_surfaces(tmp_path):
    cfg = _make_config(str(tmp_path))
    with pytest.raises(RuntimeError, match="llm-cmd failed"):
        main(["ask", "q", "--config", cfg, "--llm-cmd", "false"])


def test_fleet_rollup_and_triage(tmp_path, capsys):
    a = _make_config(str(tmp_path / "a"), site="Building A")
    b = _make_config(str(tmp_path / "b"), site="Building B")
    glob = str(tmp_path / "*" / "config.json")
    rc = main(["fleet", glob, "--ask", "which building is worst?", "--out", str(tmp_path / "f.html")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Building A" in out and "Building B" in out and "grounded=True" in out
    assert os.path.exists(tmp_path / "f.html")


def test_fleet_no_match_returns_error(tmp_path, capsys):
    assert main(["fleet", str(tmp_path / "none" / "*.json")]) == 2


def test_charts_demo_backward_compatible(tmp_path, capsys):
    rc = main(["charts", "--demo", "reheat", "--ahu", "1", "--out", str(tmp_path / "charts")])
    assert rc == 0
    assert os.path.exists(tmp_path / "charts" / "hec_summary.json")


def test_no_subcommand_errors():
    with pytest.raises(SystemExit):
        main([])
