"""Tests for #78: the linked PNNL Re-tuning references, cause-keyed recommendations, the audit
report's title / empty sections / styling / "Learn more" links, the RCx "Further reading", the
trend viewer's units and the lab's display paths."""

import datetime as dt
import importlib
import inspect
import json
import os
import pkgutil
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import camber.rules  # noqa: E402
from camber import references as R  # noqa: E402
from camber.actionplan import action_plan_html, action_plan_rows, build_action_plan  # noqa: E402
from camber.api.ui import live_dashboard_html, role_units  # noqa: E402
from camber.aso import RECOMMENDERS, recommend  # noqa: E402
from camber.lab._app import display_path  # noqa: E402
from camber.lab._ui import lab_page_html  # noqa: E402
from camber.report.audit import ECM, AuditReport, Benchmark, html_document  # noqa: E402
from camber.rules.base import Finding  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _rule_names() -> set:
    """Every rule name defined in camber.rules: classes with a string ``name`` plus the built-in
    registry's names, which include the instance-named rules (cohort, census, starvation)."""
    from camber.rules import builtin

    names = set(builtin.rule_names())
    for mod in pkgutil.iter_modules(camber.rules.__path__):
        m = importlib.import_module(f"camber.rules.{mod.name}")
        for _n, obj in inspect.getmembers(m, inspect.isclass):
            name = getattr(obj, "name", None)
            if isinstance(name, str) and obj.__module__ == m.__name__:
                names.add(name)
    return names


# --------------------------------------------------------------------------- registry integrity


def test_registry_ids_unique_https_kinds_and_dates():
    ids = [r.id for r in R._ALL]
    assert len(ids) == len(set(ids)) == len(R.REFERENCES)
    for r in R.REFERENCES.values():
        assert re.fullmatch(r"[a-z0-9][a-z0-9-]*", r.id), r.id
        assert r.url.startswith("https://"), r.url
        assert r.kind in R.KINDS and r.title and r.publisher
        dt.date.fromisoformat(r.verified_on)
    assert len({r.url for r in R.REFERENCES.values()}) == len(R.REFERENCES)


def test_the_nine_guides_and_ten_chapters_are_registered():
    guides = [r for r in R.REFERENCES.values() if r.kind == R.GUIDE and r.number]
    assert len(guides) == 9 and all(r.number.startswith("PNNL-SA-") for r in guides)
    assert all(r.section for r in guides)  # each guide mapping was checked against its headings
    assert [f"pnnl-retuning-ch{n}" in R.REFERENCES for n in range(1, 11)] == [True] * 10
    for rid in ("pnnl-trending-requirements", "pnnl-ecam-interval-data", "ecam"):
        assert rid in R.REFERENCES


def test_every_mapped_rule_exists_and_every_mapped_id_resolves():
    known = _rule_names()
    for rule, ids in R.RULE_REFERENCES.items():
        assert rule in known, f"mapped rule {rule!r} is not a rule"
        assert ids and all(i in R.REFERENCES for i in ids), rule
    # every guide backs at least one rule, and so do chapters 5-8
    mapped = {i for ids in R.RULE_REFERENCES.values() for i in ids}
    for r in R.REFERENCES.values():
        if r.kind == R.GUIDE and r.number:
            assert r.id in mapped, r.id
    for n in (5, 6, 7, 8):
        assert f"pnnl-retuning-ch{n}" in mapped
    # a recommendation cites only registered references
    for rule in RECOMMENDERS:
        rec = recommend(Finding(rule=rule, equip="E", severity="warn", summary=""))
        if rec is not None:
            assert all(i in R.REFERENCES for i in rec.references), rule


def test_instance_named_rules_are_seen_and_mapped():
    """#89: the instance-named rules count as rules, and the 0.98 mappings are in place."""
    known = _rule_names()
    for rule in ("cohort_airflow", "sat_rogue_zone_census", "static_cohort_starvation"):
        assert rule in known, rule
    assert R.reference_ids_for("sat_rogue_zone_census") == [
        "pnnl-guide-discharge-air-temp",
        "pnnl-retuning-ch7",
    ]
    assert R.reference_ids_for("static_rogue_zone_census")[0] == "pnnl-guide-static-pressure"
    for rule in ("condenser_bypass_leak", "chiller_staging_fleet", "boiler_efficiency_drift"):
        assert R.reference_ids_for(rule) == ["pnnl-retuning-ch8"], rule
    # deliberately unmapped (see the comment in RULE_REFERENCES)
    for rule in ("sat_reset_effectiveness", "static_reset_effectiveness", "g36_afdd"):
        assert R.reference_ids_for(rule) == [], rule


