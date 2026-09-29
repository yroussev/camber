"""Refrigerant saturation properties and the pressure/temperature transforms (0.93, #39)."""

import dataclasses
import json
import math
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import refrigerant as R  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.resolve import discover, discover_store, resolve  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

# NIST REFPROP saturation temperatures published beside the raw pressures in the NIST
# residential heat-pump FDD data (R-410A; doi:10.18434/M32132, NIST public data): liquid-line
# service valve (psia -> bubble degF) and vapour service valve (psia -> dew degF).
_NIST_BUBBLE = [
    (244.332, 78.104),
    (309.126, 94.551),
    (358.941, 105.499),
    (383.854, 110.544),
    (443.243, 121.627),
    (624.846, 149.496),
]
_NIST_DEW = [
    (79.507, 11.737),
    (137.308, 41.844),
    (144.659, 44.934),
    (152.571, 48.131),
    (162.58, 52.0),
    (238.469, 76.673),
]


@pytest.mark.parametrize("point,table", [("bubble", _NIST_BUBBLE), ("dew", _NIST_DEW)])
def test_r410a_matches_nist_refprop_within_a_twentieth_of_a_degree(point, table):
    p = np.array([a for a, _ in table])
    t = np.array([b for _, b in table])
    got = R.saturation_temp(p, "R-410A", point=point, basis="absolute")
    assert np.max(np.abs(got - t)) < 0.05


def test_r410a_pt_chart_points_and_glide():
    # the familiar gauge-set numbers: 118.8 psig at 40 degF, ~31 degF at 100 psig
    assert R.saturation_pressure(40.0, "R-410A") == pytest.approx(118.8, abs=0.3)
    bub = R.saturation_temp(100.0, "R-410A", point="bubble")
    dew = R.saturation_temp(100.0, "R-410A", point="dew")
    assert bub == pytest.approx(31.3, abs=0.2)
    assert 0.1 < dew - bub < 0.3  # a near-azeotrope's small glide


@pytest.mark.parametrize("fluid", sorted(R.REFRIGERANTS))
def test_round_trip_every_fluid(fluid):
    ref = R.get_refrigerant(fluid)
    temps = np.linspace(-40.0, min(150.0, ref.critical_temp_f - 2.0), 40)
    for point in ("bubble", "dew"):
        p = R.saturation_pressure(temps, fluid, point=point)
        back = R.saturation_temp(p, fluid, point=point)
        assert np.max(np.abs(back - temps)) < 1e-6


def test_pure_fluid_spot_values():
    # R-134a at 40 degF ~ 35 psig, R-22 ~ 68.5 psig, R-32 ~ 121 psig (common P-T chart values)
    assert R.saturation_pressure(40.0, "R-134a") == pytest.approx(35.0, abs=0.5)
    assert R.saturation_pressure(40.0, "R-22") == pytest.approx(68.5, abs=0.7)
    assert R.saturation_pressure(40.0, "R-32") == pytest.approx(121.0, abs=1.5)


def test_against_coolprop_when_installed():
    cp = pytest.importorskip("CoolProp.CoolProp")
    names = {"R-410A": "R410A", "R-134a": "R134a", "R-22": "R22", "R-32": "R32", "R-744": "CO2"}
    for fluid, cpname in names.items():
        ref = R.get_refrigerant(fluid)
        for t_f in np.linspace(-30.0, min(140.0, ref.critical_temp_f - 3.0), 12):
            for q, point in ((0, "bubble"), (1, "dew")):
                pa = cp.PropsSI("P", "T", (t_f - 32) / 1.8 + 273.15, "Q", q, cpname)
                psia = pa / 6894.757
                got = R.saturation_temp(psia, fluid, point=point, basis="absolute")
                assert abs(got - t_f) < 0.05, (fluid, t_f, point)


