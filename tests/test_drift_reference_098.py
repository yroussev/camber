"""0.98 (#86 items 1 and 5, decision S4): a declared, never-persisted drift reference.

``drift.families[].reference`` scores each equipment against another, known-healthy one
(``{"equip": ...}``) or against a known-good period of its own (``{"period": [start, end]}``). The
baseline is fitted in a scratch store on every run and never saved, so a run still cannot mint the
baseline it scores against; the reference equipment itself declines as ``is_reference``.
"""

import importlib.util
import json
import os

import numpy as np
import pandas as pd
import pytest

from camber.config import drift_refit, run_config, run_drift_config
from camber.driftrun import run_drift
from camber.model.roles import Role
from camber.report.drift import drift_report_html
from camber.store import ParquetStore

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FID = "ds-test-boiler"


def _boiler(*, eff=0.80, days=60, start="2026-01-01", seed=0, drop=()):
    """Hourly heating-season boiler: heat ~ (60 - OAT), gas = heat / efficiency."""
    idx = pd.date_range(start, periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    oat = 35 + 15 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi) + rng.normal(0, 3, len(idx))
    heat_kw = np.clip(12.0 * (60 - oat), 0, None) * (1 + rng.normal(0, 0.02, len(idx)))
    gpm = 150.0 + rng.normal(0, 2, len(idx))
    sup = 170.0 + rng.normal(0, 0.3, len(idx))
    f = pd.DataFrame(
        {
            Role.GAS_INPUT_RATE: np.where(heat_kw > 0, heat_kw / eff, 0.0),
            Role.HW_SUPPLY_TEMP: sup,
            Role.HW_RETURN_TEMP: sup - heat_kw * 3412.14 / (500 * gpm),
            Role.HW_FLOW: gpm,
            Role.OAT: oat,
        },
        index=idx,
    )
    return f.drop(columns=list(drop))


def _store(root, frames: dict) -> str:
    st = ParquetStore(root)
    for equip, frame in frames.items():
        st.write_role_frame(frame, facility_id=_FID, equip=equip, equip_class="HW_PLANT")
    st.register_facility(_FID, name="test boiler plant")
    return root


def _config(root, families, **drift) -> dict:
    return {
        "source": {"kind": "store", "store": root, "facility_id": _FID},
        "resample": "1h",
        "equipment": [{"class": "HW_PLANT", "marker_role": "hw_supply_temp"}],
        "rules": [],
        "drift": {"families": families, **drift},
    }


def _plant(tmp_path):
    return _store(
        str(tmp_path / "store"),
        {
            "PLANT__fault_free": _boiler(),
            "PLANT__healthy": _boiler(seed=1),
            "PLANT__fouled": _boiler(eff=0.68, seed=2),
        },
    )


_REF = {"class": "HW_PLANT", "family": "boiler", "reference": {"equip": "PLANT__fault_free"}}


def _by_equip(findings, rule="boiler_efficiency_drift") -> dict:
    return {f.equip: f for f in findings if f.rule == rule}


def test_reference_equip_scores_the_others_without_a_store(tmp_path):
    root = _plant(tmp_path)
    res = run_config(_config(root, [_REF]), base_dir=str(tmp_path))
    got = _by_equip(res.findings)
    assert got["PLANT__fouled"].severity == "fault"
    assert "vs the reference PLANT__fault_free" in got["PLANT__fouled"].summary
    assert "frozen" not in got["PLANT__fouled"].summary
    assert got["PLANT__healthy"].severity == "ok"
    ref = got["PLANT__fault_free"]
    assert ref.severity == "info" and ref.metrics["declined"] is True
    assert ref.metrics["reason"] == "is_reference"
    for f in got.values():
        assert f.metrics["baseline_source"] == "reference:PLANT__fault_free"
        assert any("declared reference PLANT__fault_free" in c for c in f.caveats)
    fam = res.drift.families[0]
    assert fam.reference == {"equip": "PLANT__fault_free"}
    assert fam.baseline == (None, None) and fam.current == (None, None)
    assert [r["reason"] for r in fam.unevaluated] == ["is the declared reference"]
    assert {d.equip for d in fam.diagnoses} == {"PLANT__fouled", "PLANT__healthy"}
    assert fam.as_dict()["reference"] == {"equip": "PLANT__fault_free"}
    assert "drift:HW_PLANT:boiler" in res.rules_run
    # nothing was written next to the config: the reference is never persisted
    assert not any(p.endswith(".json") for p in os.listdir(tmp_path))
    html = drift_report_html(res.drift)
    assert "the declared reference PLANT__fault_free" in html and "never stored" in html


