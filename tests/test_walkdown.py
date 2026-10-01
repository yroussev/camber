"""The walk-down checklist (camber.walkdown, 0.98 #88) and the RCx "Verify on site" section."""

import pytest

from camber import walkdown as W
from camber.aso import RECOMMENDERS, recommend
from camber.references import REFERENCES, WALKDOWN_REFERENCES
from camber.rules.base import Finding, RuleSkip
from camber.rules.builtin import builtin_registry
from camber.rules.param_docs import PARAM_DOCS
from camber.rules.triage import Issue, SensorCause


def _issue(rule, equip="AHU1", *, rank=1, metrics=None, severity="fault", members=(), cond=()):
    root = Finding(rule, equip, severity, metrics=dict(metrics or {}))
    return Issue(
        key=f"k{rank}",
        root=root,
        members=[root, *members],
        conditional_on=list(cond),
        severity=severity,
        equip=equip,
        rank=rank,
    )


def _rule_of():
    reg = builtin_registry()

    def get(name):
        try:
            return reg.get(name)
        except KeyError:
            return None

    return get


class _Trust:
    def __init__(self, trust, verdict):
        self.trust, self.verdict = trust, verdict


# --------------------------------------------------------------------------- the guards


def test_every_recommender_rule_has_a_site_check_or_uses_the_generic_one():
    for rule in RECOMMENDERS:
        assert rule in W.SITE_CHECKS or rule in W.GENERIC_SITE_CHECK, rule
    assert not (W.GENERIC_SITE_CHECK & set(W.SITE_CHECKS))


def test_templates_are_complete_and_cause_keys_resolve():
    for rule, entry in W.SITE_CHECKS.items():
        assert "*" in entry, rule  # an unlisted cause still gets the rule's item
        for key, t in entry.items():
            for tt in t if isinstance(t, tuple) else (t,):
                assert tt.kind in ("equipment", "sensor"), (rule, key)
                for text in (tt.look_at, tt.point, tt.confirms, tt.refutes):
                    assert text.strip(), (rule, key)
    for rule in W.CAUSE_KEYS:
        assert rule in W.SITE_CHECKS, rule
    assert W.WALKDOWN_REFERENCES == WALKDOWN_REFERENCES == ("pnnl-retuning-ch9",)
    assert all(r in REFERENCES for r in WALKDOWN_REFERENCES)


def test_every_dcv_cause_has_its_own_item():
    from camber.aso import _DCV_ALSO

    for cause in _DCV_ALSO:  # every cause the DCV recommender can lead with
        assert cause in W.SITE_CHECKS["dcv_verification"], cause


def test_design_params_name_real_rule_attributes():
    rule_of = _rule_of()
    for rule, spec in W.DESIGN_PARAMS.items():
        inst = rule_of(rule)
        assert inst is not None, rule
        for attr, label, unit in spec:
            assert hasattr(inst, attr), (rule, attr)
            assert label
            if unit is None:  # the unit comes from the parameter registry
                assert PARAM_DOCS[rule][attr].unit, (rule, attr)


# --------------------------------------------------------------------------- cause keys


