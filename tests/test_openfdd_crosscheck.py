"""examples/openfdd_crosscheck (#22 item 1): mapping, normalisers and scoring, offline.

No Docker, no open-fdd install and no real data: synthetic frames, the pandas engine's output
recorded on the synthetic probes, and hand-written SQL result files in the engine's format. The
real-engine run is a manual step (``run_crosscheck.py``).
"""

import json
import os
import sys

import pandas as pd
import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_EX = os.path.join(_ROOT, "examples", "openfdd_crosscheck")
sys.path.insert(0, _ROOT)
sys.path.insert(0, _EX)

import harness as hx  # noqa: E402
import run_crosscheck as rc  # noqa: E402

from camber.model.roles import Role  # noqa: E402
from camber.rules.g36_rule import G36AFDD  # noqa: E402

ROLE_MAP = hx.load_json("role_map.json")
PROFILES = hx.load_json("profiles.json")


def _fixture(name):
    with open(os.path.join(_EX, "fixtures", name), encoding="utf-8") as fh:
        return json.load(fh)


def _frame(**cols):
    idx = pd.date_range("2024-07-01", periods=8, freq="15min")
    return pd.DataFrame({k: [float(v)] * len(idx) for k, v in cols.items()}, index=idx)


# --------------------------------------------------------------------------- mapping
def test_role_map_is_versioned_and_complete():
    assert ROLE_MAP["version"] and ROLE_MAP["changes"][ROLE_MAP["version"]]
    assert ROLE_MAP["checked_against"]["open_fdd_commit"] == hx.OPENFDD_PIN["commit"]
    valid = {r.value for r in Role}
    for row in ROLE_MAP["roles"]:
        assert row["camber"] in valid
        assert row["pandas"] and row["sql"]
        assert row["scale"] in ("none", "percent_to_fraction")
    assert set(ROLE_MAP["fault_conditions"]) == set(hx.FCS)
    assert ROLE_MAP["fault_conditions"]["FC13"]["sql"] == "FC13-SAT-HIGH"
    # one CAMBER role per engine column, no duplicates
    for eng in ("pandas", "sql"):
        cols = [r[eng] for r in ROLE_MAP["roles"]]
        assert len(cols) == len(set(cols))


def test_profiles_cover_both_engines_and_only_known_fcs():
    profs = PROFILES["profiles"]
    assert set(profs) == {"openfdd_defaults", "g36"}
    assert profs["openfdd_defaults"]["pandas"] == {} and profs["openfdd_defaults"]["sql"] == {}
    g36 = profs["g36"]
    assert set(g36["pandas"]) <= set(hx.FCS) and set(g36["sql"]) <= set(hx.FCS)
    # the G36 values CAMBER uses (camber.fdd_g36.G36Thresholds)
    assert g36["pandas"]["FC8"] == {"eps_sat": 2.0, "eps_mat": 5.0, "delta_supply_fan": 2.0}
    assert g36["pandas"]["FC2"]["eps_rat"] == 2.0 and g36["pandas"]["FC2"]["eps_oat"] == 5.0
    # a FC the SQL tuning file cannot reach is listed, never silently mis-set
    for fc in g36["sql_not_expressible"]:
        assert fc in hx.FCS and "eps_sat" not in g36["sql"].get(fc, {})


def test_openfdd_frame_scales_percent_and_renames():
    f = _frame(supply_air_temp=55, cool_valve=50, oa_damper=0.5, supply_fan_speed=80)
    out, applied = hx.openfdd_frame(f, ROLE_MAP, "pandas")
    assert out["discharge-air-temp"].iloc[0] == 55
    assert out["cooling-valve"].iloc[0] == pytest.approx(0.5)
    # 0.5 % stays 0.005: open-fdd would read a raw 0.5 as 50 %
    assert out["outside-air-damper"].iloc[0] == pytest.approx(0.005)
    assert out["fan-cmd"].iloc[0] == pytest.approx(0.8)
    assert applied == []
    sql, _ = hx.openfdd_frame(f, ROLE_MAP, "sql")
    assert {"sat", "clg_valve_pct", "oa_damper_pct", "fan_cmd"} <= set(sql.columns)
    with pytest.raises(ValueError):
        hx.openfdd_frame(f, ROLE_MAP, "rust")