def test_references_doc_lists_every_mapped_rule():
    with open(os.path.join(_ROOT, "docs", "REFERENCES.md"), encoding="utf-8") as fh:
        doc = fh.read()
    for rule in R.RULE_REFERENCES:
        assert f"`{rule}`" in doc, rule


def test_helpers_and_link_policy():
    assert R.reference_ids_for("economizer_high_limit")[0] == "pnnl-guide-economizer"
    assert R.references_for("no_such_rule") == []
    f = [Finding(rule="reheat_penalty", equip="V", severity="warn", summary="")]
    got = R.references_for_findings(f, kinds=(R.GUIDE,))
    assert [r.id for r in got] == ["pnnl-guide-zone-heat-cool"]
    h = R.links_html(["pnnl-guide-economizer", "nope"])
    assert h.count("<a ") == 1 and "rel='noopener noreferrer'" in h
    assert R.links_text(["pnnl-retuning-ch8", "nope"]) == "pnnl-retuning-ch8"
    assert R.reference("pnnl-retuning-ch8").short() == "PNNL re-tuning ch. 8"
    assert set(R.reference_urls()) == {r.url for r in R.REFERENCES.values()}
    # link only: no PDF from the publisher lives in the repository
    for dirpath, dirs, files in os.walk(_ROOT):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("site", "build")]
        assert not [f for f in files if f.lower().endswith(".pdf")], dirpath


def test_linkcheck_script_carries_the_reference_urls():
    with open(os.path.join(_ROOT, "scripts", "datasets_linkcheck.py")) as fh:
        src = fh.read()
    assert "camber.references" in src and "reference_urls" in src


# --------------------------------------------------------------------------- cause-keyed actions


def _dcv(sev, **m):
    return Finding(rule="dcv_verification", equip="R", severity=sev, metrics=m, summary="")


def test_functioning_dcv_with_a_high_floor_is_told_to_lower_it():
    rec = recommend(
        _dcv("warn", status="functioning", excess_at_low_demand_pct=100.0, below_floor_pct=1.0)
    )
    assert rec.title == "Lower the minimum outdoor air at low demand"
    assert "Enable" not in rec.title and "Ra·Az" in rec.suggested
    assert rec.references[0] == "pnnl-guide-min-oa"


@pytest.mark.parametrize(
    "sev,metrics,title",
    [
        ("warn", {"status": "static"}, "Enable / repair demand-controlled ventilation"),
        ("warn", {"status": "uncorrelated"}, "Tie outdoor air to demand"),
        ("fault", {"status": "functioning", "below_floor_pct": 30.0}, "Restore the minimum"),
        ("fault", {"status": "static", "co2_breach_at_min_pct": 25.0}, "Make outdoor air respond"),
        ("fault", {"unventilated_high_co2_hours": 9.0}, "Restore ventilation during occupied"),
        # a fault under a lower configured threshold still reads its under-ventilation cause
        ("fault", {"status": "functioning", "below_floor_pct": 4.0}, "Restore the minimum"),
    ],
)
def test_dcv_action_follows_the_cause(sev, metrics, title):
    rec = recommend(_dcv(sev, **metrics))
    assert rec.title.startswith(title)
    if sev == "fault":
        assert "Cuts over-ventilation" not in rec.expected_effect


def test_system_dcv_reads_the_worst_air_handlers_cause():
    per = {
        "AHU-1": {"severity": "ok", "status": "functioning"},
        "AHU-2": {"severity": "warn", "status": "functioning", "excess_at_low_demand_pct": 80.0},
    }
    f = Finding(
        rule="dcv_system_verification",
        equip="<fleet>",
        severity="warn",
        metrics={"per_ahu": per},
        summary="",
    )
    rec = recommend(f)
    assert rec.title == "Lower the minimum outdoor air at low demand" and "AHU-2" in rec.action


def _f(rule, sev="warn", **m):
    return Finding(rule=rule, equip="E", severity=sev, metrics=m, summary="")