def test_co2_declines_above_the_critical_point():
    ref = R.get_refrigerant("CO2")
    assert ref.name == "R-744"
    assert ref.critical_pressure_psia == pytest.approx(1070.0, abs=1.0)
    assert ref.critical_temp_f == pytest.approx(87.8, abs=0.1)
    p = pd.Series([400.0, 1040.0, 1100.0, 1500.0, float("nan")])
    t = R.saturation_temp(p, "R-744")
    assert t.iloc[:2].notna().all() and t.iloc[2:].isna().all()
    assert R.is_supercritical(p, "R-744").tolist() == [False, False, True, True, False]
    sc = R.subcooling(p, pd.Series(80.0, index=p.index), "R-744")
    assert sc.iloc[2:].isna().all()  # no saturation, no subcooling: the transform declines


def test_units_and_bases():
    psig = 100.0
    base = R.saturation_temp(psig, "R-410A")
    assert R.saturation_temp(psig + R.STANDARD_ATM_PSIA, "R-410A", basis="absolute") == (
        pytest.approx(base)
    )
    kpa = psig / 0.14503773773
    assert R.saturation_temp(kpa, "R-410A", pressure_unit="kPa") == pytest.approx(base)
    assert R.saturation_temp(psig, "R-410A", temp_unit="C") == pytest.approx((base - 32) / 1.8)
    assert R.saturation_temp(psig, "R-410A", temp_unit="K") == pytest.approx(
        (base - 32) / 1.8 + 273.15
    )
    # altitude: a lower atmosphere makes the same gauge reading a lower absolute pressure
    assert R.saturation_temp(psig, "R-410A", atm_psia=12.2) < base
    for bad in (
        dict(basis="vacuum"),
        dict(pressure_unit="atm"),
        dict(temp_unit="R"),
        dict(point="triple"),
    ):
        with pytest.raises(ValueError):
            if "temp_unit" in bad:
                R.saturation_temp(psig, "R-410A", **bad)
            else:
                R.saturation_temp(psig, "R-410A", **bad)
    with pytest.raises(ValueError):
        R.saturation_pressure(40.0, "R-410A", basis="vacuum")


def test_out_of_range_readings_are_nan_not_extrapolated():
    got = R.saturation_temp(pd.Series([-70.0, -10.0, 100.0, 800.0]), "R-410A")
    # below a vacuum (a dead transducer), at the edge, normal, above the pseudo-critical point
    assert math.isnan(got.iloc[0]) and got.iloc[1] < -80 and not math.isnan(got.iloc[2])
    assert math.isnan(got.iloc[3])
    assert math.isnan(R.saturation_temp("n/a", "R-410A"))
    assert math.isnan(R.saturation_pressure(200.0, "R-410A"))  # above T_c


def test_names():
    for spelling in ("R-410A", "r410a", "410A", "R 410 A", "R410a"):
        assert R.normalize_refrigerant(spelling) == "R-410A"
    assert R.normalize_refrigerant("co2") == "R-744"
    assert R.normalize_refrigerant("R134A") == "R-134a"
    assert R.normalize_refrigerant(R.get_refrigerant("R-22")) == "R-22"
    with pytest.raises(ValueError, match="supported"):
        R.normalize_refrigerant("R-1234yf")


def test_transform_signs():
    p_liq, p_suc = 330.0, 118.8  # R-410A: ~105 degF condensing, ~40 degF evaporating
    t_cond = R.saturation_temp(p_liq, "R-410A")
    assert R.subcooling(p_liq, t_cond - 10.0, "R-410A") == pytest.approx(10.0)
    t_evap = R.saturation_temp(p_suc, "R-410A", point="dew")
    assert R.superheat(p_suc, t_evap + 8.0, "R-410A") == pytest.approx(8.0)
    t_dew = R.saturation_temp(p_liq, "R-410A", point="dew")
    assert R.discharge_superheat(p_liq, t_dew + 45.0, "R-410A") == pytest.approx(45.0)
    assert R.condenser_approach(p_liq, t_dew - 3.0, "R-410A") == pytest.approx(3.0)
    assert R.evaporator_approach(p_suc, t_evap + 5.0, "R-410A") == pytest.approx(5.0)