def test_fan_fallback_and_coil_substitution_are_declared():
    f = _frame(supply_air_temp=56, mixed_air_temp=60, cool_valve=0, supply_fan_status=1)
    f.columns = [Role(c) for c in f.columns]  # Role-typed columns, as the store returns them
    out, applied = hx.openfdd_frame(f, ROLE_MAP, "pandas")
    assert out["fan-cmd"].iloc[0] == 1.0 and out["fan-status"].iloc[0] == 1.0
    assert out["cooling-coil-entering-temp"].iloc[0] == 60
    assert out["cooling-coil-leaving-temp"].iloc[0] == 56
    assert len(applied) == 3 and any(a.startswith("fan-cmd <- supply_fan_status") for a in applied)
    # the SQL engine falls back to MAT/SAT by itself: no substitution for it
    sql, applied_sql = hx.openfdd_frame(f, ROLE_MAP, "sql")
    assert "cooling-coil-entering-temp" not in sql.columns
    assert applied_sql == ["fan_cmd <- supply_fan_status (no supply_fan_speed is mapped)"]
    # with a heating coil MAT/SAT span both coils: no substitution
    h = _frame(supply_air_temp=56, mixed_air_temp=60, cool_valve=0, heat_valve=0)
    out, applied = hx.openfdd_frame(h, ROLE_MAP, "pandas")
    assert "cooling-coil-entering-temp" not in out.columns and applied == []


def test_sql_tree_layout_one_building_per_equipment(tmp_path):
    frames = {
        "AHU__a/b": _frame(supply_air_temp=55, cool_valve=40, supply_fan_status=1),
        "AHU__c": _frame(supply_air_temp=55, cool_valve=40, supply_air_temp_sp=55),
    }
    notes = hx.write_sql_tree(frames, ROLE_MAP, str(tmp_path), grid_minutes=15)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["AHU__a_b", "AHU__c"]
    manifest = tmp_path / "AHU__a_b" / "manifest.json"
    assert json.loads(manifest.read_text()) == {"grid_minutes": 15}
    edir = tmp_path / "AHU__a_b" / "AHU__a_b"
    cols = (edir / "columns.csv").read_text().splitlines()
    assert cols[0] == "column,point_role,point_name,units"
    assert "clg_valve_pct,clg_valve_pct,clg_valve_pct,0-1" in cols
    # a role only the neighbour has stays out of this building's columns
    assert not any(c.startswith("sat_sp,") for c in cols)
    hist = pd.read_csv(edir / "history_wide.csv")
    assert hist.columns[0] == "timestamp_utc" and hist["timestamp_utc"].iloc[0].endswith("Z")
    assert hist["clg_valve_pct"].iloc[0] == pytest.approx(0.4)
    assert notes["AHU__a/b"]  # the fan_cmd fallback was applied and recorded
    assert notes["AHU__c"] == []


def test_sql_tuning_and_docker_commands(tmp_path):
    g36 = PROFILES["profiles"]["g36"]
    ov = hx.sql_rule_overrides(g36, ROLE_MAP)
    assert "FC13-SAT-HIGH" not in ov and "FC8" in ov
    # a rule whose G36 tolerances cannot all be set gets no partial override
    for fc in g36["sql_not_expressible"]:
        assert ROLE_MAP["fault_conditions"][fc]["sql"] not in ov
    text = hx.sql_tuning_yaml(ov)
    assert text.startswith("rules:\n") and "  FC8:\n    supply_tol: 2.0" in text
    assert hx.sql_tuning_yaml({}) == "rules: {}\n"
    tuning = tmp_path / "t.yaml"
    ingest, run = hx.docker_commands(
        "img",
        str(tmp_path / "tree"),
        str(tmp_path / "w"),
        "g36",
        buildings=["B1", "B2"],
        tuning_file=str(tuning),
    )
    for argv in (ingest, run):
        assert argv[:5] == ["docker", "run", "--rm", "--network", "none"]
        assert "for b in B1 B2;" in argv[-1]
    assert any(a.endswith(":/data:ro") for a in ingest)
    assert any(a.endswith(":/parquet:ro") for a in run)
    assert any(a.endswith("/rule_tuning/defaults.yaml:ro") for a in run)
    assert "--rules-dir /opt/open-fdd/sql_rules" in run[-1]
    # only results are writable in the run container
    rw = [
        a for a in run if a.startswith(("/", str(tmp_path))) and ":" in a and not a.endswith(":ro")
    ]
    assert rw == [f"{tmp_path / 'w' / 'results-g36'}:/out"]
    _, run_default = hx.docker_commands("img", "t", "w", "d", buildings=["B"], tuning_file=None)
    assert not any("rule_tuning" in a for a in run_default)
    with pytest.raises(ValueError):
        hx.docker_commands("img", "t", "w", "d", buildings=["a; rm -rf /"], tuning_file=None)


