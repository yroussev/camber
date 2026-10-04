"""0.101 (#108): the walk-down's equipment item follows the cause that heads the RCx issue.

When a member finding's cause heads an issue (#101), the "Verify on site" equipment item checks
that cause, not the root's. Synthetic fixtures only."""

from test_rcx_cause_lead_101 import _stuck_damper_run

from camber import walkdown as W
from camber.references import WALKDOWN_REFERENCES, reference_ids_for
from camber.report import build_rcx_report
from camber.rules.base import Finding
from camber.rules.triage import Issue

ECON = W.SITE_CHECKS["outdoor_air_fraction"]
STUCK_ITEM = ECON["damper_not_delivering"]
VENT_ITEM = ECON["*"]


def _issue(key="k1", rank=1, fcm_cause="damper_not_delivering"):
    root = Finding("outdoor_air_fraction", "AHU-1", "fault", {"oaf_median_pct": 5.0})
    fcm = Finding("free_cooling_missed", "AHU-1", "fault", {"missed_cause": fcm_cause})
    return Issue(
        key=key, root=root, members=[root, fcm], severity="fault", equip="AHU-1", rank=rank
    )


def _equipment(checks):
    return [c for c in checks if c.kind == "equipment"]


def test_without_a_heading_cause_the_root_item_stays():
    (c,) = _equipment(W.site_checks([_issue()]))
    assert (c.look_at, c.rule) == (VENT_ITEM.look_at, "outdoor_air_fraction")
    # naming the root's own cause is the same as naming none
    same = W.site_checks([_issue()], heading_causes={"k1": ("outdoor_air_fraction", "*")})
    assert [x.as_dict() for x in same] == [x.as_dict() for x in W.site_checks([_issue()])]


def test_the_heading_cause_picks_the_equipment_item():
    heads = {"k1": ("free_cooling_missed", "damper_not_delivering")}
    (c,) = _equipment(W.site_checks([_issue()], heading_causes=heads))
    assert c.look_at == STUCK_ITEM.look_at and c.point == STUCK_ITEM.point
    assert c.rule == "free_cooling_missed" and c.issue_key == "k1" and c.rank == 1
    assert c.references[: len(reference_ids_for("free_cooling_missed"))] == list(
        reference_ids_for("free_cooling_missed")
    )
    assert set(WALKDOWN_REFERENCES) <= set(c.references)


def test_only_the_named_issue_changes():
    a, b = _issue("k1", 1), _issue("k2", 2)
    b.equip = "AHU-2"
    heads = {"k2": ("free_cooling_missed", "damper_not_delivering")}
    got = {c.issue_key: c.look_at for c in _equipment(W.site_checks([a, b], heading_causes=heads))}
    assert got == {"k1": VENT_ITEM.look_at, "k2": STUCK_ITEM.look_at}


def test_a_heading_rule_without_templates_gets_its_generic_item():
    heads = {"k1": ("economizer_damper_drift", "*")}
    (c,) = _equipment(W.site_checks([_issue()], heading_causes=heads))
    assert c.rule == "economizer_damper_drift"
    assert c.look_at == W._generic("economizer_damper_drift", None).look_at


def test_report_walkdown_matches_the_heading(tmp_path):
    rep = build_rcx_report(_stuck_damper_run(tmp_path))
    (iss,) = rep.to_dict()["issues"]
    assert iss["cause_rule"] == "free_cooling_missed"
    verify = next(s for s in rep.sections if s["id"] == "verify")
    rows = [r for b in verify["blocks"] if b.get("kind") == "table" for r in b["rows"]]
    looks = [r[2] for r in rows]
    assert STUCK_ITEM.look_at in looks
    assert VENT_ITEM.look_at not in looks
