"""0.111: the trust floor and sensor_trust read one score (#311), and a configured sensor
reference reaches sensor_trust, rule_run, finding_detail, the RCx ranking and the triage prompts
(#313).

Every case runs on a small store in its own tmp directory (the sensor-bias catalog entry is
generated locally); nothing reaches the network.
"""

import json
import os
import re
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber import datasets  # noqa: E402
from camber.capabilities import CallContext, default_registry  # noqa: E402
from camber.capabilities._common import trust_banner, trust_declines  # noqa: E402
from camber.config import run_config  # noqa: E402
from camber.report import build_rcx_report  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.builtin import builtin_registry  # noqa: E402
from camber.rules.triage import Issue, _sensor_first  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

REG = default_registry()
_BIAS_DS = "synthetic-ahu-sensor-bias"
_BIAS_FID = "ds-synthetic-ahu-sensor-bias"


# ------------------------------------------------------------------------------------- #311


def _bursty(frame: pd.DataFrame) -> pd.DataFrame:
    """Only one day in seven stored: a trend logged in bursts (about 14 % of the window)."""
    day = (frame.index - frame.index[0]).days
    return frame[(day % 7) == 0]


@pytest.fixture(scope="module")
def bursty(tmp_path_factory):
    root = tmp_path_factory.mktemp("bursty311")
    st = ParquetStore(str(root / "store"))
    st.write_role_frame(
        _bursty(fx.ahu_frame(seed=5)), facility_id="burst", equip="AHU-B", equip_class="AHU"
    )
    st.write_role_frame(fx.ahu_frame(seed=6), facility_id="burst", equip="AHU-D", equip_class="AHU")
    st.register_facility("burst", name="Bursty site")
    cfg = {
        "source": {"kind": "store", "store": "store", "facility_id": "burst"},
        "resample": "1h",
        "equipment": [{"class": "AHU"}],
        "rules": ["economizer_high_limit", "outdoor_air_fraction", "supply_air_reset"],
    }
    path = root / "burst.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return CallContext(
        store=str(root / "store"), allowed_facilities=frozenset(["burst"]), config=str(path)
    )


def test_a_bursty_trend_is_declined_exactly_when_sensor_trust_puts_it_below_the_floor(bursty):
    st = REG.call("sensor_trust", {"facility_id": "burst"}, bursty).data
    score = {(p["equip"], p["role"]): p for p in st["points"]}
    below = {k for k, p in score.items() if p["below_floor"]}
    # the bursty unit's points cover about a seventh of the run's time grid
    assert ("AHU-B", "supply_air_temp") in below
    assert score[("AHU-B", "supply_air_temp")]["coverage"] < 0.25
    assert not any(e == "AHU-D" for e, _r in below)
    rr = REG.call("rule_run", {"facility_id": "burst"}, bursty)
    declined = [f for f in rr.data["findings"] if f["metrics"].get("declined")]
    assert declined and all(f["equip"] == "AHU-B" for f in declined)
    reg = builtin_registry()
    for f in declined:
        named = re.findall(r"(\w+) \(trust ([0-9.]+)\)", f["summary"])
        assert named
        for role, trust in named:  # each named input is below the floor, at the same score
            assert (f["equip"], role) in below
            assert f"{score[(f['equip'], role)]['trust']:.2f}" == trust
    judged = {(f["rule"], f["equip"]) for f in rr.data["findings"] if f not in declined}
    for rule, equip in judged:  # a check that ran reads no input below the floor
        req = {r.value for r in reg.get(rule).roles_required}
        assert not {(equip, r) for r in req} & below
    banner = next(b for b in rr.banners if "declined" in b)
    assert "(trust 0." in banner and "sensor_trust over the same window" in banner


def test_the_decline_notice_names_the_scores():
    bad = Finding(
        rule="cooling_tower_approach",
        equip="T1",
        severity="info",
        metrics={
            "declined": True,
            "min_trust": 0.5,
            "untrusted_roles": ["cw_supply_temp"],
            "trust": {"cw_supply_temp": 0.21},
        },
        summary="T1: declined -- untrusted input(s): cw_supply_temp (trust 0.21)",
    )
    other = Finding(
        rule="cooling_tower_approach",
        equip="T2",
        severity="info",
        metrics={**bad.metrics, "trust": {"cw_supply_temp": 0.34}},
        summary="",
    )
    declines, floor = trust_declines([bad, other])
    assert declines[0] == (
        "cooling_tower_approach",
        "T1",
        ["cw_supply_temp"],
        {"cw_supply_temp": 0.21},
    )
    text = trust_banner(declines, floor)
    assert "cw_supply_temp (trust 0.21 to 0.34)" in text


# ------------------------------------------------------------------------------------- #313


