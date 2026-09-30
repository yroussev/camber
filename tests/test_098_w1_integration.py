"""0.98 wave 1 integration: the branches' features working together.

- the thresholds registry documents the air-gates and terminal-ventilation params;
- a YAML config that sets ``occupancy_gate`` and ``shortfall_share_pct`` gives the same findings as
  its JSON equivalent;
- the RCx report built from that YAML config lists the checks not evaluated and the M&V data
  needed.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cli import main  # noqa: E402
from camber.config import run_config_file  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

FID = "w1-fac"
START = "2026-03-02"  # a Monday


def test_rules_params_shows_the_occupancy_gate(capsys):
    assert main(["rules", "params", "supply_air_control"]) == 0
    out = capsys.readouterr().out
    assert 'occupancy_gate = "trended"  [choice]' in out
    assert 'one of "trended", "schedule", "off"' in out
    snippet = json.loads(out[out.index("{") :])
    assert snippet["rules"][0]["params"]["occupancy_gate"] == "trended"


def _store(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    idx = pd.date_range(START, periods=24 * 14, freq="h")
    h = idx.hour.to_numpy()
    occ = ((idx.dayofweek < 5) & (h >= 7) & (h < 18)).astype(float)
    fan = ((idx.dayofweek < 5) & (h >= 5) & (h < 21)).astype(float)  # runs past occupancy
    sat = np.where(occ > 0, 55.0, 60.0)  # 5 F off setpoint only in unoccupied fan-on hours
    st.write_role_frame(
        pd.DataFrame(
            {
                Role.SUPPLY_AIR_TEMP: sat,
                Role.SUPPLY_AIR_TEMP_SP: 55.0,
                Role.SUPPLY_FAN_STATUS: fan,
                Role.OCCUPANCY: occ,
            },
            index=idx,
        ),
        facility_id=FID,
        equip="AHU1",
        equip_class="AHU",
    )
    # a zone 4 F below its heating setpoint with the reheat valve maxed for 4 occupied hours
    # (about 3.6 % of the occupied samples): fault-deep, a small share
    short = (idx.normalize() == pd.Timestamp("2026-03-04")) & (h >= 8) & (h < 12)
    st.write_role_frame(
        pd.DataFrame(
            {
                Role.SPACE_TEMP: np.where(short, 66.0, 72.0),
                Role.COOL_SP: 75.0,
                Role.HEAT_SP: 70.0,
                Role.HEAT_VALVE: np.where(short, 100.0, 10.0),
                Role.SUPPLY_FAN_STATUS: fan,
                Role.OCCUPANCY: occ,
            },
            index=idx,
        ),
        facility_id=FID,
        equip="VAV1",
        equip_class="VAV",
    )
    days = pd.date_range("2024-01-01", periods=24 * 10, freq="h")
    rng = np.random.default_rng(0)
    t = 50 + 15 * np.sin(np.arange(len(days)) / 24 * 2 * np.pi) + rng.normal(0, 1, len(days))
    st.write_role_frame(
        pd.DataFrame({Role.POWER: 100 + 2 * t, Role.OAT: t}, index=days),
        facility_id=FID,
        equip="Meter",
        equip_class="M",
    )
    st.register_facility(FID, name="W1 facility")


_CONFIG = {
    "site": "W1 facility",
    "source": {"kind": "store", "store": "store", "facility_id": FID},
    "equipment": [{"class": "AHU"}, {"class": "VAV"}, {"class": "M"}],
    "rules": [
        {"name": "supply_air_control", "params": {"occupancy_gate": "off"}},
        {
            "name": "overcooling_severity",
            "params": {"shortfall_share_pct": {"warn": 2.0, "fault": 10.0}},
        },
        "chw_plant_reset",
    ],
    "mv": [
        {
            "class": "M",
            "role": "power",
            "period": ["2024-01-01", "2024-01-05"],
            "reporting_period": ["2024-01-06", None],
        }
    ],
}

_YAML = """\
# the JSON config above, by hand
site: W1 facility
source: {kind: store, store: store, facility_id: w1-fac}
equipment:
  - class: AHU
  - class: VAV
  - class: M
rules:
  - name: supply_air_control
    params:
      occupancy_gate: "off"   # quoted: a bare off would be a string too, as in JSON
  - name: overcooling_severity
    params:
      shortfall_share_pct: {warn: 2.0, fault: 10.0}
  - chw_plant_reset
mv:
  - class: M
    role: power
    period: [2024-01-01, 2024-01-05]
    reporting_period: [2024-01-06, null]
"""


def _dicts(res):
    return json.loads(json.dumps([f.as_dict() for f in res.findings], default=str))


@pytest.fixture
def runs(tmp_path):
    pytest.importorskip("yaml")
    _store(tmp_path)
    (tmp_path / "run.json").write_text(json.dumps(_CONFIG))
    (tmp_path / "run.yaml").write_text(_YAML)
    return run_config_file(str(tmp_path / "run.json")), run_config_file(str(tmp_path / "run.yaml"))


def test_yaml_and_json_set_the_new_params_identically(runs):
    j, y = runs
    assert _dicts(j) == _dicts(y) and j.rules_run == y.rules_run
    assert [s.as_dict() for s in j.rules_skipped] == [s.as_dict() for s in y.rules_skipped]
    by = {f.rule: f for f in y.findings}
    assert by["supply_air_control"].metrics["occupancy_gate"] == "off"
    oc = by["overcooling_severity"].metrics
    # 3.6 % share: above the configured 2 % warn, below the default 5 % (which would say info)
    assert oc["shortfall_depth_severity"] == "fault" and oc["shortfall_severity"] == "warn"


def test_rcx_from_yaml_lists_skips_and_data_needed(runs):
    from camber.report import build_rcx_report

    _, y = runs
    assert any(s.rule == "chw_plant_reset" for s in y.rules_skipped)
    need = {f.rule: f for f in y.findings}["mv_baseline"].metrics["data_needed"]
    assert need["shortfall"] > 0
    html = build_rcx_report(y).to_html()
    assert "Checks not evaluated (missing inputs)" in html
    assert "<td>Checks not evaluated</td>" in html
    assert "Data needed (Meter): " in html