def test_reference_period_of_the_same_equipment(tmp_path):
    healthy, fouled = _boiler(days=60), _boiler(eff=0.68, days=60, start="2026-03-02", seed=4)
    root = _store(str(tmp_path / "store"), {"B1": pd.concat([healthy, fouled])})
    fam = {
        "class": "HW_PLANT",
        "family": "boiler",
        "reference": {"period": ["2026-01-01", "2026-02-28"]},
    }
    res = run_drift_config(_config(root, [fam]), base_dir=str(tmp_path))
    f = _by_equip(res.findings)["B1"]
    assert f.severity == "fault"
    assert f.metrics["baseline_source"] == "period:2026-01-01..2026-02-28"
    assert any("known-good period" in c for c in f.caveats)
    assert "vs the known-good period" in f.summary
    # the current window defaults to everything from the end of the reference period on
    assert res.families[0].current == ("2026-02-28", None)
    assert "known-good period" in drift_report_html(res)


def test_reference_equip_with_its_own_period_and_explicit_current(tmp_path):
    root = _plant(tmp_path)
    fam = dict(_REF, reference={"equip": "PLANT__fault_free", "period": ["2026-01-01", None]})
    cfg = _config(root, [fam], current=["2026-01-15", "2026-02-15"])
    res = run_drift_config(cfg, base_dir=str(tmp_path))
    f = _by_equip(res.findings)["PLANT__fouled"]
    assert f.severity == "fault"
    assert f.metrics["baseline_source"] == "reference:PLANT__fault_free@2026-01-01.."
    assert res.families[0].current == ("2026-01-15", "2026-02-15")


def test_a_reference_that_cannot_serve_declines_every_target(tmp_path):
    root = _store(
        str(tmp_path / "store"),
        {
            "PLANT__fault_free": _boiler(drop=(Role.GAS_INPUT_RATE,)),
            "PLANT__fouled": _boiler(eff=0.68, seed=2),
        },
    )
    res = run_drift_config(_config(root, [_REF]), base_dir=str(tmp_path))
    f = _by_equip(res.findings)["PLANT__fouled"]
    assert f.severity == "info" and f.metrics["reason"] == "reference_missing_inputs"
    assert res.families[0].diagnoses == []  # declines only: not evaluated, never "steady"


def test_an_empty_reference_window_declines(tmp_path):
    root = _plant(tmp_path)
    fam = dict(_REF, reference={"equip": "PLANT__fault_free", "period": ["2030-01-01", None]})
    res = run_drift_config(_config(root, [fam]), base_dir=str(tmp_path))
    f = _by_equip(res.findings)["PLANT__fouled"]
    assert f.metrics["reason"] == "empty_reference"


def test_empty_current_window_declines(tmp_path):
    root = _plant(tmp_path)
    cfg = _config(root, [_REF], current=["2030-01-01", "2030-02-01"])
    res = run_drift_config(cfg, base_dir=str(tmp_path))
    f = _by_equip(res.findings)["PLANT__fouled"]
    assert f.metrics["declined"] and f.metrics["empty_periods"] == ["current"]


