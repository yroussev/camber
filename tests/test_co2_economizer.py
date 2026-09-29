"""0.93 (#38): co2_ventilation leaves economizer-mode hours out of over-ventilation, and the fleet
twin co2_ventilation_system joins zone CO2 to its air handler's economizer state."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.iaq import analyze_co2_ventilation, economizer_mode_mask  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.model.topology import Topology  # noqa: E402
from camber.rules.iaq_rule import CO2Ventilation, CO2VentilationSystem  # noqa: E402

IDX = pd.date_range("2025-06-02", periods=24 * 14, freq="1h")  # two weeks from a Monday

# ============================================================================ #38


def _econ_unit(co2_rise=60.0):
    """An AHU economizing on mild afternoons (damper 100 %), at a 20 % minimum otherwise."""
    n = len(IDX)
    oat = 55.0 + 15.0 * np.sin(2 * np.pi * (IDX.hour - 9) / 24)
    damper = np.where(oat > 62.0, 100.0, 20.0)
    co2 = 420.0 + np.where(damper > 50, 30.0, co2_rise) + np.random.default_rng(0).normal(0, 5, n)
    return pd.DataFrame({Role.OAT: oat, Role.OA_DAMPER: damper, Role.CO2: co2}, index=IDX)


def test_economizer_mode_mask_bases():
    u = _econ_unit()
    m, basis = economizer_mode_mask(IDX, oat=u[Role.OAT], damper=u[Role.OA_DAMPER])
    assert basis == "oat_damper" and m.equals(u[Role.OA_DAMPER] > 50)
    # above the high limit the damper opening is not economizing
    m2, _ = economizer_mode_mask(IDX, oat=u[Role.OAT], damper=u[Role.OA_DAMPER], high_limit_f=60.0)
    assert not m2.any()
    cmd = pd.Series(np.where(IDX.hour == 12, 0.5, 0.0), IDX)
    m3, basis3 = economizer_mode_mask(IDX, econ_cmd=cmd)
    assert basis3 == "econ_cmd" and int(m3.sum()) == 14
    # an OAT alone never says a unit economizes (a 100 % OA unit, a zone)
    assert economizer_mode_mask(IDX, oat=u[Role.OAT]) == (None, "none")
    # mixed-air balance: MAT at OAT = 100 % outside air
    m4, _ = economizer_mode_mask(IDX, oat=u[Role.OAT], mat=u[Role.OAT], rat=pd.Series(75.0, IDX))
    assert m4.all()


def test_over_ventilation_leaves_economizer_hours_out():
    u = _econ_unit(co2_rise=400.0)  # adequately ventilated at minimum OA
    econ = u[Role.OA_DAMPER] > 50
    res = analyze_co2_ventilation(u.rename(columns={Role.CO2: "CO2"}), "Z", economizer_mask=econ)
    assert res.over_vent_pct == 0.0 and res.over_vent_econ_pct == 100.0
    assert res.over_vent_all_pct > 0 and 0 < res.econ_hours_pct < 100
    ok = CO2Ventilation().analyze("RTU-1", u)
    assert ok.severity == "ok" and ok.metrics["economizer_basis"] == "oat_damper"
    # without the exclusion, economizer hours read as over-ventilation
    assert (
        CO2Ventilation(exclude_economizer=False).analyze("RTU-1", u).metrics["over_vent_pct"]
        == res.over_vent_all_pct
    )
    # near outdoor at the MINIMUM damper too -> genuinely over-ventilated
    lo = CO2Ventilation().analyze("RTU-1", _econ_unit(co2_rise=60.0))
    assert lo.severity == "warn" and "non-economizer" in lo.summary


def test_over_ventilation_not_judged_when_economizing_nearly_always():
    u = _econ_unit()
    u[Role.OA_DAMPER] = 100.0
    u.iloc[:5, u.columns.get_loc(Role.OA_DAMPER)] = 20.0
    got = CO2Ventilation(oa_damper_min_pct=20.0).analyze("RTU-1", u)
    assert got.metrics["over_vent_pct"] is None and "not judged" in got.summary


def test_zone_without_economizer_evidence_is_caveated_when_over_ventilated():
    z = pd.DataFrame({Role.CO2: 430.0}, index=IDX)
    got = CO2Ventilation().analyze("VAV-1", z)
    assert got.severity == "warn" and any("co2_ventilation_system" in c for c in got.caveats)


def test_system_rule_joins_zones_to_their_air_handler():
    u = _econ_unit(co2_rise=400.0)
    frames = {
        "RTU_1": u.drop(columns=[Role.CO2]),
        "RTU_1_zone_1": pd.DataFrame({Role.CO2: u[Role.CO2]}),
        "RTU_1_zone_2": pd.DataFrame({Role.CO2: u[Role.CO2] + 10.0}),
        "orphan": pd.DataFrame({Role.CO2: 430.0}, index=IDX),
    }
    topo = Topology.from_parent_map({"RTU_1_zone_1": "RTU_1", "RTU_1_zone_2": "RTU_1"})
    got = CO2VentilationSystem().analyze_fleet(frames, topology=topo)
    pz = got.metrics["per_zone"]
    assert pz["RTU_1_zone_1"]["severity"] == "ok"
    assert pz["RTU_1_zone_1"]["economizer_source"] == "RTU_1"
    assert pz["orphan"]["severity"] == "warn" and got.metrics["n_zones_unattributed"] == 1
    assert got.severity == "warn"
    # one economizing unit and no topology: every zone joins it, with a caveat
    solo = CO2VentilationSystem().analyze_fleet(
        {k: v for k, v in frames.items() if k != "orphan"}, topology=None
    )
    assert solo.metrics["grouping_provenance"] == "single_source"
    empty = CO2VentilationSystem().analyze_fleet({"RTU_1": frames["RTU_1"]})
    assert empty.metrics["declined"] is True