@pytest.fixture(scope="module")
def bias(tmp_path_factory):
    """The sensor-bias entry: AHU__sat_bias reads 3 F high against LOGGER__sat_bias; AHU__healthy
    gets a CSV reference cut from its own supply air, so its comparison passes."""
    root = tmp_path_factory.mktemp("bias313")
    store = str(root / "store")
    datasets.ingest(_BIAS_DS, store, data_dir=str(root / "cache"))
    healthy = ParquetStore(store).read_role_frame(facility_id=_BIAS_FID, equip="AHU__healthy")
    sat = healthy[[c for c in healthy.columns if str(getattr(c, "value", c)) == "supply_air_temp"]]
    sat = sat.iloc[:, 0].dropna()
    pd.DataFrame({"time": sat.index, "f": sat.to_numpy()}).to_csv(root / "ref.csv", index=False)
    cfg = datasets.config_template(_BIAS_DS, store, exercise="data-sensor-conditional")
    cfg["report"]["rcx"]["references"]["AHU__healthy"] = {
        "supply_air_temp": {"csv": "ref.csv", "label": "handheld probe"}
    }
    path = root / "bias.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    ctx = CallContext(store=store, allowed_facilities=frozenset([_BIAS_FID]), config=str(path))
    return ctx, cfg, str(root)


def test_sensor_trust_carries_the_reference_comparisons(bias):
    ctx = bias[0]
    res = REG.call("sensor_trust", {"facility_id": _BIAS_FID}, ctx)
    refs = {r["equip"]: r for r in res.data["references"]}
    biased, healthy = refs["AHU__sat_bias"], refs["AHU__healthy"]
    assert biased["verdict"] == "warn" and biased["bias"] == pytest.approx(3.0, abs=0.2)
    assert biased["n_samples"] > 100 and biased["overlap_start"] and biased["overlap_end"]
    assert healthy["verdict"] == "ok" and abs(healthy["bias"]) < 0.5
    pt = next(
        p
        for p in res.data["points"]
        if (p["equip"], p["role"]) == ("AHU__sat_bias", "supply_air_temp")
    )
    # an offset has no shape: the score stays high, the reference makes the point suspect
    assert pt["trust"] >= 0.8 and pt["verdict"] == "suspect" and not pt["below_floor"]
    assert "reference_warn" in pt["flags"] and pt["reference"]["verdict"] == "warn"
    facts = [f for f in res.facts if f["kind"] == "reference_comparison"]
    assert len(facts) == 2
    warn = next(f for f in facts if f["value"] == "warn")
    assert "minus the reference" in warn["text"] and "reads high" in warn["text"]
    passed = next(f for f in facts if f["value"] == "ok")
    assert "passed" in passed["text"] and "AHU__healthy" in passed["text"]
    assert "disagrees with its configured reference" in res.text


def test_rule_run_and_finding_detail_mark_findings_conditional_on_the_sensor(bias):
    ctx = bias[0]
    rr = REG.call("rule_run", {"facility_id": _BIAS_FID, "min_severity": "ok"}, ctx)
    rows = {f["finding_id"]: f for f in rr.data["findings"]}
    leak = rows["leaking_valve@AHU__sat_bias"]
    assert (
        leak["conditional_on"] and "sensor_drift:supply_air_temp warn" in leak["conditional_on"][0]
    )
    assert not any(r.get("conditional_on") for r in rows.values() if r["equip"] == "AHU__healthy")
    fact = next(f for f in rr.facts if f.get("finding_id") == "leaking_valve@AHU__sat_bias")
    assert "conditional on sensor_drift:supply_air_temp" in fact["text"]
    assert any("possible sensor problem first" in b for b in rr.banners)
    fd = REG.call(
        "finding_detail",
        {"facility_id": _BIAS_FID, "finding_id": "leaking_valve@AHU__sat_bias"},
        ctx,
    )
    assert fd.data["finding"]["conditional_on"] == leak["conditional_on"]
    assert "conditional on" in fd.text and fd.banners
    # the CLI's findings are unchanged: the condition is a field of the row, not of the finding
    cli = run_config(bias[1], base_dir=bias[2], persist=False)
    f = next(x for x in cli.findings if (x.rule, x.equip) == ("leaking_valve", "AHU__sat_bias"))
    assert " ".join(f.summary.split()) == leak["summary"]


def test_the_rcx_report_ranks_the_root_sensor_issue_above_its_dependents(bias):
    ctx, cfg, base = bias
    for issues in (
        build_rcx_report(run_config(cfg, base_dir=base)).to_dict()["issues"],
        REG.call("rcx_report", {"facility_id": _BIAS_FID}, ctx).data["issues"],
    ):
        rank = {(i["equip"], i["rules"][0]): i["rank"] for i in issues}
        sensor = rank[("AHU__sat_bias", "sensor_drift:supply_air_temp")]
        assert sensor < rank[("AHU__sat_bias", "leaking_valve")]
        assert sensor < rank[("AHU__sat_bias", "supply_air_reset_compliance")]


def test_sensor_first_moves_only_a_root_above_its_own_dependents():
    def issue(key, members=(), dependents=()):
        return Issue(key=key, root=None, members=list(members), dependents=list(dependents))

    a, b, c = object(), object(), object()
    leak = issue("leak", [a])
    other = issue("other", [b])
    sensor = issue("sensor", [c], dependents=[a])
    assert [i.key for i in _sensor_first([other, leak, sensor])] == ["other", "sensor", "leak"]
    assert [i.key for i in _sensor_first([sensor, leak, other])] == ["sensor", "leak", "other"]


def test_the_triage_prompts_put_a_disagreeing_reference_first():
    prompt = {p.name: p for p in REG.prompts()}["triage_ahu"]
    msg = prompt.render({"facility_id": "f", "equip": "AHU-1"})
    text = msg[0]["text"]
    assert "reference" in text and "conditional_on" in text and "sensor problem first" in text