# --------------------------------------------------------------------------- normalisers
def test_normalise_camber_on_a_synthetic_frame():
    spec = hx.PROBES["fc13_sat_3_over_sp_half_cooling"]["frame"]
    f = hx.probe_frame(dict(spec, sat_offset_sp=4.0, cool_valve=100.0))
    f.columns = [Role(c) for c in f.columns]
    vs = {v.fc: v for v in hx.normalise_camber(G36AFDD().analyze("X", f).as_dict(), "X")}
    assert len(vs) == 15 and all(v.engine == hx.ENGINE_CAMBER for v in vs.values())
    assert vs["FC13"].evaluated and vs["FC13"].native_fired
    assert hx.common_verdict(vs["FC13"]) == ("fired", None)
    assert not vs["FC7"].evaluated and vs["FC7"].reason.startswith("omitted")
    assert "operating states" in vs["FC13"].denominator


def test_normalise_camber_declined_run():
    finding = {"metrics": {"declined": True, "reason": "no supply-fan status"}}
    vs = hx.normalise_camber(finding, "X")
    assert len(vs) == 15 and not any(v.evaluated for v in vs)
    assert vs[0].reason == "run declined: no supply-fan status"


def test_normalise_pandas_recorded_fixture():
    fx = _fixture("pandas_probe_records.json")
    recs = [r for r in fx["records"] if r["profile"] == "openfdd_defaults"]
    vs = hx.normalise_pandas(recs, ROLE_MAP, "openfdd_defaults", fx["poll_seconds"])
    by = {(v.equip, v.fc): v for v in vs}
    v = by[("fc13_sat_1p5_over_sp_full_cooling", "FC13")]
    assert v.engine == hx.ENGINE_PANDAS and v.evaluated and v.native_fired
    assert v.eval_hours == pytest.approx(v.eval_hours) and v.eval_hours > 0
    # no heating valve in the probes: FC5/FC7 are skipped with the missing role named
    fc7 = by[("fc13_sat_1p5_over_sp_full_cooling", "FC7")]
    assert not fc7.evaluated and "heating-valve" in fc7.reason
    fc6 = by[("fc13_sat_1p5_over_sp_full_cooling", "FC6")]
    assert not fc6.evaluated and "vav-total-airflow" in fc6.reason


@pytest.mark.parametrize("name", sorted(hx.PROBES))
def test_probe_expectations_camber_and_recorded_pandas(name):
    p = hx.PROBES[name]
    f = hx.probe_frame(p["frame"])
    f.columns = [Role(c) for c in f.columns]
    cam = {v.fc: v for v in hx.normalise_camber(G36AFDD().analyze(name, f).as_dict(), name)}
    assert hx.native_verdict(cam[p["fc"]])[0] == (
        "fired" if p["expect"]["camber g36_afdd [camber_defaults]"] else "not_fired"
    )
    fx = _fixture("pandas_probe_records.json")
    for prof in ("openfdd_defaults", "g36"):
        recs = [r for r in fx["records"] if r["profile"] == prof and r["equip"] == name]
        vs = {v.fc: v for v in hx.normalise_pandas(recs, ROLE_MAP, prof, fx["poll_seconds"])}
        want = p["expect"][f"open-fdd pandas [{prof}]"]
        assert hx.native_verdict(vs[p["fc"]])[0] == ("fired" if want else "not_fired")
    sql = _fixture("sql_probe_results.json")
    for prof in ("openfdd_defaults", "g36"):
        vs = hx.normalise_sql(sql["profiles"][prof], ROLE_MAP, prof, sorted(hx.PROBES), {})
        v = {(x.equip, x.fc): x for x in vs}[(name, p["fc"])]
        want = p["expect"][f"open-fdd sql [{prof}]"]
        assert hx.native_verdict(v)[0] == ("fired" if want else "not_fired")