@pytest.mark.parametrize(
    "rule,metrics,key",
    [
        ("free_cooling_missed", {"missed_cause": "damper_not_delivering"}, "damper_not_delivering"),
        (
            "free_cooling_missed",
            {"missed_cause": "economizer_not_commanded"},
            "economizer_not_commanded",
        ),
        ("free_cooling_missed", {"missed_cause": "undetermined"}, "missed_free_cooling"),
        ("outdoor_air_fraction", {"oaf_median_pct": 5.0, "min_oa_pct": 20.0}, "under_ventilation"),
        ("economizer_high_limit", {}, "excess_oa"),
        ("supply_air_reset", {"reset_direction": "rising_with_load"}, "rising_with_load"),
        ("supply_air_reset", {"sp_behaviour": "reset"}, "reset_short"),
        ("supply_air_reset", {"sp_wrong_direction": True}, "wrong_direction"),
        ("supply_air_reset", {}, "*"),
        ("reheat_penalty", {"valve_divergence_share": 0.6}, "valve_divergence"),
        ("reheat_penalty", {"valve_divergence_share": 0.1}, "*"),
        ("unmet_setpoint_hours", {"too_hot_pct": 1, "too_cold_pct": 9}, "too_cold"),
        ("supply_air_control", {"too_warm_pct": 9}, "warm"),
        ("airflow_tracking", {"overshoot_pct": 9}, "over"),
        ("chw_plant_reset", {"chwst_reset_direction": "reverse", "low_deltaT_pct": 0}, "reverse"),
        ("chw_plant_reset", {"low_deltaT_pct": 40.0}, "low_dt"),
        ("chw_plant_reset", {"low_deltaT_pct": 40.0, "flow_mode": "constant"}, "*"),
        (
            "chw_pump_dp_reset",
            {"pct_running_near_min": 80, "near_min_source": "learned"},
            "at_min_inferred",
        ),  # fmt: skip
        ("hw_pump_dp_reset", {"pct_running_near_min": 80, "near_min_source": "fixed"}, "at_min"),
        (
            "hw_pump_dp_reset",
            {"pct_running_near_full": 60, "dp_sp_reset_present": True},
            "reset_full",
        ),  # fmt: skip
        ("cooling_tower_approach", {"effort_gated": True}, "effort_gated"),
        ("leaking_valve", {"hw_leak_pct": 30.0, "chw_leak_pct": 2.0}, "heating"),
        ("leaking_valve", {}, "*"),
        ("dcv_verification", {"unventilated_high_co2_hours": 10.0}, "unventilated"),
        ("dcv_verification", {"status": "uncorrelated"}, "uncorrelated"),
        ("simultaneous_heat_cool", {}, "*"),
        ("dcv_verification", {"fan_off_occupied_pct": 60.0}, "fan_off_occupied"),
        ("actuator_stuck", {"tier": "contradicted"}, "contradicted"),
        (
            "actuator_stuck",
            {"tier": "unexplained_flat", "roles": {"heat_valve": {"tier": "contradicted"}}},
            "contradicted",
        ),  # fmt: skip
        ("actuator_stuck", {"tier": "unexplained_flat"}, "*"),
    ],
)
def test_cause_key_follows_the_metrics(rule, metrics, key):
    f = Finding(rule, "X", "fault", metrics=metrics)
    assert W.cause_key(f) == key
    assert key in W.SITE_CHECKS[rule]


# --------------------------------------------------------------------------- the builder


