"""0.100 (#101): a member finding's equipment-level cause leads the RCx issue heading.

When the root of an issue's chain names a symptom or a sequence cause and a member names the
component at fault (a damper that does not deliver, a valve that does not follow its demand), the
member's cause heads the issue; the action stays the root's. Synthetic fixtures only."""

import os

import numpy as np
import pandas as pd

from camber.aso import recommend
from camber.config import run_config
from camber.model.roles import Role
from camber.report import build_rcx_report
from camber.report.rcx import _EQUIPMENT_CAUSES, _cause_lead, _equipment_cause, _issue_dict
from camber.rules.base import Finding
from camber.rules.triage import CAUSE_CHAINS
from camber.store import ParquetStore
from camber.walkdown import SITE_CHECKS

STUCK = "Outdoor-air damper not modulating (stuck low)"


def _f(rule, severity="fault", **m):
    return Finding(rule, "AHU-1", severity, dict(m))


def _oaf_low():
    return _f("outdoor_air_fraction", oaf_median_pct=5.0, min_oa_pct=20.0)


def _fcm(cause="damper_not_delivering"):
    return _f("free_cooling_missed", missed_cause=cause, commanded_open_oaf_median_pct=4.4)


class _Iss:
    """The attributes of :class:`camber.rules.triage.Issue` that the heading reads."""

    key, rank, equip, severity, chain = "k", 1, "AHU-1", "fault", "econ"
    cost = None
    cost_basis_note = ""
    conditional_on: list = []
    dependents: list = []
    hours_union = fan_on_hours = pct_runtime = None
    fan_gate = ""
    confidence = "M"
    confidence_components: dict = {}
    why: list = []

    def __init__(self, *members):
        self.members = list(members)
        self.root = self.members[0]
        self.rules = [m.rule for m in self.members]


# ---------------------------------------------------------------- the precedence


def test_a_member_damper_cause_leads_a_ventilation_root():
    iss = _Iss(_oaf_low(), _fcm())
    lead, cause = _cause_lead(iss)
    assert lead is iss.members[1] and cause == STUCK
    d = _issue_dict(iss)
    assert d["cause"] == STUCK and d["cause_rule"] == "free_cooling_missed"
    assert d["title"] == "Restore minimum outside air"  # the action stays the root's


def test_no_equipment_member_keeps_the_root_cause():
    for cause in ("economizer_not_commanded", "undetermined"):
        iss = _Iss(_oaf_low(), _fcm(cause))
        assert _cause_lead(iss) == (None, "")
        d = _issue_dict(iss)
        assert d["cause"] == "Outside air below the ventilation minimum"
        assert d["cause_rule"] == "outdoor_air_fraction"
    assert _cause_lead(_Iss(_fcm())) == (None, "")  # a lone finding


def test_an_equipment_root_keeps_its_heading():
    drift = _f("economizer_damper_drift")
    iss = _Iss(drift, _fcm())
    assert _equipment_cause(drift)
    assert _cause_lead(iss) == (None, "")
    assert _issue_dict(iss)["cause_rule"] == "economizer_damper_drift"


def test_the_most_upstream_equipment_member_wins():
    root = _f("supply_air_reset", sp_wrong_direction=True)
    stuck = _f("reheat_penalty", "warn", valve_divergence_share=0.4)
    tuned = _f("reheat_penalty", "warn", valve_divergence_share=0.1)
    assert _equipment_cause(stuck) and not _equipment_cause(tuned)
    iss = _Iss(root, tuned, stuck, _f("simultaneous_heat_cool"))
    lead, cause = _cause_lead(iss)
    assert lead is stuck and cause == recommend(stuck).cause
    assert cause.startswith("reheat valve stuck")
    # a member with no recommendation (not actionable) never leads
    quiet = _f("reheat_penalty", "info", valve_divergence_share=0.4)
    assert _cause_lead(_Iss(root, quiet)) == (None, "")


def test_equipment_causes_name_real_rules_and_walkdown_causes():
    chained = {r for _c, rules in CAUSE_CHAINS for r in rules}
    for rule, key in _EQUIPMENT_CAUSES:
        assert rule in chained or rule in SITE_CHECKS, rule
        if key != "*":
            assert key in SITE_CHECKS[rule], (rule, key)


# ---------------------------------------------------------------- the report


def _stuck_damper_run(tmp_path):
    """Mild weather, the OA damper commanded fully open, 5 % outside air delivered: the OA fraction
    is below a 20 % minimum (root) and free cooling is missed because the damper does not deliver
    (member)."""
    idx = pd.date_range("2025-06-02", periods=14 * 24, freq="1h")
    oat = pd.Series(50 + 5 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi), index=idx)
    rat = pd.Series(72.0, index=idx)
    frame = pd.DataFrame(
        {
            Role.OAT: oat,
            Role.RETURN_AIR_TEMP: rat,
            Role.MIXED_AIR_TEMP: 0.05 * oat + 0.95 * rat,
            Role.SUPPLY_AIR_TEMP: 55.0,
            Role.OA_DAMPER: 100.0,
            Role.COOL_VALVE: 40.0,
            Role.SUPPLY_FAN_STATUS: 1.0,
        },
        index=idx,
    )
    st = ParquetStore(os.path.join(str(tmp_path), "store"))
    st.write_role_frame(frame, facility_id="F", equip="AHU-1", equip_class="AHU")
    cfg = {
        "site": "Demo",
        "source": {"kind": "store", "store": "store", "facility_id": "F"},
        "equipment": [{"class": "AHU"}],
        "rules": [{"name": "outdoor_air_fraction", "params": {"min_oa_pct": 20.0}}],
    }
    cfg["rules"].append("free_cooling_missed")
    return run_config(cfg, base_dir=str(tmp_path))


def test_report_heads_the_issue_with_the_member_cause(tmp_path):
    rep = build_rcx_report(_stuck_damper_run(tmp_path))
    (iss,) = rep.to_dict()["issues"]
    assert iss["rules"] == ["outdoor_air_fraction", "free_cooling_missed"]
    assert (iss["cause"], iss["cause_rule"]) == (STUCK, "free_cooling_missed")
    page = next(s for s in rep.sections if s["id"].startswith("issue-"))
    assert page["title"] == f"Issue 1: {STUCK}"
    texts = [b.get("text", "") for b in page["blocks"] if b.get("kind") == "p"]
    note = next(t for t in texts if t.startswith("Cause from the free_cooling_missed member"))
    assert "(the root finding reads: Outside air below the ventilation minimum)" in note
    assert any(t.startswith("Recommended action — Restore minimum outside air:") for t in texts)
    html = rep.to_html()
    assert f"Issue 1: {STUCK}" in html
    summary = next(s for s in rep.sections if s["id"] == "summary")
    table = next(b for b in summary["blocks"] if b.get("kind") == "table")
    assert table["rows"][0][1] == STUCK  # the executive summary's Issue column
