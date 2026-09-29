"""DX / heat-pump charge and indoor-airflow rules and the dx drift family (0.93, #40)."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import faultlab  # noqa: E402
from camber.driftrun import DRIFT_FAMILIES, build_drift_suite  # noqa: E402
from camber.dxdrift import DX_DETECTORS, diagnose_dx_drift  # noqa: E402
from camber.eval import benchmark  # noqa: E402
from camber.model.equipclass import equip_family  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.applicability import RULE_EQUIP_CLASSES  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.builtin import builtin_registry, make_rule  # noqa: E402
from camber.rules.dx_airflow_rule import DXIndoorAirflow, return_dewpoint_f  # noqa: E402
from camber.rules.dx_charge_rule import DXRefrigerantCharge  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402


def _unit(
    n=240, *, seed=0, sc_shift=0.0, sh_shift=0.0, split_shift=0.0, dsh_shift=0.0, start="2025-06-01"
):
    """A split heat pump in cooling: readings that follow OAT / return air, plus a fault shift."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="1h")
    oat = 75.0 + 20.0 * rng.random(n)
    rat = 72.0 + 8.0 * rng.random(n)
    dew = 50.0 + 12.0 * rng.random(n)
    sc = 9.0 + 0.05 * (oat - 85) - 0.15 * (rat - 76) + rng.normal(0, 0.4, n) + sc_shift
    sh = 12.0 + 0.05 * (oat - 85) + rng.normal(0, 0.8, n) + sh_shift
    split = 18.0 + 0.55 * (rat - 76) - 0.45 * (dew - 56) + rng.normal(0, 0.3, n) + split_shift
    dsh = 45.0 + 0.4 * (oat - 85) - 0.3 * (rat - 76) + rng.normal(0, 1.2, n) + dsh_shift
    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.RETURN_AIR_TEMP: rat,
            Role.SUPPLY_AIR_TEMP: rat - split,
            Role.RETURN_AIR_DEWPOINT_TEMP: dew,
            Role.SUBCOOLING_TEMP: sc,
            Role.SUPERHEAT_TEMP: sh,
            Role.DISCHARGE_SUPERHEAT_TEMP: dsh,
        },
        index=idx,
    )


# ------------------------------------------------------------------ charge
def test_charge_baseline_mode_detects_both_directions():
    base = _unit(seed=1)
    low = DXRefrigerantCharge(BaselineStore()).analyze_periods(
        "HP1", base, _unit(seed=2, sc_shift=-6.0, sh_shift=8.0, start="2025-08-01")
    )
    assert low.severity == "fault"
    assert low.metrics["charge_verdict"] == "undercharge"
    assert low.metrics["superheat_corroborates"] is True
    high = DXRefrigerantCharge(BaselineStore()).analyze_periods(
        "HP1", base, _unit(seed=3, sc_shift=8.0, start="2025-08-01")
    )
    assert high.severity == "fault" and high.metrics["charge_verdict"] == "overcharge"
    ok = DXRefrigerantCharge(BaselineStore()).analyze_periods(
        "HP1", base, _unit(seed=4, start="2025-08-01")
    )
    assert ok.severity == "ok" and ok.metrics["charge_verdict"] == "charge_ok"
    assert ok.metrics["covariate"] == "return_air_temp"


def test_charge_baseline_is_frozen_and_reused():
    store = BaselineStore()
    rule = DXRefrigerantCharge(store, site="s")
    rule.analyze_periods("HP1", _unit(seed=1), _unit(seed=2, start="2025-08-01"))
    rec = store.get("s", "HP1", "dx_subcooling")
    assert rec is not None and rec.kind == "dx_subcooling"
    # a later run with a faulty "baseline" window still scores against the frozen reference
    f = rule.analyze_periods(
        "HP1", _unit(seed=5, sc_shift=-6.0), _unit(seed=6, sc_shift=-6.0, start="2025-09-01")
    )
    assert f.metrics["charge_verdict"] == "undercharge"
    frozen_only = DXRefrigerantCharge(BaselineStore(), freeze_if_missing=False)
    d = frozen_only.analyze_periods("HP2", _unit(seed=1), _unit(seed=2))
    assert d.severity == "info" and d.metrics["declined"]