def test_normalise_sql_denominators_and_skips():
    fx = _fixture("sql_results_synthetic.json")
    vs = hx.normalise_sql(
        fx["bodies"], ROLE_MAP, "openfdd_defaults", fx["equips"], fx["fan_on_hours"]
    )
    by = {(v.equip, v.fc): v for v in vs}
    assert len(vs) == 15 * 2
    fc8 = by[("AHU__stuck", "FC8")]  # fault hours only -> the frame's fan-on hours
    assert fc8.eval_hours == 80.0 and "fan-on hours" in fc8.denominator and fc8.native_fired
    fc13 = by[("AHU__stuck", "FC13")]  # FC13-SAT-HIGH, fault_pct present
    assert fc13.eval_hours == pytest.approx(40.0) and "fault_pct" in fc13.denominator
    fc10 = by[("AHU__stuck", "FC10")]
    assert fc10.eval_hours == 60.0 and fc10.denominator.startswith("total_hours")
    assert not by[("AHU__stuck", "FC7")].evaluated
    assert "htg_valve_pct" in by[("AHU__stuck", "FC7")].reason
    assert by[("AHU__stuck", "FC9")].reason.startswith("engine error")
    assert by[("AHU__stuck", "FC12")].reason == "no row for this equipment"
    assert by[("AHU__stuck", "FC1")].reason == "no result file"
    assert by[("AHU__fault_free", "FC13")].native_fired is False


# --------------------------------------------------------------------------- scoring
def _v(engine, equip, fc, *, hours=None, fault=0.0, evaluated=True, reason=None):
    return hx.Verdict(
        engine, "p", equip, fc, evaluated, reason, fault, hours, "d", (fault or 0) > 0
    )


def test_common_verdict_rule():
    assert hx.common_verdict(_v("e", "a", "FC8", hours=100, fault=5)) == ("fired", None)
    assert hx.common_verdict(_v("e", "a", "FC8", hours=100, fault=4.9))[0] == "not_fired"
    st, why = hx.common_verdict(_v("e", "a", "FC8", hours=10, fault=10))
    assert st == "not_evaluated" and "24" in why
    st, why = hx.common_verdict(_v("e", "a", "FC8", evaluated=False, reason="missing roles: x"))
    assert (st, why) == ("not_evaluated", "missing roles: x")
    assert hx.native_verdict(_v("e", "a", "FC8", hours=10, fault=0.1))[0] == "fired"


def test_score_keeps_not_evaluated_apart_and_engines_separate():
    labels = {"ff": "", "d1": "damper", "d2": "damper", "v1": "valve_stuck", "x": "damper"}
    vs = []
    for eng, fires in (("A", {"d1", "v1"}), ("B", {"ff", "d1", "d2"})):
        for equip in ("ff", "d1", "d2", "v1"):
            vs.append(_v(eng, equip, "FC8", hours=100, fault=50 if equip in fires else 0))
            vs.append(_v(eng, equip, "FC13", evaluated=False, reason="missing roles: sat_sp"))
        vs.append(_v(eng, "x", "FC8", evaluated=False, reason="missing roles: mat"))
    vs.append(_v("A", "unlabelled", "FC8", hours=100, fault=50))
    sc = hx.score(vs, labels)
    assert set(sc) == {"A [p]", "B [p]"}
    a8 = sc["A [p]"]["per_fc"]["FC8"]
    assert (a8["tp"], a8["fn"], a8["fp"], a8["tn"]) == (2, 1, 0, 1)
    assert a8["tpr"] == pytest.approx(0.6667) and a8["tpr_ci"][0] < 0.6667 < a8["tpr_ci"][1]
    assert a8["fpr"] == 0.0 and a8["not_evaluated"] == {"x": "missing roles: mat"}
    a13 = sc["A [p]"]["per_fc"]["FC13"]
    assert a13["tpr"] is None and a13["fpr_ci"] is None and len(a13["not_evaluated"]) == 4
    b = sc["B [p]"]["overall"]
    assert (b["tp"], b["fn"], b["fp"], b["tn"]) == (2, 1, 1, 0) and b["not_evaluated"] == ["x"]
    assert sc["A [p]"]["by_fault_type"]["valve_stuck"]["tp"] == 1
    assert "fpr" not in sc["A [p]"]["by_fault_type"]["damper"]


