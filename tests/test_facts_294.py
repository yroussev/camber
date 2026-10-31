"""0.112 (#294, part 3): what a model is asked to say now has a fact to cite -- each flagged span,
each gap and point coverage, the points that never move, how a sensor reference bears on a
finding, an issue's rank, the server's own notices and a savings claim's own figures.

Every case runs on a small store in its own tmp directory (the sensor-bias catalog entry is
generated locally); nothing reaches the network.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import datasets  # noqa: E402
from camber.agent.verify import check  # noqa: E402
from camber.capabilities import CallContext, _Facts, default_registry  # noqa: E402
from camber.capabilities._cap_mv_summary import _claim_facts  # noqa: E402
from camber.capabilities._common import flat_roles, role_unit  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

REG = default_registry()
_DS, _FID = "synthetic-ahu-sensor-bias", "ds-synthetic-ahu-sensor-bias"
_LEAK = "leaking_valve@AHU__sat_bias"
_RESET = "supply_air_reset_compliance@AHU__sat_bias"


@pytest.fixture(scope="module")
def bias(tmp_path_factory):
    """AHU__sat_bias reads 3 F high against its duct logger; both units' heating valve and
    supply-air setpoint never move; the loggers cover four days of the three weeks."""
    root = tmp_path_factory.mktemp("facts294")
    store = str(root / "store")
    datasets.ingest(_DS, store, data_dir=str(root / "cache"))
    cfg = datasets.config_template(_DS, store, exercise="data-sensor-conditional")
    path = root / "bias.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    return CallContext(store=store, allowed_facilities=frozenset([_FID]), config=str(path))


def _kind(res, kind: str) -> list:
    return [f for f in res.facts if f.get("kind") == kind]


def _grounded(sentence: str, res) -> bool:
    return check(sentence, _Facts(res.facts), strict=True, require_cite_each_sentence=True).grounded


def test_finding_detail_states_each_span_and_the_longest(bias):
    res = REG.call("finding_detail", {"facility_id": _FID, "finding_id": _LEAK}, bias)
    spans = res.data["violations"]["spans"]
    facts = _kind(res, "evidence_span")
    assert [(f["start"], f["end"], f["value"]) for f in facts] == [
        (s["start"], s["end"], s["hours"]) for s in spans
    ]
    assert all(f["unit"] == "h" and f["subject"]["equip"] == "AHU__sat_bias" for f in facts)
    first = facts[0]
    assert "2025-05-14 06:00 to 2025-05-14 08:45, 3 hours" in first["text"]
    sent = f"The first flagged span ran from 2025-05-14 06:00 to 08:45, 3 hours [{first['id']}]."
    assert _grounded(sent, res)
    total = _kind(res, "evidence_hours")[0]
    assert total["longest_span_hours"] == 3.0 and "the longest 3 hours" in total["text"]
    assert _grounded(f"The longest span lasted 3 hours [{total['id']}].", res)


def test_finding_detail_states_the_inputs_that_never_move(bias):
    res = REG.call("finding_detail", {"facility_id": _FID, "finding_id": _LEAK}, bias)
    flat = _kind(res, "flat_point")
    assert [(f["role"], f["value"], f["unit"], f["verdict"]) for f in flat] == [
        ("heat_valve", 0.0, "%", "flat")
    ]
    assert "never moves over the window" in flat[0]["text"] and f"[{flat[0]['id']}]" in res.text


def test_a_reference_bias_that_explains_the_finding_says_so(bias):
    res = REG.call("finding_detail", {"facility_id": _FID, "finding_id": _LEAK}, bias)
    effect = _kind(res, "reference_effect")[0]
    assert effect["verdict"] == "explained" and effect["value"] == "ok"
    assert effect["severity"] == "fault" and effect["bias"] == {"supply_air_temp": 3.0}
    assert effect["bias_unit"] == {"supply_air_temp": "°F"}
    assert "by -3.0 °F" in effect["text"] and "explained by the sensor offset" in effect["text"]
    # the corrected run's own figures are in the fact: "is explained by" can be cited with them
    assert "+0.4F" in effect["text"]
    window = _kind(res, "reference_window")[0]
    assert window["verdict"] == "outside" and window["value"] == 0.0
    assert window["flagged_hours"] == 8.0 and "on other days" in window["text"]
    assert window["overlap_start"].startswith("2025-05-19")


def test_a_finding_the_bias_does_not_explain_persists(bias):
    res = REG.call("finding_detail", {"facility_id": _FID, "finding_id": _RESET}, bias)
    effect = _kind(res, "reference_effect")[0]
    assert effect["verdict"] == "persists" and effect["value"] == "warn"
    assert "does not explain it" in effect["text"]
    window = _kind(res, "reference_window")[0]
    assert window["verdict"] == "partly" and 0 < window["value"] < window["flagged_hours"]


def test_a_finding_with_no_reference_has_no_reference_fact(bias):
    res = REG.call(
        "finding_detail",
        {"facility_id": _FID, "finding_id": "supply_air_reset_compliance@AHU__healthy"},
        bias,
    )
    assert not _kind(res, "reference_effect")
    # the healthy unit's comparison passed: its window is still stated
    assert {f["verdict"] for f in _kind(res, "reference_window")} <= {"outside", "partly", "inside"}


def test_sensor_trust_states_flat_points_and_the_bias_unit(bias):
    res = REG.call("sensor_trust", {"facility_id": _FID}, bias)
    flat = {(f["equip"], f["role"]): f for f in _kind(res, "flat_point")}
    assert ("AHU__sat_bias", "heat_valve") in flat
    sp = flat[("AHU__sat_bias", "supply_air_temp_sp")]
    assert sp["value"] == 55.0 and sp["unit"] == "°F" and "read 55 °F" in sp["text"]
    ref = next(r for r in res.data["references"] if r["equip"] == "AHU__sat_bias")
    assert ref["unit"] == "°F" and ref["bias"] == pytest.approx(3.0, abs=0.2)
    fact = next(f for f in _kind(res, "reference_comparison") if f["equip"] == "AHU__sat_bias")
    assert fact["unit"] == "°F" and "bias +3.0 °F" in fact["text"]
    assert fact["subject"] == {
        "facility_id": _FID,
        "equip": "AHU__sat_bias",
        "role": "supply_air_temp",
    }
    assert "(bias +3.0 °F)" in res.text


def test_the_rcx_ranking_and_its_flat_inputs_are_facts(bias):
    res = REG.call("rcx_report", {"facility_id": _FID}, bias)
    # the capability's own issue facts (the report's fact index has its own I ids)
    issues = [f for f in _kind(res, "issue") if "@rcx_report-" in f["id"]]
    n = len(issues)
    assert [f["rank"] for f in issues] == list(range(1, n + 1))
    assert all(
        f["of"] == n and f["text"].startswith(f"Issue {f['rank']} of {n} on ") for f in issues
    )
    sent = f"1. The top issue is a supply-air sensor offset [{issues[0]['id']}]."
    assert _grounded(sent, res)
    flat = _kind(res, "flat_point")
    assert ("AHU__sat_bias", "heat_valve") in {(f["equip"], f["role"]) for f in flat}
    assert all("rest on it" in f["text"] for f in flat)


def test_coverage_states_each_point_and_gap(bias):
    res = REG.call("coverage", {"facility_id": _FID}, bias)
    points = _kind(res, "point_coverage")
    assert len(points) == len(res.data["points"])
    for f, p in zip(points, res.data["points"]):
        assert (f["equip"], f["role"]) == (p["equip"], p["role"])
        assert f["value"] == round(p["coverage"] * 100, 1) and f["unit"] == "%"
    gaps = _kind(res, "gap")
    assert gaps and len(gaps) == len(res.data["gaps"])
    for f, g in zip(gaps, res.data["gaps"]):
        assert (f["start"], f["end"], f["value"]) == (g["start"], g["end"], g["hours"])
    g = gaps[0]
    when = pd.Timestamp(g["start"]).strftime("%Y-%m-%d %H:%M")
    assert when in g["text"]
    assert _grounded(
        f"The logger has no data from {when} on, a gap of {g['value']:g} hours [{g['id']}].", res
    )


def test_each_notice_is_a_fact_reading_as_its_banner(bias):
    res = REG.call("finding_detail", {"facility_id": _FID, "finding_id": _LEAK}, bias)
    notices = _kind(res, "notice")
    assert res.banners and [f["text"] for f in notices] == res.banners
    assert all(f["id"].startswith(f"N{k}@finding_detail-") for k, f in enumerate(notices, 1))
    assert all(f["basis"] == "notice" for f in notices)


def test_fact_ids_stay_the_same_across_calls(bias):
    one = REG.call("finding_detail", {"facility_id": _FID, "finding_id": _LEAK}, bias)
    two = REG.call("finding_detail", {"facility_id": _FID, "finding_id": _LEAK}, bias)
    assert [f["id"] for f in one.facts] == [f["id"] for f in two.facts]
    assert one.facts == two.facts


def test_a_claims_figures_are_claimed_facts_never_measured():
    facts = _claim_facts("site", "The retrofit saved 15% (42,000 kWh, $5,300) in 2017.")
    assert [(f["value"], f["unit"]) for f in facts] == [
        (15.0, "%"),
        (42000.0, "kWh"),
        (5300.0, "$"),
        (2017.0, None),
    ]
    assert all(f["kind"] == "claimed_figure" and f["basis"] == "claimed" for f in facts)
    assert facts[0]["text"].startswith("The claim under check states 15%: the claim's own figure")
    # only the figures are written, never the claim's words
    assert not any("retrofit" in f["text"] for f in facts)


def _meter(days=150, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=24 * days, freq="h")
    t = 55 + 20 * np.sin(np.arange(len(idx)) / (24 * 365) * 2 * np.pi) + rng.normal(0, 2, len(idx))
    e = 50 + np.clip(t - 60, 0, None) * 2 + rng.normal(0, 1, len(idx))
    return pd.DataFrame({Role.POWER: e, Role.OAT: t}, index=idx)


def test_mv_summary_takes_the_claim_and_the_prompt_passes_it(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(_meter(), facility_id="mv-site", equip="MainMeter", equip_class="METER")
    cfg = {
        "source": {"kind": "store", "store": "store", "facility_id": "mv-site"},
        "equipment": [{"class": "METER"}],
        "mv": [
            {
                "class": "METER",
                "role": "power",
                "period": ["2024-01-01", "2024-03-31"],
                "reporting_period": ["2024-04-01", None],
            }
        ],
    }
    path = tmp_path / "mv.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    ctx = CallContext(store=st.root, allowed_facilities=frozenset(["mv-site"]), config=str(path))
    res = REG.call("mv_summary", {"facility_id": "mv-site", "claim": "saves 15% a year"}, ctx)
    claimed = _kind(res, "claimed_figure")
    assert [(f["value"], f["unit"]) for f in claimed] == [(15.0, "%")]
    assert claimed[0]["id"].startswith("A1@mv_summary-")
    plain = REG.call("mv_summary", {"facility_id": "mv-site"}, ctx)
    assert not _kind(plain, "claimed_figure")
    prompt = {p.name: p for p in REG.prompts()}["check_savings_claim"]
    text = prompt.render({"facility_id": "mv-site", "claim": "saves 15% a year"})[0]["text"]
    assert '"claim": "saves 15% a year"' in text and "claimed_figure" in text


def test_flat_roles_and_role_units():
    idx = pd.date_range("2025-01-01", periods=48, freq="h")
    frame = pd.DataFrame(
        {
            Role.HEAT_VALVE: 0.0,
            Role.SUPPLY_AIR_TEMP_SP: 55.0,
            Role.SUPPLY_AIR_TEMP: np.linspace(50, 60, 48),
        },
        index=idx,
    )
    frame.iloc[:5, 1] = np.nan  # gaps do not make a point move
    out = flat_roles(frame)
    assert set(out) == {"heat_valve", "supply_air_temp_sp"}
    assert out["supply_air_temp_sp"]["n_samples"] == 43
    assert flat_roles(frame.iloc[:10]) == {}  # too few samples to say it never moves
    assert role_unit("supply_air_temp") == "°F" and role_unit("oat") == "°F"
    assert role_unit("heat_valve") == "%" and role_unit("power") is None


def test_the_prompts_point_at_the_new_facts():
    prompts = {p.name: p for p in REG.prompts()}
    triage = prompts["triage_ahu"].render({"facility_id": "f", "equip": "AHU-1"})[0]["text"]
    assert "explained by the sensor offset" in triage and "never move" in triage
    assert "('1.')" in triage and "N id" in triage
    dq = prompts["data_quality_review"].render({"facility_id": "f"})[0]["text"]
    assert "each listed gap is a fact" in dq
    rcx = prompts["rcx_executive_summary"].render({"facility_id": "f"})[0]["text"]
    assert "Issue 1 of 6" in rcx