def test_charge_declines_honestly():
    rule = DXRefrigerantCharge(BaselineStore())
    no_sc = _unit().drop(columns=[Role.SUBCOOLING_TEMP])
    assert rule.analyze_periods("HP1", no_sc, no_sc).metrics["reason"] == "subcooling_not_mapped"
    assert rule.analyze("HP1", no_sc).metrics["reason"] == "subcooling_not_mapped"
    no_oat = _unit().drop(columns=[Role.OAT])
    assert rule.analyze_periods("HP1", no_oat, no_oat).metrics["reason"] == "no_normalizer"
    short = _unit(n=12)
    assert (
        DXRefrigerantCharge(BaselineStore()).analyze_periods("HP1", short, short).metrics["reason"]
        == "no_baseline"
    )
    no_sh = _unit().drop(columns=[Role.SUPERHEAT_TEMP])
    f = DXRefrigerantCharge(BaselineStore()).analyze_periods(
        "HP1", no_sh, _unit(seed=2, sc_shift=-6.0).drop(columns=[Role.SUPERHEAT_TEMP])
    )
    assert f.metrics["superheat_drift_f"] is None
    assert any("superheat not mapped" in c for c in f.caveats)


def test_charge_target_mode_and_per_equipment_targets():
    frame = _unit(seed=1, sc_shift=-5.0)  # median ~4 degF subcooling
    targets = {"HP_A*": {"subcooling_f": 9.0, "superheat_f": 10.0}, "HP_B*": {"subcooling_f": 4.0}}
    rule = DXRefrigerantCharge(targets=targets)
    a = rule.analyze("HP_A1", frame)
    assert a.severity == "warn" and a.metrics["charge_verdict"] == "undercharge"
    assert a.metrics["superheat_corroborates"] is True
    b = rule.analyze("HP_B1", frame)
    assert b.severity == "ok"
    far = rule.analyze("HP_A1", _unit(seed=1, sc_shift=10.0))
    assert far.severity == "fault" and far.metrics["charge_verdict"] == "overcharge"
    # a single target dict applies to every unit; a unit no pattern matches gets the limits
    one = DXRefrigerantCharge(targets={"subcooling_f": 9.0, "tolerance_f": 6.0})
    assert one.analyze("X", frame).severity == "ok"
    lim = rule.analyze("HP_C1", frame)
    assert lim.metrics["mode"] == "limits" and lim.severity == "ok"
    too_few = DXRefrigerantCharge(targets={"subcooling_f": 9.0}).analyze("X", _unit(n=5))
    assert too_few.metrics["reason"] == "too_few_samples"


def test_charge_limits_mode_without_a_target():
    rule = builtin_registry().get("dx_refrigerant_charge")
    flash = rule.analyze("HP1", _unit(sc_shift=-8.5))
    assert flash.severity == "warn" and flash.metrics["charge_verdict"] == "undercharge"
    backed = rule.analyze("HP1", _unit(sc_shift=20.0))
    assert backed.severity == "warn" and backed.metrics["charge_verdict"] == "overcharge"
    assert rule.analyze("HP1", _unit()).severity == "ok"
    no_sh = rule.analyze("HP1", _unit().drop(columns=[Role.SUPERHEAT_TEMP]))
    assert no_sh.metrics["superheat_median_f"] is None


def test_charge_superheat_metric_for_a_fixed_orifice():
    rule = DXRefrigerantCharge(metric="superheat", targets={"superheat_f": 12.0})
    assert rule.roles_required == (Role.SUPERHEAT_TEMP,)
    f = rule.analyze("HP1", _unit(sh_shift=8.0))
    assert f.metrics["charge_verdict"] == "undercharge"  # high superheat on an orifice: low charge
    no_tgt = DXRefrigerantCharge(metric="superheat").analyze("HP1", _unit())
    assert no_tgt.metrics["reason"] == "no_target"
    with pytest.raises(ValueError):
        DXRefrigerantCharge(metric="pressure")
    b = DXRefrigerantCharge(BaselineStore(), metric="superheat").analyze_periods(
        "HP1", _unit(seed=1), _unit(seed=2, sh_shift=10.0)
    )
    assert b.metrics["charge_verdict"] == "undercharge"


