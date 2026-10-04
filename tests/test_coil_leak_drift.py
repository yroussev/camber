"""0.100 (#100): the opt-in coil-valve leak drift detector (``coil_leak_drift``).

The detector fits a coil's valve-shut air rise (leaving minus mixed air) against the mixed air in a
known-good baseline and flags a shift in the current window: down for a cooling leak, up for a
heating leak. It is opt-in (an ``ahu`` drift-family entry names ``coil_leak``), so every default
suite, config and output is unchanged.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ahudrift import diagnose_ahu_drift  # noqa: E402
from camber.config import run_config, run_drift_config  # noqa: E402
from camber.driftrun import COIL_LEAK_COILS, build_drift_suite, family_names  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules import param_docs as pd_docs  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.builtin import builtin_registry, rule_names  # noqa: E402
from camber.rules.coil_leak_rule import CoilLeakDrift  # noqa: E402
from camber.scorecard import RULE_CATEGORY  # noqa: E402
from camber.store import ParquetStore  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402

_FID = "ds-test-ahu"


def _ahu(
    *,
    days=60,
    start="2026-01-01",
    seed=0,
    fan_heat=1.0,
    leak_f=0.0,
    heat_leak_f=0.0,
    heating=False,
    coil_leaving=False,
):
    """Hourly AHU: valves shut 2/3 of the hours, where SAT = MAT + fan heat (+ a leak's shift)."""
    idx = pd.date_range(start, periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    n = len(idx)
    mat = 58 + 6 * np.sin(np.arange(n) / 24 * 2 * np.pi) + rng.normal(0, 1.0, n)
    cool = np.where(np.arange(n) % 3 == 0, 40.0, 0.0)  # open one hour in three
    shut = cool == 0.0
    rise = np.where(shut, fan_heat - leak_f + heat_leak_f, -12.0) + rng.normal(0, 0.15, n)
    f = pd.DataFrame(
        {
            Role.COOL_VALVE: cool,
            Role.MIXED_AIR_TEMP: mat,
            Role.SUPPLY_AIR_TEMP: mat + rise,
            Role.SUPPLY_FAN_STATUS: np.ones(n),
        },
        index=idx,
    )
    if heating:
        f[Role.HEAT_VALVE] = 0.0
    if coil_leaving:
        # the coil's own leaving air: no fan heat (draw-through fan downstream)
        f[Role.COOL_COIL_LEAVING_TEMP] = mat + (rise - fan_heat)
    return f


def _rule(coil="cooling", **kw):
    return CoilLeakDrift(BaselineStore(), site="S", run_id="r", coil=coil, **kw)


# --------------------------------------------------------------------------- the detector


def test_not_registered_and_coil_validated():
    assert "coil_leak_drift" not in rule_names()
    assert "coil_leak_drift" not in builtin_registry().names()
    with pytest.raises(ValueError):
        CoilLeakDrift(BaselineStore(), coil="reheat")


def test_cooling_leak_pulls_the_rise_down():
    f = _rule().analyze_periods("AHU-1", _ahu(), _ahu(seed=1, leak_f=1.1))
    assert f.rule == "coil_leak_drift" and f.severity in ("warn", "fault")
    m = f.metrics
    assert m["coil_leak_drift_direction"] == "down" and m["coil_leak_drift_f"] < -0.9
    assert m["coil_leak_which"] == "cooling" and m["coil_leak_basis"] == "supply_air_temp"
    assert m["coil_leak_fan_gate"] == "status"
    assert "uncommanded cooling" in f.summary and "vs frozen baseline" in f.summary
    assert any("reference thermometer" in c for c in f.caveats)


def test_healthy_unit_is_quiet():
    f = _rule().analyze_periods("AHU-1", _ahu(), _ahu(seed=1))
    assert f.severity == "ok" and abs(f.metrics["coil_leak_drift_f"]) < 0.2


def test_one_sided_a_rise_is_not_a_cooling_leak():
    f = _rule().analyze_periods("AHU-1", _ahu(), _ahu(seed=1, heat_leak_f=3.0))
    assert f.severity == "ok" and f.metrics["coil_leak_drift_direction"] == "up"
    assert "not the direction" in f.summary


def test_heating_instance_flags_a_rise():
    base, cur = _ahu(heating=True), _ahu(seed=1, heating=True, heat_leak_f=3.0)
    f = _rule("heating").analyze_periods("AHU-1", base, cur)
    assert f.severity == "fault" and f.metrics["coil_leak_which"] == "heating"
    assert "uncommanded heating" in f.summary
    # the cooling instance reads the same rise and stays quiet
    assert _rule().analyze_periods("AHU-1", base, cur).severity == "ok"


def test_heating_needs_an_allowed_basis():
    base = _ahu(heating=True)
    f = _rule("heating", judge_heating_on_supply_air=False).analyze_periods("AHU-1", base, base)
    assert f.severity == "info" and f.metrics["reason"] == "no_heating_coil_basis"


def test_coil_leaving_sensor_is_the_basis_and_must_match_the_baseline():
    base, cur = _ahu(coil_leaving=True), _ahu(seed=1, coil_leaving=True, leak_f=2.0)
    f = _rule().analyze_periods("AHU-1", base, cur)
    assert f.metrics["coil_leak_basis"] == "cool_coil_leaving_temp" and f.severity == "fault"
    mism = _rule().analyze_periods("AHU-1", _ahu(), cur)
    assert mism.severity == "info" and mism.metrics["reason"] == "basis_mismatch"
    off = _rule(use_coil_leaving=False).analyze_periods("AHU-1", base, cur)
    assert off.metrics["coil_leak_basis"] == "supply_air_temp"


def test_declines_are_loud():
    cur = _ahu().drop(columns=[Role.MIXED_AIR_TEMP])
    f = _rule().analyze_periods("AHU-1", _ahu(), cur)
    assert f.metrics["reason"] == "coil_leak_inputs_not_mapped"
    no_freeze = _rule(freeze_if_missing=False).analyze_periods("AHU-1", _ahu(), _ahu())
    assert no_freeze.severity == "info" and no_freeze.metrics["declined"]
    always_open = _ahu()
    always_open[Role.COOL_VALVE] = 50.0
    f = _rule().analyze_periods("AHU-1", _ahu(), always_open)
    assert f.metrics["declined"] and "nothing scoreable" in f.summary


def test_no_fan_signal_is_caveated():
    base, cur = _ahu(), _ahu(seed=1)
    base = base.drop(columns=[Role.SUPPLY_FAN_STATUS])
    cur = cur.drop(columns=[Role.SUPPLY_FAN_STATUS])
    f = _rule().analyze_periods("AHU-1", base, cur)
    assert f.metrics["coil_leak_fan_gate"] is None
    assert any("no fan status or speed" in c for c in f.caveats)


def test_occupied_only_reads_the_trended_occupancy():
    base, cur = _ahu(), _ahu(seed=1, leak_f=1.5)
    for fr in (base, cur):
        fr[Role.OCCUPANCY] = (fr.index.hour >= 6) & (fr.index.hour < 20)
    f = _rule(occupied_only=True).analyze_periods("AHU-1", base, cur)
    assert f.metrics["coil_leak_occupancy_gate"] == "trended occupancy"
    assert f.severity in ("warn", "fault")
    assert "coil_leak_occupancy_gate" not in _rule().analyze_periods("A", base, cur).metrics


def test_evidence_hooks():
    rule = _rule()
    kind, x, y = rule.drift_signature()
    assert kind == "coil_leak_cool" and set(rule.drift_frame(_ahu()).columns) == {x, y}


# --------------------------------------------------------------------------- opt-in wiring


def test_default_suites_are_unchanged_and_coil_leak_appends():
    for fam in family_names():
        plain = [type(r).__name__ for r in build_drift_suite(fam, BaselineStore())]
        assert "CoilLeakDrift" not in plain, fam
    plain = build_drift_suite("ahu", BaselineStore())
    leak = build_drift_suite("ahu", BaselineStore(), coil_leak=COIL_LEAK_COILS)
    assert [type(r).__name__ for r in leak[: len(plain)]] == [type(r).__name__ for r in plain]
    assert [(r.name, r.coil) for r in leak[len(plain) :]] == [
        ("coil_leak_drift", "cooling"),
        ("coil_leak_drift", "heating"),
    ]
    tuned = build_drift_suite(
        "ahu", BaselineStore(), coil_leak=("cooling",), coil_leak_params={"warn_sigma": 3.0}
    )
    assert tuned[-1].warn_sigma == 3.0
    with pytest.raises(ValueError):
        build_drift_suite("chiller", BaselineStore(), coil_leak=("cooling",))
    with pytest.raises(ValueError):
        build_drift_suite("ahu", BaselineStore(), coil_leak=("reheat",))
    assert RULE_CATEGORY["coil_leak_drift"] == "maintenance"


def test_diagnosis_puts_a_leak_on_the_coil_side():
    leak = Finding(
        rule="coil_leak_drift",
        equip="AHU-1",
        severity="warn",
        summary="",
        metrics={"coil_leak_which": "cooling", "coil_leak_drift_f": -1.1},
    )
    d = diagnose_ahu_drift([leak])
    assert d.locus == "coil" and d.severity == "warn"
    assert d.signals["coil_leak_drift:cooling"]["side"] == "coil"
    assert "passing water" in d.causes[0]
    steady = Finding(rule="coil_leak_drift", equip="AHU-1", severity="ok", summary="", metrics={})
    assert diagnose_ahu_drift([steady]).locus == "steady"


def _store(root, frames: dict) -> str:
    st = ParquetStore(root)
    for equip, frame in frames.items():
        st.write_role_frame(frame, facility_id=_FID, equip=equip, equip_class="AHU")
    st.register_facility(_FID, name="test ahu")
    return root


def _config(root, families) -> dict:
    return {
        "source": {"kind": "store", "store": root, "facility_id": _FID},
        "resample": "1h",
        "equipment": [{"class": "AHU", "marker_role": "mixed_air_temp"}],
        "rules": [],
        "drift": {"families": families},
    }


def test_config_twin_reference_and_period_reference(tmp_path):
    root = _store(
        str(tmp_path / "store"),
        {
            "AHU__fault_free": _ahu(),
            "AHU__healthy": _ahu(seed=1),
            "AHU__leak": _ahu(seed=2, leak_f=1.2),
            "AHU__onset": pd.concat(
                [_ahu(days=30, seed=3), _ahu(days=30, start="2026-01-31", seed=4, leak_f=1.2)]
            ),
        },
    )
    twin = {
        "class": "AHU",
        "family": "ahu",
        "reference": {"equip": "AHU__fault_free"},
        "coil_leak": ["cooling"],
    }
    res = run_config(_config(root, [twin]), base_dir=str(tmp_path))
    got = {f.equip: f for f in res.findings if f.rule == "coil_leak_drift"}
    assert got["AHU__leak"].severity in ("warn", "fault")
    assert "vs the reference AHU__fault_free" in got["AHU__leak"].summary
    assert got["AHU__healthy"].severity == "ok"
    assert got["AHU__fault_free"].metrics["reason"] == "is_reference"
    diag = {d.equip: d for d in res.drift.families[0].diagnoses}
    assert diag["AHU__leak"].locus == "coil"
    assert not any(p.endswith(".json") for p in os.listdir(tmp_path))  # nothing persisted

    period = {
        "class": "AHU",
        "family": "ahu",
        "reference": {"period": ["2026-01-01", "2026-01-30 23:00"]},
        "coil_leak": ["cooling"],
    }
    res = run_drift_config(_config(root, [period]), base_dir=str(tmp_path))
    f = {x.equip: x for x in res.findings if x.rule == "coil_leak_drift"}["AHU__onset"]
    assert f.severity in ("warn", "fault") and f.metrics["baseline_source"].startswith("period:")


def test_config_without_the_key_runs_no_leak_detector(tmp_path):
    root = _store(str(tmp_path / "store"), {"A": _ahu(), "B": _ahu(seed=1, leak_f=2.0)})
    fam = {"class": "AHU", "family": "ahu", "reference": {"equip": "A"}}
    res = run_config(_config(root, [fam]), base_dir=str(tmp_path))
    assert not any(f.rule == "coil_leak_drift" for f in res.findings)


@pytest.mark.parametrize(
    "extra,msg",
    [
        ({"coil_leak": "cooling"}, "non-empty list"),
        ({"coil_leak": []}, "non-empty list"),
        ({"coil_leak": ["reheat"]}, "unknown coil_leak"),
        ({"coil_leak": ["cooling"], "coil_leak_params": {"bogus": 1}}, "invalid coil_leak_params"),
        ({"coil_leak": ["cooling"], "coil_leak_params": {"coil": "heating"}}, "may not set"),
        ({"coil_leak_params": {"warn_f": 1.0}}, "non-empty list"),
    ],
)
def test_config_validation(tmp_path, extra, msg):
    root = _store(str(tmp_path / "store"), {"A": _ahu(), "B": _ahu(seed=1)})
    fam = {"class": "AHU", "family": "ahu", "reference": {"equip": "A"}, **extra}
    with pytest.raises(ValueError, match=msg):
        run_drift_config(_config(root, [fam]), base_dir=str(tmp_path))


# --------------------------------------------------------------------------- parameter docs


def test_every_tunable_parameter_is_documented():
    assert pd_docs.documented_drift_rules() == ["coil_leak_drift"]
    for rule in pd_docs.documented_drift_rules():
        params = pd_docs.drift_rule_params(rule)
        names = {p.name for p in params}
        for p in params:
            assert p.doc is not None, f"{rule}.{p.name}: no DRIFT_PARAM_DOCS entry"
            d = p.doc
            assert d.basis.startswith(pd_docs.BASIS_KINDS), (rule, p.name)
            assert d.unit and d.calibrate and d.range, (rule, p.name)
            if not d.is_choice and isinstance(p.default, (int, float)):
                assert d.range[0] <= p.default <= d.range[-1], (rule, p.name)
        for name in pd_docs.DRIFT_PARAM_DOCS[rule]:
            assert name in names, f"{rule}.{name}: documented but not a constructor parameter"
        for name in pd_docs.DRIFT_EXEMPT[rule]:
            assert name not in names and name not in pd_docs.DRIFT_PARAM_DOCS[rule]


def test_mixed_air_outside_the_baseline_range_is_not_extrapolated():
    cold = _ahu(seed=1)
    cold[Role.SUPPLY_AIR_TEMP] -= 25.0
    cold[Role.MIXED_AIR_TEMP] -= 25.0  # same rise, 25 F colder entering air
    f = _rule().analyze_periods("AHU-1", _ahu(), cold)
    assert f.severity == "info" and f.metrics["declined"]
    assert f.metrics["coil_leak_n_out_of_scope"] > 0
    assert any("outside it" in c for c in f.caveats)
    half = pd.concat(
        [
            _ahu(days=30, seed=2),
            cold.iloc[: 30 * 24].set_axis(pd.date_range("2026-01-31", periods=30 * 24, freq="1h")),
        ]
    )
    g = _rule().analyze_periods("AHU-1", _ahu(), half)
    assert g.severity == "ok" and g.metrics["coil_leak_n_out_of_scope"] > 0
    assert any("were not judged" in c for c in g.caveats)


# --------------------------------------------------------------------------- the validation script


def _leak_script():
    import importlib.util

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "examples", "lbnl_fdd", "leak_drift.py")
    spec = importlib.util.spec_from_file_location("leak_drift", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_validation_groups_follow_the_physics():
    L = _leak_script()
    sd, fcu = L.UNITS["sdahu"], L.UNITS["fcu"]
    assert L.group_of(sd, "AHU_annual.csv") == ("negative", None)
    assert L.group_of(sd, "coi_leakage_010_annual.csv") == ("positive", "cooling")
    assert L.group_of(sd, "coi_leakage_025_annual.csv")[0] is None  # a byte-identical copy
    assert L.group_of(sd, "coi_stuck_010_annual.csv")[0] == "same_symptom"
    assert L.group_of(sd, "coi_bias_-2_annual.csv")[0] == "confound"
    assert L.group_of(sd, "oa_bias_2_annual.csv")[0] is None
    assert L.group_of(fcu, "FCU_VLVLeak_Heating_50.csv") == ("positive", "heating")
    assert L.group_of(fcu, "FCU_VLVStuck_Cooling_0.csv")[0] == "negative"  # stuck closed
    assert L.group_of(fcu, "FCU_VLVStuck_Cooling_20.csv")[0] == "same_symptom"
    assert L.group_of(fcu, "FCU_Control_CoolingReverse.csv")[0] == "same_symptom"


def test_validation_modes_on_synthetic_runs():
    L = _leak_script()
    unit = dict(L.UNITS["sdahu"])
    year = dict(days=365, start="2018-01-01")
    frames = {
        "AHU_annual.csv": _ahu(**year),
        "coi_leakage_010_annual.csv": _ahu(seed=1, leak_f=1.2, **year),
        "damper_stuck_075_annual.csv": _ahu(seed=2, **year),
        "coi_bias_-2_annual.csv": _ahu(seed=3, leak_f=3.6, **year),
    }
    for mode, negatives in (("split", 2), ("twin", 1), ("onset", 2)):
        res = L.score_unit(frames, unit, mode)
        c = res["confusion"]
        assert (c.tp, c.fn, c.fp, c.tn) == (1, 0, 0, negatives), mode
        conf = [r for r in res["rows"] if r["group"] == "confound"]
        assert conf and conf[0]["fired"] == ["cooling"]  # reported, never scored
    spliced = L.splice(frames["AHU_annual.csv"], frames["coi_leakage_010_annual.csv"])
    assert len(spliced) == len(frames["AHU_annual.csv"]) and spliced.index.is_monotonic_increasing
    keys = L.report({"sdahu": {"split": L.score_unit(frames, unit, "split")}})
    assert keys["leak_drift.sdahu.split.tpr"] == 1.0 and keys["leak_drift.sdahu.split.fpr"] == 0.0