def _role_frame(n=6):
    idx = pd.date_range("2025-07-01", periods=n, freq="1h")
    return pd.DataFrame(
        {
            Role.DISCHARGE_PRESSURE: np.full(n, 330.0),
            Role.SUCTION_PRESSURE: np.full(n, 118.8),
            Role.LIQUID_LINE_TEMP: np.full(n, 95.0),
            Role.SUCTION_LINE_TEMP: np.full(n, 48.0),
            Role.DISCHARGE_LINE_TEMP: np.full(n, 160.0),
            Role.CW_RETURN_TEMP: np.full(n, 95.0),
            Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
        },
        index=idx,
    )


def test_derive_refrigerant_roles_adds_every_derivable_role():
    f = _role_frame()
    out = R.derive_refrigerant_roles(f, "R-410A")
    for role in R.DERIVED_ROLES:
        assert role in out.columns
    assert out[Role.SUBCOOLING_TEMP].iloc[0] == pytest.approx(
        R.saturation_temp(330.0, "R-410A") - 95.0
    )
    notes = out.attrs["refrigerant_derived"]
    assert notes["subcooling_temp"]["from"] == ["discharge_pressure", "liquid_line_temp"]
    assert notes["superheat_temp"]["declined_rows"] == 0
    assert Role.SUBCOOLING_TEMP not in f.columns  # the input is untouched


def test_derive_prefers_liquid_pressure_and_keeps_a_reported_value():
    f = _role_frame()
    f[Role.LIQUID_LINE_PRESSURE] = 320.0
    f[Role.SUPERHEAT_TEMP] = 11.0  # controller-reported: kept
    out = R.derive_refrigerant_roles(f, "R-410A")
    assert out.attrs["refrigerant_derived"]["subcooling_temp"]["from"][0] == "liquid_line_pressure"
    assert (out[Role.SUPERHEAT_TEMP] == 11.0).all()
    assert "superheat_temp" not in out.attrs["refrigerant_derived"]
    over = R.derive_refrigerant_roles(f, "R-410A", overwrite=True)
    assert over[Role.SUPERHEAT_TEMP].iloc[0] != 11.0


def test_derive_counts_declined_rows():
    f = _role_frame(3)
    f.loc[f.index[0], Role.DISCHARGE_PRESSURE] = -70.0  # an unpowered transducer
    out = R.derive_refrigerant_roles(f, "R-410A", roles=[Role.SUBCOOLING_TEMP])
    assert out.attrs["refrigerant_derived"]["subcooling_temp"]["declined_rows"] == 1
    assert list(out.attrs["refrigerant_derived"]) == ["subcooling_temp"]
    assert R.derivation_inputs([Role.SUBCOOLING_TEMP, Role.OAT]) == (
        Role.LIQUID_LINE_PRESSURE,
        Role.DISCHARGE_PRESSURE,
        Role.LIQUID_LINE_TEMP,
    )


def test_resolve_derives_from_a_store_when_the_refrigerant_is_named(tmp_path):
    store = ParquetStore(str(tmp_path / "store"))
    store.write_role_frame(_role_frame(), facility_id="f1", equip="CH1", equip_class="CHILLER")
    ref = discover_store(store, "f1", "CHILLER")[0]
    plain = resolve(ref, None, (Role.SUBCOOLING_TEMP, Role.DISCHARGE_PRESSURE), resample="1h")
    assert Role.SUBCOOLING_TEMP not in plain.columns
    tagged = dataclasses.replace(ref, refrigerant="R-410A")
    got = resolve(tagged, None, (Role.SUBCOOLING_TEMP, Role.SUPERHEAT_TEMP), resample="2h")
    assert list(got.columns) == [Role.SUBCOOLING_TEMP, Role.SUPERHEAT_TEMP]
    assert got[Role.SUBCOOLING_TEMP].iloc[0] == pytest.approx(
        R.saturation_temp(330.0, "R-410A") - 95.0
    )