def test_charge_heating_rows_are_ignored():
    frame = _unit(sc_shift=-8.0)
    frame[Role.REVERSING_VALVE_CMD] = 1.0  # all heating: nothing to judge
    f = DXRefrigerantCharge(targets={"subcooling_f": 9.0}).analyze("HP1", frame)
    assert f.metrics["reason"] == "too_few_samples"
    frame[Role.REVERSING_VALVE_CMD] = 0.0
    frame[Role.COMPRESSOR_STATUS] = 0.0  # compressor off
    assert (
        DXRefrigerantCharge(targets={"subcooling_f": 9.0}).analyze("HP1", frame).metrics["reason"]
        == "too_few_samples"
    )


def test_charge_on_a_water_cooled_machine_normalizes_on_tons():
    n = 300
    idx = pd.date_range("2025-06-01", periods=n, freq="1h")
    rng = np.random.default_rng(0)
    load = 0.4 + 0.6 * rng.random(n)
    frame = pd.DataFrame(
        {
            Role.CHW_FLOW: 20.0 + 0.0 * load,
            Role.CHW_RETURN_TEMP: 44.0 + 10.0 * load,
            Role.CHW_SUPPLY_TEMP: np.full(n, 44.0),
            Role.CW_SUPPLY_TEMP: 80.0 + 5.0 * rng.random(n),
            Role.SUBCOOLING_TEMP: 8.0 + 2.0 * load + rng.normal(0, 0.3, n),
        },
        index=idx,
    )
    cur = frame.copy()
    cur[Role.SUBCOOLING_TEMP] -= 6.0
    f = DXRefrigerantCharge(BaselineStore()).analyze_periods("CH1", frame, cur)
    assert f.metrics["normalized_on"] == "tons"
    assert f.metrics["charge_verdict"] == "undercharge"


# ------------------------------------------------------------------ airflow
def test_airflow_baseline_mode_on_dew_point():
    base = _unit(seed=1)
    low = DXIndoorAirflow(BaselineStore()).analyze_periods(
        "HP1", base, _unit(seed=2, split_shift=4.0)
    )
    assert low.severity == "fault" and low.metrics["airflow_verdict"] == "airflow_low"
    assert low.metrics["covariate"] == "return_air_dewpoint_temp"
    high = DXIndoorAirflow(BaselineStore()).analyze_periods(
        "HP1", base, _unit(seed=3, split_shift=-2.5)
    )
    assert high.severity in ("warn", "fault") and high.metrics["airflow_verdict"] == "airflow_high"
    ok = DXIndoorAirflow(BaselineStore()).analyze_periods("HP1", base, _unit(seed=4))
    assert ok.severity == "ok"


def test_airflow_narrow_split_with_lost_subcooling_reads_as_charge():
    base = _unit(seed=1)
    cur = _unit(seed=2, split_shift=-3.0, sc_shift=-6.0)
    f = DXIndoorAirflow(BaselineStore()).analyze_periods("HP1", base, cur)
    assert f.severity == "info" and f.metrics["airflow_verdict"] == "capacity_low"
    assert f.metrics["attribution"] == "refrigerant_circuit"


def test_airflow_without_humidity_falls_back_to_oat_and_says_so():
    base = _unit(seed=1).drop(columns=[Role.RETURN_AIR_DEWPOINT_TEMP, Role.SUBCOOLING_TEMP])
    cur = _unit(seed=2).drop(columns=[Role.RETURN_AIR_DEWPOINT_TEMP, Role.SUBCOOLING_TEMP])
    f = DXIndoorAirflow(BaselineStore()).analyze_periods("HP1", base, cur)
    assert any("dew point" in c for c in f.caveats)
    assert f.metrics["subcooling_drift_f"] is None
    # return RH is converted to a dew point
    base[Role.RETURN_AIR_HUMIDITY] = 50.0
    cur[Role.RETURN_AIR_HUMIDITY] = 50.0
    g = DXIndoorAirflow(BaselineStore()).analyze_periods("HP1", base, cur)
    assert not any("dew point or humidity" in c for c in g.caveats)