def test_the_trust_gate_applies_to_the_reference_and_the_targets(tmp_path):
    ff = _boiler()
    ff[Role.HW_SUPPLY_TEMP] = 170.0  # a flatlined sensor: untrusted
    root = _store(str(tmp_path / "store"), {"PLANT__fault_free": ff, "PLANT__x": _boiler(seed=2)})
    cfg = _config(root, [_REF])
    cfg["trust_gate"] = {"min_trust": 0.5}
    res = run_drift_config(cfg, base_dir=str(tmp_path))
    f = _by_equip(res.findings)["PLANT__x"]
    assert f.metrics["reason"] == "reference_untrusted"

    root2 = _store(str(tmp_path / "store2"), {"PLANT__fault_free": _boiler(), "PLANT__x": ff})
    cfg2 = _config(root2, [_REF])
    cfg2["trust_gate"] = {"min_trust": 0.5}
    f2 = _by_equip(run_drift_config(cfg2, base_dir=str(tmp_path)).findings)["PLANT__x"]
    assert f2.metrics["declined"] and "hw_supply_temp" in f2.metrics["untrusted_roles"]


@pytest.mark.parametrize(
    "reference, match",
    [
        ({"equip": "PLANT__nope"}, "was not discovered"),
        ({"equip": "PLANT__fault_free", "window": 1}, "unknown reference key"),
        ({}, "must be an object"),
        ("PLANT__fault_free", "must be an object"),
        ({"period": None}, "neither"),
        ({"period": "2026"}, "pair"),
    ],
)
def test_reference_validation(tmp_path, reference, match):
    root = _plant(tmp_path)
    cfg = _config(root, [dict(_REF, reference=reference)])
    with pytest.raises(ValueError, match=match):
        run_drift_config(cfg, base_dir=str(tmp_path))


def test_freeze_refuses_a_declared_reference(tmp_path):
    root = _plant(tmp_path)
    cfg = _config(root, [_REF])
    with pytest.raises(ValueError, match="never frozen"):
        run_drift_config(cfg, base_dir=str(tmp_path), freeze_if_missing=True)

    from camber.cli import main

    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(cfg))
    assert main(["drift", "freeze", str(path)]) == 1


def test_run_drift_refuses_freezing_a_reference_directly():
    with pytest.raises(ValueError, match="never frozen"):
        run_drift(
            {"HW_PLANT": []},
            None,
            store=None,
            families=[_REF],
            baseline=None,
            current=None,
            freeze_if_missing=True,
        )


def test_a_family_without_a_reference_still_needs_its_windows():
    fam = {"class": "HW_PLANT", "family": "boiler"}
    with pytest.raises(ValueError, match="explicit baseline and current"):
        run_drift({"HW_PLANT": []}, None, store=None, families=[fam], baseline=None, current=None)


def test_a_mixed_section_still_needs_a_store_for_the_stored_family(tmp_path):
    root = _plant(tmp_path)
    cfg = _config(root, [_REF, {"class": "HW_PLANT", "family": "boiler"}])
    with pytest.raises(ValueError, match="drift.store is required"):
        run_drift_config(cfg, base_dir=str(tmp_path))


def test_refit_leaves_reference_families_out(tmp_path):
    root = _plant(tmp_path)
    assert drift_refit(_config(root, [_REF]), base_dir=str(tmp_path)) == {}


def test_cli_drift_run_prints_the_reference(tmp_path, capsys):
    from camber.cli import main

    root = _plant(tmp_path)
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(_config(root, [_REF])))
    assert main(["drift", "run", str(path)]) == 0
    out = capsys.readouterr().out
    assert "reference PLANT__fault_free" in out and "is the declared reference" in out


# --------------------------------------------------------------------------------------------
# Consistency with examples/lbnl_fdd/plant_detectors.py: the config path must give the same
# verdict, run by run, as the example's direct analyze_periods(fault_free, run) scoring.