def test_other_rules_follow_their_cause():
    low_dt = recommend(_f("chw_plant_reset", low_deltaT_pct=40.0, chwst_reset_present=True))
    assert low_dt.title == "Fix low chilled-water loop ΔT"
    flat = recommend(_f("chw_plant_reset", low_deltaT_pct=3.0, chwst_reset_present=False))
    assert flat.title == "Reset the chilled-water supply temperature"
    at_min = recommend(_f("hw_pump_dp_reset", pct_running_near_min=70.0, pct_running_near_full=0))
    assert at_min.title.startswith("Right-size the pump") and "hot-water" in at_min.action
    has_reset = recommend(
        _f("chw_pump_dp_reset", pct_running_near_full=45.0, dp_sp_reset_present=True)
    )
    assert has_reset.title.startswith("Find why") and "already resets" in has_reset.action
    no_reset = recommend(
        _f("chw_pump_dp_reset", pct_running_near_full=45.0, dp_sp_reset_present=False)
    )
    assert no_reset.title.startswith("Reset the pump")
    sat_ok = recommend(_f("supply_air_reset", sp_behaviour="reset", reset_direction="flat"))
    assert sat_ok.title == "Widen the supply-air-temperature reset range"
    sat_cap = recommend(_f("supply_air_reset", reset_direction="rising_with_load"))
    assert sat_cap.title.startswith("Check cooling capacity")
    sat_none = recommend(_f("supply_air_reset", reset_direction="flat", sp_behaviour="flat"))
    assert sat_none.title == "Enable supply-air-temperature reset"
    tower = recommend(_f("cooling_tower_approach", effort_gated=True))
    assert tower.title.startswith("Restore cooling-tower capacity")
    assert recommend(_f("cooling_tower_approach", effort_gated=None)).title.startswith("Reset")
    dual = recommend(_f("reheat_minimization_g36"))
    assert dual.title == "Implement the dual-maximum heating sequence"
    assert recommend(_f("reheat_penalty")).title.startswith("Minimize reheat")
    oc = recommend(_f("overcooling_min_flow"))
    assert "cooling setpoint" not in oc.suggested and "Raise the zone" not in oc.action
    ocs = recommend(_f("overcooling_severity", shortfall_severity=None))
    assert ocs.title == "Investigate zone overcooling" and "heating shortfall" in ocs.action
    under = recommend(_f("outdoor_air_fraction", failure_mode="under_ventilation"))
    assert under.references[0] == "pnnl-guide-min-oa"
    assert recommend(_f("outdoor_air_fraction")).references[0] == "pnnl-guide-economizer"


def test_recommendation_severity_is_the_findings():
    for sev in ("warn", "fault"):
        for rule in RECOMMENDERS:
            rec = recommend(_f(rule, sev))
            if rec is not None:
                assert rec.severity == sev and rec.rule == rule


# --------------------------------------------------------------------------- reports


def _findings():
    return [
        Finding(
            rule="dcv_verification",
            equip="ROOM",
            severity="warn",
            metrics={"status": "functioning", "excess_at_low_demand_pct": 100.0},
            summary="ROOM: OA rises with demand (DCV functioning); OA above the floor at low "
            "demand 100%",
        )
    ]


def test_audit_title_is_neutral_without_std211_inputs():
    r = AuditReport(building="Room", level=2)
    r.add_findings(_findings())
    assert r.display_title() == "Building analytics report -- Room"
    h = r.to_html(recommend=True)
    assert "Std-211" not in h and "Energy Conservation Measures" not in h
    assert "<style>" in h and ".camber-report" in h and "<link" not in h and "<script" not in h
    # the action follows the cause, with its Learn more links; the findings table links too
    assert "Lower the minimum outdoor air at low demand" in h
    assert "Enable / repair" not in h
    assert h.count("pnnl_sa_88958.pdf") == 2 and "Learn more" in h
    txt = r.to_text()
    assert "learn more: pnnl-guide-min-oa, pnnl-retuning-ch5" in txt
    assert "Energy Conservation Measures" not in txt


def test_audit_is_std211_with_its_inputs_and_explicit_title_wins():
    r = AuditReport(building="HQ", level=2, benchmark=Benchmark(120.0, 100.0))
    assert not r.is_std211()  # Level 2 needs the ECM table too
    r.add_ecm(ECM(name="Reset SAT", finding="flat SAT", affected_system="AHU-1"))
    assert r.is_std211() and "ASHRAE Std-211 Level 2 Audit &mdash; HQ" in r.to_html()
    assert "Energy Conservation Measures" in r.to_html()
    assert AuditReport(building="B", level=1, benchmark=Benchmark(1.0, 1.0)).is_std211()
    assert AuditReport(building="B", level=2, title="My report").display_title() == "My report"


def test_html_document_wraps_with_viewport_and_escaped_title():
    d = html_document("<p>x</p>", title="A <b>")
    assert d.startswith("<!doctype html>") and "name='viewport'" in d
    assert "<title>A &lt;b&gt;</title>" in d and "<p>x</p>" in d


def test_action_plan_rows_and_html_carry_references():
    items = build_action_plan(_findings())
    rows = action_plan_rows(items)
    assert rows[0]["references"] == ["pnnl-guide-min-oa", "pnnl-retuning-ch5"]
    assert json.dumps(rows)  # JSON-friendly
    assert "camber-refs" in action_plan_html(items)
    assert items[0].as_dict()["recommendation"]["references"][0] == "pnnl-guide-min-oa"


