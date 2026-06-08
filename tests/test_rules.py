"""Tests for the rule framework: Rule protocol, registry, runner, rule #1."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.resolve import discover  # noqa: E402
from camber.rules.base import Finding, Registry, Rule  # noqa: E402
from camber.rules.simul_hc import SimultaneousHeatCool  # noqa: E402


def test_rule_satisfies_protocol():
    assert isinstance(SimultaneousHeatCool(), Rule)


def test_finding_shape():
    f = Finding(rule="r", equip="AHU1", severity="warn", metrics={"x": 1})
    d = f.as_dict()
    assert d["rule"] == "r" and d["severity"] == "warn" and d["metrics"]["x"] == 1


def test_registry_register_lookup():
    reg = Registry()
    reg.register(SimultaneousHeatCool())
    assert reg.names() == ["simultaneous_heat_cool"]
    assert reg.get("simultaneous_heat_cool").name == "simultaneous_heat_cool"


def _role_frame(simul: bool, n=24 * 14):
    idx = pd.date_range("2025-07-07", periods=n, freq="1h")  # starts Monday
    chw = np.full(n, 60.0)
    hhw = np.full(n, 30.0) if simul else np.zeros(n)
    return pd.DataFrame({Role.COOL_VALVE: chw, Role.HEAT_VALVE: hhw}, index=idx)


def test_rule_detects_simultaneous():
    rule = SimultaneousHeatCool()
    f = rule.analyze("AHU_T", _role_frame(simul=True))
    assert f.severity == "fault"
    assert f.metrics["simultaneous_hc_pct"] > 90


def test_rule_clean_is_ok():
    rule = SimultaneousHeatCool()
    f = rule.analyze("AHU_T", _role_frame(simul=False))
    assert f.severity == "ok"
    assert f.metrics["simultaneous_hc_pct"] < 1


# ---- runner end-to-end over a tiny per-point fixture folder ----

_HDR = "Timestamp,Value (%)\n"


def _write(folder, equip, measure, const):
    # 14 days hourly so occupied-hours filtering leaves enough points
    rows = []
    base = pd.Timestamp("2025-07-07")
    for i in range(24 * 14):
        t = (base + pd.Timedelta(hours=i)).strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
        rows.append(f"{t},{const}\n")
    with open(os.path.join(folder, f"{equip}_{measure}.csv"), "w", encoding="utf-8") as f:
        f.write(_HDR + "".join(rows))


def test_runner_over_fixture_folder(tmp_path):
    folder = str(tmp_path)
    # AHU_1 with both coils open -> simultaneous fault
    _write(folder, "AHU_1", "CHW_Valve", 60)
    _write(folder, "AHU_1", "HHW_Valve", 30)

    mapping = MappingProvider.from_dict({
        "aliases": {"CHW_Valve": "cool_valve", "HHW_Valve": "heat_valve"},
    })
    reg = Registry()
    reg.register(SimultaneousHeatCool())
    refs = discover(folder, "AHU", marker_measure="CHW_Valve")
    findings = reg.run("simultaneous_heat_cool", refs, mapping)
    assert len(findings) == 1
    assert findings[0].equip == "AHU_1"
    assert findings[0].severity == "fault"