def _plant_detectors():
    path = os.path.join(_ROOT, "examples", "lbnl_fdd", "plant_detectors.py")
    spec = importlib.util.spec_from_file_location("lbnl_plant_detectors", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _consistent(tmp_path, frames: dict, fault_free: str, detector: str, family: str, cls: str):
    """Assert config-path verdicts == plant_detectors.score verdicts for every non-reference run."""
    PD = _plant_detectors()
    example = PD.score(detector, frames, fault_free)["runs"]
    equip = {name: "PLANT__" + os.path.splitext(name)[0] for name in frames}
    st = ParquetStore(str(tmp_path / "pstore"))
    for name, frame in frames.items():
        st.write_role_frame(frame, facility_id="ds-x", equip=equip[name], equip_class=cls)
    st.register_facility("ds-x", name="x")
    marker = "hw_supply_temp" if cls == "HW_PLANT" else "power"
    cfg = {
        "source": {"kind": "store", "store": st.root, "facility_id": "ds-x"},
        "resample": "1h",
        "equipment": [{"class": cls, "marker_role": marker}],
        "rules": [],
        "drift": {
            "families": [
                {"class": cls, "family": family, "reference": {"equip": equip[fault_free]}}
            ]
        },
    }
    got = _by_equip(run_config(cfg, base_dir=str(tmp_path)).findings, detector)
    for name, verdict in example.items():
        if name == fault_free:
            assert got[equip[name]].metrics["reason"] == "is_reference"
            continue
        f = got[equip[name]]
        assert (f.severity, (f.metrics or {}).get("attribution")) == (
            verdict["severity"],
            verdict["attribution"],
        ), name
    return example


def test_config_path_matches_plant_detectors_synthetic(tmp_path):
    frames = {
        "BoilerPlant.csv": _boiler(),
        "BoilerPlant_healthy.csv": _boiler(seed=1),
        "BoilerPlant_boiler_foul_065.csv": _boiler(eff=0.55, seed=2),
        "BoilerPlant_boiler_foul_095.csv": _boiler(eff=0.755, seed=3),
    }
    runs = _consistent(
        tmp_path, frames, "BoilerPlant.csv", "boiler_efficiency_drift", "boiler", "HW_PLANT"
    )
    assert runs["BoilerPlant_boiler_foul_065.csv"]["severity"] == "fault"


_CHILLER = os.path.join(_ROOT, "examples", "_data", "lbnl", "chiller", "ChillerPlant.csv")
_BOILER = os.path.join(
    _ROOT, "examples", "_data", "lbnl_boiler", "full", "LBNL_FDD_Dataset_Boiler_Plant"
)


@pytest.mark.skipif(not os.path.exists(_CHILLER), reason="LBNL chiller plant absent (fetch.py)")
def test_config_path_matches_plant_detectors_on_the_lbnl_chiller(tmp_path):
    PD = _plant_detectors()
    runs = _consistent(
        tmp_path,
        PD.load_frames("chiller"),
        PD.CHILLER_FAULT_FREE,
        "cooling_tower_fan_effort_drift",
        "tower",
        "CHW_PLANT",
    )
    fired = sorted(r for r, v in runs.items() if v["severity"] in ("warn", "fault"))
    assert fired == [
        "ChillerPlant_coolingtower_fouling_065.csv",
        "ChillerPlant_coolingtower_fouling_080.csv",
    ]


@pytest.mark.skipif(not os.path.isdir(_BOILER), reason="LBNL boiler plant absent")
def test_config_path_matches_plant_detectors_on_the_lbnl_boiler(tmp_path):
    PD = _plant_detectors()
    runs = _consistent(
        tmp_path,
        PD.load_frames("boiler"),
        PD.BOILER_FAULT_FREE,
        "boiler_efficiency_drift",
        "boiler",
        "HW_PLANT",
    )
    fired = sorted(r for r, v in runs.items() if v["severity"] in ("warn", "fault"))
    assert fired == [f"BoilerPlant_boiler_foul_0{s}.csv" for s in ("65", "80", "95")]