def test_config_report_title_and_ecms(tmp_path):
    import _rcx_fixture as fx

    from camber.config import run_config

    fx.make_store(tmp_path)
    cfg = fx.config()
    cfg["report"].update(
        {
            "benchmark": {"site_eui": 90, "peer_median_eui": 80},
            "ecms": [{"name": "Reset SAT", "finding": "flat", "affected_system": "AHU"}],
            "out_html": "a.html",
        }
    )
    res = run_config(cfg, base_dir=str(tmp_path))
    assert res.report.is_std211() and len(res.report.ecms) == 1
    html = (tmp_path / "a.html").read_text()
    assert html.startswith("<!doctype html>") and "Std-211 Level 2" in html
    cfg["report"]["title"] = "Named"
    assert run_config(cfg, base_dir=str(tmp_path)).report.display_title() == "Named"


def test_rcx_further_reading_and_json_ids(tmp_path):
    import _rcx_fixture as fx

    from camber.config import run_config
    from camber.report.rcx import build_rcx_report

    fx.make_store(tmp_path)
    run = run_config(fx.config(), base_dir=str(tmp_path))
    rep = build_rcx_report(run)
    d = rep.to_dict()
    reading = [s for s in d["sections"] if s["id"] == "reading"]
    assert reading and reading[0]["slot"] is None
    ids = reading[0]["blocks"][1]["refs"]
    want = {i for iss in d["issues"] for i in iss["references"]}
    # only the guides relevant to this report's issues, plus (0.98, #88) the walk-down chapter
    # behind the "Verify on site" section
    assert set(ids) == want | {"pnnl-retuning-ch9"} and ids
    kinds = [R.REFERENCES[i].kind for i in ids]
    assert kinds == sorted(kinds, key=lambda k: k != R.GUIDE)  # guides first
    html = rep.to_html()
    assert "<h2>Further reading</h2>" in html and "Learn more:" in html
    # the section can be left out like any other
    from camber.report.rcx import RcxOptions

    o = RcxOptions(sections=("cover", "summary", "issues"))
    assert "Further reading" not in build_rcx_report(run, options=o).to_html()


# --------------------------------------------------------------------------- trend viewer + lab


def test_role_units_cover_the_common_roles():
    u = role_units()
    assert u["co2"] == "ppm" and u["outdoor_co2"] == "ppm" and u["space_temp"] == "°F"
    assert u["oa_airflow"] == "cfm" and u["cool_valve"] == "%" and u["duct_static"] == "inH₂O"
    assert u["supply_air_temp_sp"] == "°F" and u["cool_sp"] == "°F"
    # 0.96: counts carry their count unit; states and commands stay unitless (own panel)
    assert u["occupancy"] == "persons"
    assert u["sat_reset_requests"] == u["static_pressure_requests"] == "requests"
    assert u["compressor_stage"] == u["heat_stage"] == "stage"
    assert u["supply_air_humidity"] == "%RH" and u["filter_diff_press"] == "inH₂O"
    for state in ("supply_fan_status", "pump_status", "econ_cmd", "warmup"):
        assert state not in u, state


def test_role_units_leave_the_suggester_unit_table_alone():
    from camber.mapping_assist import ROLE_UNIT
    from camber.model.roles import Role

    assert not any(Role.OCCUPANCY in roles for roles in ROLE_UNIT.values())
    assert "persons" not in ROLE_UNIT


def test_trend_viewer_has_legend_units_axes_and_a_normalised_toggle():
    h = live_dashboard_html()
    for token in ("id='legend'", "id='norm'", "id='tip'", "'time ('", "niceTicks", '"co2"'):
        assert token in h, token
    # 0.96: site time when /facilities reports a zone, labelled with it; a UTC box converts;
    # hover labels follow the same rule; no zone keeps UTC
    for token in ("id='utc'", "f.timezone", "timeZone:TZ", "zoneName()", "'UTC'", "wallToUtc"):
        assert token in h, token
    assert "' UTC'" not in h  # the hover label is no longer hard-coded
    assert "<script src" not in h and "https://" not in h and h.count("http://") == 1
    js = h[h.rindex("<script>") :]
    assert "__UNITS__" not in js and "innerHTML" not in js and "eval(" not in js


def test_display_path_is_home_relative_and_short(tmp_path):
    home = str(tmp_path / "home" / "me")
    assert display_path(home + "/data/lab_store", home=home) == "~/data/lab_store"
    long = "/var/tmp/" + "x" * 60 + "/cache/finnish"
    assert display_path(long, home=home) == "…/cache/finnish"
    assert display_path("/srv/s", home=home) == "/srv/s" and display_path("", home=home) == ""


def test_lab_page_collapses_teaches_and_shows_display_paths():
    h = lab_page_html("tok")
    assert "details" in h and "what it teaches" in h and "dp.data_dir" in h
    assert "d.data_dir" not in h.replace("dp.data_dir", "")