def test_order_sources_and_references():
    cond = SensorCause(
        "trust", "AHU1", ("duct_static_sp",), "duct_static_sp untrusted (trust 0.40)"
    )
    issues = [
        _issue("free_cooling_missed", rank=1, metrics={"missed_cause": "damper_not_delivering"}),
        _issue("static_pressure_reset", rank=2, severity="warn", cond=[cond]),
        _issue("outdoor_air_fraction", rank=3, severity="warn"),
    ]
    skipped = [
        RuleSkip("chw_plant_reset", "CH1", missing=["chw_supply_temp"]),
        RuleSkip("chw_plant_reset", "CH2", missing=["chw_supply_temp"]),
        RuleSkip("hw_pump_dp_reset", "P1", reason="no_verdict"),  # not a missing input
    ]
    declined = [Finding("supply_air_control", "AHU2", "info", metrics={"declined": True,
                "untrusted_roles": ["supply_air_temp"]})]  # fmt: skip
    trust = {"AHU1": {"duct_static_sp": _Trust(0.4, "untrusted")}}
    checks = W.site_checks(
        issues,
        recommend=recommend,
        rule_of=_rule_of(),
        overrides={"free_cooling_missed": {"high_limit_f": 60.0}},
        trust=trust,
        skipped=skipped,
        declined=declined,
    )
    kinds = [c.kind for c in checks]
    assert kinds == sorted(kinds, key=W.KINDS.index)  # sensors, equipment, design values, data
    s = [c for c in checks if c.kind == "sensor"]
    assert [(c.equip, c.point, c.issue_key, c.rank) for c in s] == [
        ("AHU1", "duct_static_sp (trust 0.40, untrusted)", "k2", 2),
        ("AHU2", "supply_air_temp", "", 0),  # Appendix A decline: no issue
    ]
    assert "setpoint at the controller" in s[0].look_at and "(trust" not in s[0].look_at
    assert "reference instrument" in s[1].look_at and "supply_air_control declined" in s[1].look_at
    eq = [c for c in checks if c.kind == "equipment"]
    assert [c.rule for c in eq] == ["free_cooling_missed", "static_pressure_reset",
                                    "outdoor_air_fraction"]  # fmt: skip
    damper = eq[0]
    assert "blades" in damper.look_at and damper.point.startswith("oa_damper")
    assert "mixed-air sensor" in damper.refutes
    # the recommendation's cause-specific references first, the walk-down chapter last
    assert damper.references[0] == "pnnl-guide-economizer"
    assert damper.references[-1] == "pnnl-retuning-ch9"
    # design values: the overridden high limit is a site value; outdoor_air_fraction's default
    # minimum is not
    dv = [c for c in checks if c.kind == "design_value"]
    assert [(c.rule, c.issue_key) for c in dv] == [("outdoor_air_fraction", "k3")]
    assert dv[0].point.startswith("Design minimum outdoor-air fraction = 20 %")
    assert "rules[outdoor_air_fraction].params.min_oa_pct" in dv[0].refutes
    # data: one item per (rule, missing set), naming every equipment; no_verdict left out
    (data,) = [c for c in checks if c.kind == "data"]
    assert (data.rule, data.equip, data.point) == ("chw_plant_reset", "CH1, CH2", "chw_supply_temp")
    assert data.references == ["pnnl-guide-plant-cooling", "pnnl-retuning-ch8", "pnnl-retuning-ch9"]
    assert all(c.as_dict()["references"] for c in checks)


def test_sensor_drift_issue_generic_rule_dedup_and_caps():
    drift = _issue("sensor_drift:oat", "AHU1", rank=1)
    cond = SensorCause("sensor_drift", "AHU1", ("oat",), "sensor_drift:oat fault (bias +6)")
    hl = _issue("economizer_high_limit", "AHU1", rank=2, cond=[cond])
    g36 = _issue("g36_afdd", "AHU1", rank=3)  # no template: the generic item from its inputs
    nothing = _issue("no_such_rule", "AHU1", rank=4)
    skipped = [
        RuleSkip("supply_air_reset", f"AHU{i}", missing=["supply_air_temp"]) for i in range(9)
    ]
    skipped.append(RuleSkip("supply_air_reset", "", reason="no_data"))
    checks = W.site_checks([drift, hl, g36, nothing], rule_of=_rule_of(), skipped=skipped)
    sensors = [c for c in checks if c.kind == "sensor"]
    assert len(sensors) == 1 and sensors[0].issue_key == "k1"  # oat on AHU1 once, first issue
    assert "sensor_drift:oat" in sensors[0].look_at
    eq = {c.rule: c for c in checks if c.kind == "equipment"}
    assert "sensor_drift:oat" not in eq  # a sensor issue gets the sensor item only
    assert "the devices and sensors behind" in eq["g36_afdd"].look_at.lower()
    assert "supply_air_temp" in eq["g36_afdd"].point
    assert eq["no_such_rule"].point == "the points this check reads"
    # design values with no overrides: economizer_high_limit's three
    dv = [c.point.split(" =")[0] for c in checks if c.kind == "design_value"]
    assert dv == [
        "Economizer high limit",
        "Differential (outdoor vs return) changeover",
        "Design minimum outdoor-air fraction",
    ]
    assert any("not set (inferred from the data)" in c.point for c in checks) is True
    data = [c for c in checks if c.kind == "data"]
    assert data[0].equip.endswith("and 3 more") and data[0].equip.startswith("AHU0, AHU1")
    assert data[1].equip == "the configured equipment" and "no data" in data[1].point