def test_return_dewpoint_formula():
    # 75 degF / 50 % RH -> ~55 degF dew point (psychrometric chart)
    assert float(return_dewpoint_f(pd.Series([75.0]), pd.Series([50.0])).iloc[0]) == pytest.approx(
        55.1, abs=0.5
    )


def test_airflow_target_and_limits_modes():
    frame = _unit(seed=1, split_shift=6.0)  # ~24 degF split
    t = DXIndoorAirflow(targets={"temp_split_f": 16.0}).analyze("HP1", frame)
    assert t.severity == "fault" and t.metrics["airflow_verdict"] == "airflow_low"
    assert DXIndoorAirflow(targets={"temp_split_f": 24.0}).analyze("HP1", frame).severity == "ok"
    lim = DXIndoorAirflow()
    assert lim.analyze("HP1", _unit(split_shift=13.0)).metrics["airflow_verdict"] == "airflow_low"
    assert lim.analyze("HP1", _unit(split_shift=-12.0)).metrics["airflow_verdict"] == "split_low"
    assert lim.analyze("HP1", _unit()).severity == "ok"
    missing = _unit().drop(columns=[Role.SUPPLY_AIR_TEMP])
    assert lim.analyze("HP1", missing).metrics["reason"] == "not_mapped"
    assert lim.analyze_periods("HP1", missing, missing).metrics["reason"] == "not_mapped"
    assert lim.analyze("HP1", _unit(n=4)).metrics["reason"] == "too_few_samples"
    short = _unit(n=12)
    assert (
        DXIndoorAirflow(BaselineStore()).analyze_periods("HP1", short, short).metrics["reason"]
        == "no_baseline"
    )


# ------------------------------------------------------------------ family / registry
def test_dx_family_suite_and_rollup():
    assert "dx" in DRIFT_FAMILIES
    suite = build_drift_suite("dx", BaselineStore())
    assert tuple(r.name for r in suite) == DX_DETECTORS
    base, cur = _unit(seed=1), _unit(seed=2, sc_shift=-6.0, start="2025-08-01")
    fs = [r.analyze_periods("HP1", base, cur) for r in suite]
    d = diagnose_dx_drift(fs, equip="HP1")
    assert d.family == "dx" and d.locus == "refrigerant_circuit" and d.severity == "fault"
    quiet = [
        r.analyze_periods("HP1", base, _unit(seed=3))
        for r in build_drift_suite("dx", BaselineStore())
    ]
    assert diagnose_dx_drift(quiet).locus == "steady"
    declined = Finding("dx_refrigerant_charge", "HP1", "info", {"declined": True, "reason": "x"})
    assert diagnose_dx_drift([declined]).locus == "unknown"
    odd = Finding("dx_indoor_airflow", "HP1", "warn", {"attribution": "somewhere"})
    assert diagnose_dx_drift([odd]).locus == "unknown"


def test_registry_classes_and_promoted_scenarios():
    reg = builtin_registry()
    for name in (
        "dx_refrigerant_charge",
        "dx_indoor_airflow",
        "hp_mode_vs_need",
        "hp_capacity_shortfall",
        "hp_room_imbalance",
        "source_loop_deltat",
    ):
        assert name in reg.names() and name in RULE_EQUIP_CLASSES
    assert make_rule("dx_refrigerant_charge", targets={"subcooling_f": 9}).targets
    assert equip_family("SPLIT_SYSTEM") == "dx" and equip_family("GEO_LOOP") == "source_loop"
    # promoted from PENDING_SCENARIOS to the gated SCENARIOS at the 0.93 sign-off
    new = (
        "dx_refrigerant_charge",
        "dx_indoor_airflow",
        "hp_mode_vs_need",
        "hp_capacity_shortfall",
        "source_loop_deltat",
    )
    assert not set(new) & set(faultlab.PENDING_SCENARIOS)
    sc = {n: faultlab.SCENARIOS[n] for n in new}
    rep = benchmark(faultlab.labeled_records(scenarios=sc), faultlab.targets(sc))
    for name, c in rep.per_detector.items():
        assert c.true_positive_rate == 1.0 and c.false_positive_rate == 0.0, name
    # none of them fires on another rule's scenario
    xf = faultlab.cross_fire()
    assert not any(set(new) & set(v) for k, v in xf.items() if k not in new)