def test_markdown_and_offline_probe_run(tmp_path):
    rc.main(["--probe", "--out", str(tmp_path), "--sql-skip", "no Docker in tests"])
    res = json.loads((tmp_path / "probes.json").read_text())
    assert res["not_run"] == {
        hx.ENGINE_PANDAS: "no --openfdd-python given",
        hx.ENGINE_SQL: "no Docker in tests",
    }
    assert {v["engine"] for v in res["verdicts"]} == {hx.ENGINE_CAMBER}
    for name, p in res["probes"].items():
        row = p["engines"]["camber g36_afdd [camber_defaults]"]
        assert row["observed"] == ("fired" if row["expected_fired"] else "not_fired"), name
    md = (tmp_path / "probes.md").read_text()
    assert "## Not run" in md and "no Docker in tests" in md
    full = hx.markdown(
        {
            "verdict_rule": "r",
            "versions": {"camber": "x"},
            "datasets": [{"id": "d", "licence": "CC-BY-4.0", "n_labelled": 2, "n_fault_free": 1}],
            "window": "run",
            "scores": {
                "common": {
                    "pooled": hx.score([_v("A", "a", "FC8", hours=100, fault=9)], {"a": "f"})
                },
                "native": {
                    "pooled": hx.score([_v("A", "a", "FC8", hours=100, fault=9)], {"a": "f"})
                },
            },
            "not_run": {"open-fdd sql": "why"},
            "caveats": hx.standard_caveats("month", ["lbnl-ddahu"]),
        }
    )
    assert "### A [p]" in full and "| FC8 | 1.00 [" in full and "dual-duct" in full
    assert "not independent" in full


def test_docker_ready_reports_a_missing_binary(monkeypatch):
    monkeypatch.setattr(rc.shutil, "which", lambda _name: None)
    assert rc.docker_ready() == (False, "docker is not installed")


def test_omit_verdicts_collapses_not_evaluated_to_counts():
    labels = {"a": "f", "b": ""}
    vs = [
        _v("A", "a", "FC8", evaluated=False, reason="missing roles: mat"),
        _v("A", "b", "FC8", evaluated=False, reason="missing roles: mat"),
    ]
    scores = {"common": {"pooled": hx.score(vs, labels)}}
    rc._collapse_not_evaluated(scores)
    sc = scores["common"]["pooled"]["A [p]"]
    assert sc["per_fc"]["FC8"]["not_evaluated"] == {"counts_by_reason": {"missing roles: mat": 2}}
    assert sc["overall"]["not_evaluated"] == 2
    md = hx._engine_tables(scores["common"]["pooled"])
    assert any("| FC8 |" in line and "2: missing roles: mat" in line for line in md)


def test_template_params_come_from_the_catalog_run_template():
    assert rc.template_params("lbnl-sdahu") == {"min_oa_pct": 1.6}
    assert rc.template_params("no-such-dataset") == {}
    # an equipment-specific param reaches the rule: FC6 is evaluated only with min_oa_pct
    f = hx.probe_frame({"cool_valve": 100.0, "oa_damper": 0.0})  # minimum OA: OS#4
    vs = rc.run_camber({"X": f}, {"X": {"min_oa_pct": 1.6}})
    assert {v.fc: v for v in vs}["FC6"].evaluated
    assert not {v.fc: v for v in rc.run_camber({"X": f})}["FC6"].evaluated