def test_resolve_derives_from_a_folder_and_drops_the_extra_inputs(tmp_path):
    rows = "".join(f"07-Jul-25 {h:02d}:00:00 AM PDT,{{v}}\n" for h in (8, 9, 10))
    points = {"PDis": 330.0, "LiqT": 95.0, "PSuc": 118.8}
    for tok, v in points.items():
        with open(tmp_path / f"CH_1_{tok}.csv", "w", encoding="utf-8") as fh:
            fh.write("Timestamp,Value\n" + rows.format(v=v))
    mapping = MappingProvider.from_dict(
        {
            "aliases": {
                "PDis": "discharge_pressure",
                "LiqT": "liquid_line_temp",
                "PSuc": "suction_pressure",
            }
        }
    )
    ref = discover(str(tmp_path), "CH", marker_measure="PDis")[0]
    ref = dataclasses.replace(ref, refrigerant="R-410A")
    got = resolve(ref, mapping, (Role.SUBCOOLING_TEMP, Role.SUCTION_PRESSURE), resample="1h")
    assert set(got.columns) == {Role.SUBCOOLING_TEMP, Role.SUCTION_PRESSURE}
    assert got[Role.SUBCOOLING_TEMP].dropna().iloc[0] == pytest.approx(
        R.saturation_temp(330.0, "R-410A") - 95.0
    )


def test_config_equipment_refrigerant_tags_refs(tmp_path):
    from camber.config import _with_refrigerant

    store = ParquetStore(str(tmp_path / "store"))
    store.write_role_frame(_role_frame(), facility_id="f1", equip="CH1", equip_class="CHILLER")
    refs = discover_store(store, "f1", "CHILLER")
    assert _with_refrigerant(refs, {"class": "CHILLER"}) == refs
    tagged = _with_refrigerant(refs, {"class": "CHILLER", "refrigerant": "r410a"})
    assert tagged[0].refrigerant == "R-410A"
    with pytest.raises(ValueError):
        _with_refrigerant(refs, {"class": "CHILLER", "refrigerant": "R-9999"})


def test_config_run_feeds_the_chiller_subcooling_detector(tmp_path):
    """A store chiller with pressures + line temperatures runs the subcooling drift detector."""
    from camber.config import run_drift_config

    n = 24 * 20
    idx = pd.date_range("2025-06-01", periods=n, freq="1h")
    rng = np.random.default_rng(3)
    load = 0.5 + 0.4 * np.sin(np.arange(n) / 24 * 2 * np.pi) ** 2
    frame = pd.DataFrame(
        {
            Role.CHW_FLOW: 20.0 + 5.0 * load,
            Role.CHW_RETURN_TEMP: 44.0 + 10.0 * load,
            Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
            Role.DISCHARGE_PRESSURE: 320.0 + 20.0 * load + rng.normal(0, 1.0, n),
            Role.LIQUID_LINE_TEMP: 88.0 + 3.0 * load + rng.normal(0, 0.3, n),
            Role.SUCTION_PRESSURE: np.full(n, 105.0),
            Role.SUCTION_LINE_TEMP: np.full(n, 42.0),
        },
        index=idx,
    )
    store = ParquetStore(str(tmp_path / "store"))
    store.write_role_frame(frame, facility_id="ds-x", equip="CH1", equip_class="CHILLER")
    cfg = {
        "site": "x",
        "source": {"kind": "store", "store": str(tmp_path / "store"), "facility_id": "ds-x"},
        "equipment": [{"class": "CHILLER", "marker_role": "discharge_pressure"}],
        "drift": {
            "store": str(tmp_path / "b.json"),
            "baseline": ["2025-06-01", "2025-06-10"],
            "current": ["2025-06-11", "2025-06-20"],
            "families": [{"class": "CHILLER", "family": "chiller"}],
        },
    }

    def subcool(cfg):
        res = run_drift_config(cfg, base_dir=str(tmp_path), freeze_if_missing=True)
        return next(f for f in res.findings if f.rule == "chiller_subcooling_drift")

    untagged = subcool(json.loads(json.dumps(cfg)))
    assert untagged.metrics.get("reason") == "subcooling_not_mapped"
    cfg["equipment"][0]["refrigerant"] = "R-410A"
    cfg["drift"]["store"] = str(tmp_path / "b2.json")
    tagged = subcool(cfg)
    assert not tagged.metrics.get("declined"), tagged.caveats
    assert tagged.severity == "ok"