def test_empty_and_minimal_inputs():
    assert W.site_checks([]) == []
    # no rule lookup at all: still an item per issue, from the template
    (c,) = W.site_checks([_issue("simultaneous_heat_cool")])
    assert (c.kind, c.rank, c.references) == ("equipment", 1, ["pnnl-guide-ahu-heat-cool",
                                                               "pnnl-retuning-ch5",
                                                               "pnnl-retuning-ch9"])  # fmt: skip


def test_value_formatting():
    assert W._fmt_value(None, "°F") == "not set (inferred from the data)"
    assert W._fmt_value(True, "flag") == "yes" and W._fmt_value(False, "flag") == "no"
    assert W._fmt_value((0, 1, 2), "days") == "0, 1, 2"
    assert W._fmt_value(65.0, "°F") == "65 °F"
    assert W._fmt_value("auto", '% speed, or "auto"') == "auto"
    assert W._fmt_value(7, "hour") == "7"
    assert W._unit_of("no_rule", "x", None) == ""


# --------------------------------------------------------------------------- the RCx section


def _report(tmp_path, **opts):
    import _rcx_fixture as fx

    from camber.config import run_config
    from camber.report.rcx import RcxOptions, build_rcx_report

    fx.make_store(tmp_path)
    run = run_config(fx.config(), base_dir=str(tmp_path))
    return build_rcx_report(run, options=RcxOptions(**opts) if opts else None)


def test_rcx_verify_section(tmp_path):
    rep = _report(tmp_path)
    ids = [s["id"] for s in rep.sections]
    v = ids.index("verify")
    assert ids[v - 1].startswith("issue-") and ids[v + 1] == "reading"
    sec = rep.sections[v]
    assert sec["title"] == "Verify on site" and sec["slot"] == "section:verify"
    assert "section:verify" in rep.slots()
    lead = sec["blocks"][0]
    assert lead["kind"] == "links" and lead["refs"] == ["pnnl-retuning-ch9"]
    tables = [b for b in sec["blocks"] if b["kind"] == "table"]
    assert tables and all(
        t["header"] == ["#", "Equipment", "Look at", "Point", "Confirms", "Refutes"] for t in tables
    )
    first = tables[0]["rows"][0][0]
    assert first.startswith("<a href='#issue-")
    html = rep.to_html()
    assert "<h2>Verify on site</h2>" in html and "ch9_building_walkdown.pdf" in html
    assert first in html  # the anchor is rendered, not escaped
    reading = next(s for s in rep.sections if s["id"] == "reading")
    assert reading["blocks"][1]["refs"][-1] == "pnnl-retuning-ch9"


def test_rcx_verify_section_optional(tmp_path):
    rep = _report(tmp_path, sections=("cover", "summary", "issues", "reading"))
    ids = [s["id"] for s in rep.sections]
    assert "verify" not in ids
    reading = next(s for s in rep.sections if s["id"] == "reading")
    assert "pnnl-retuning-ch9" not in reading["blocks"][1]["refs"]


def test_rcx_no_verify_section_without_items(monkeypatch):
    from camber.report import rcx

    monkeypatch.setattr(W, "site_checks", lambda *a, **k: [])

    class _C:
        run = None
        overrides: dict = {}

        @staticmethod
        def rule(_n):
            return None

    S = {"ctx": _C(), "issues": [], "trust_gated": {}, "declined": []}
    assert rcx._sec_verify(S) is None
