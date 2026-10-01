"""0.91 integration: the pieces the four 0.91 branches share (synthetic fixtures only).

* ``g36_afdd`` is gated by the central applicability table like every other class-gated built-in,
  and a built-in rule's ``equip_classes`` attribute is the table's entry;
* the plant-capacity link reads the ``g36_afdd`` finding's FC13;
* the RCx issue page names the plant as the upstream cause and points the action at it.
"""

import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from camber import faultlab  # noqa: E402
from camber.rules.applicability import RULE_EQUIP_CLASSES, rule_equip_classes  # noqa: E402
from camber.rules.base import Finding, _class_declined  # noqa: E402
from camber.rules.builtin import builtin_registry  # noqa: E402
from camber.rules.chwplant_rule import CHWSupplyTracking  # noqa: E402
from camber.rules.g36_rule import G36AFDD  # noqa: E402
from camber.rules.triage import is_sat_high, link_findings  # noqa: E402


class _Ref:
    def __init__(self, equip, equip_class):
        self.equip, self.equip_class = equip, equip_class


def _idx(days=14):
    return pd.date_range("2025-07-07", periods=days * 24, freq="h")


# --------------------------------------------------------------------------- class gating


def test_builtin_equip_classes_attribute_is_the_table_entry():
    reg = builtin_registry()
    seen = []
    for name in reg.names():
        rule = reg.get(name)
        if hasattr(type(rule), "equip_classes") and type(rule).__module__.startswith("camber."):
            seen.append(name)
            assert name in RULE_EQUIP_CLASSES, name
            assert tuple(rule.equip_classes) == RULE_EQUIP_CLASSES[name], name
            assert rule_equip_classes(rule) == RULE_EQUIP_CLASSES[name]
    assert {"g36_afdd", "supply_air_reset_compliance"} <= set(seen)


def test_g36_afdd_is_gated_by_the_air_handler_family():
    rule = builtin_registry().get("g36_afdd")
    for cls in ("AHU", "RTU", "DOAS", "MAU", "AHU_DOAS", "", "Plant"):
        assert _class_declined(rule, _Ref("x", cls)) is None, cls
    for cls in ("VAV", "HEAT_PUMP", "FCU", "CHILLER"):
        f = _class_declined(rule, _Ref("x", cls))
        assert f is not None and f.metrics["declined"], cls


# --------------------------------------------------------------------------- plant link <- G36


def test_g36_afdd_fc13_reads_as_sat_high():
    idx = _idx()
    bad = G36AFDD().analyze("AHU-1", faultlab._g36_afdd(idx, faulty=True))
    ok = G36AFDD().analyze("AHU-1", faultlab._g36_afdd(idx, faulty=False))
    assert "FC13" in bad.metrics["flagged_fcs"] and is_sat_high(bad)
    assert "FC13" not in ok.metrics["flagged_fcs"] and not is_sat_high(ok)
    # FC12 / FC1 are not plant-capacity symptoms; an older finding without the list falls back to
    # the per-FC table
    assert not is_sat_high(Finding("g36_afdd", "A", "warn", {"flagged_fcs": ["FC12", "FC1"]}))
    fc = {"FC13": {"status": "evaluated", "pct": 40.0}}
    assert is_sat_high(Finding("g36_afdd", "A", "warn", {"fc": fc}))
    fc = {"FC13": {"status": "evaluated", "pct": 1.0}}
    assert not is_sat_high(Finding("g36_afdd", "A", "warn", {"fc": fc}))


def _linked():
    idx = _idx()
    ahu = G36AFDD().analyze("AHU-1", faultlab._g36_afdd(idx, faulty=True))
    plant = CHWSupplyTracking().analyze("CH-1", faultlab._chw_tracking(idx, faulty=True))
    assert plant.severity in ("warn", "fault")
    return link_findings([ahu, plant]), ahu


def test_plant_link_recognises_the_g36_afdd_fc13_finding():
    issues, ahu = _linked()
    a = next(i for i in issues if i.equip == "AHU-1")
    p = next(i for i in issues if i.equip == "CH-1")
    assert [c.equip for c in a.upstream_causes] == ["CH-1"]
    assert p.downstream == [ahu] and a.members == [ahu]


# --------------------------------------------------------------------------- RCx issue page


class _Ctx:
    registry = None
    dpi = 72

    def frame(self, equip):
        return None


def test_rcx_issue_page_names_the_plant_upstream_and_advises_it_first():
    from camber.aso import recommend
    from camber.report.rcx import _advice, _sec_issue

    issues, _ahu = _linked()
    a = next(i for i in issues if i.equip == "AHU-1")
    p = next(i for i in issues if i.equip == "CH-1")
    S = {"ctx": _Ctx(), "exclude_cost": lambda f: ""}
    _t, action, _s = _advice(S, a, recommend(a.root))
    assert action.startswith("Check the chilled-water plant first (CH-1)")
    sec = _sec_issue(S, a)
    banners = [b["text"] for b in sec["blocks"] if b.get("kind") == "banner"]
    assert any(t.startswith("Upstream cause: plant short of setpoint") for t in banners)
    text = " ".join(str(b) for b in sec["blocks"])
    # 0.98 (#88): "Recommended action — {title}: {action}"
    assert ": Check the chilled-water plant first" in text and "Recommended action — " in text
    # the plant page lists what it may explain; no plant-first advice on the plant itself
    psec = _sec_issue(S, p)
    ptext = " ".join(str(b) for b in psec["blocks"])
    assert "Downstream findings this plant shortfall may explain" in ptext
    assert "g36_afdd on AHU-1" in ptext
    assert not _advice(S, p, recommend(p.root))[1].startswith("Check the chilled-water plant")


# --------------------------------------------------------------------------- #63 x #61


def test_supply_air_reset_setpoint_classifier_runs_on_air_handlers_only(tmp_path):
    import numpy as np

    from camber.model.mapping import MappingProvider
    from camber.model.roles import Role
    from camber.resolve import discover_store
    from camber.store import ParquetStore

    idx = pd.date_range("2025-07-07", periods=21 * 24, freq="h")
    oat = 60 + 15 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi)
    # a coil that can't hold its flat 52 F setpoint on hot hours: SAT "resets" with OAT, the
    # setpoint says it does not
    fr = pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: 52.0 + np.clip(oat - 62.0, 0, None) * 0.5,
            Role.SUPPLY_AIR_TEMP_SP: np.full(len(idx), 52.0),
            Role.COOL_VALVE: np.full(len(idx), 60.0),
            Role.OAT: oat,
        },
        index=idx,
    )
    st = ParquetStore(str(tmp_path / "s"))
    for eq, cls in (("AHU-1", "AHU"), ("RTU-1", "RTU"), ("VAV-1", "VAV")):
        st.write_role_frame(fr, facility_id="f", equip=eq, equip_class=cls)
    refs = discover_store(st, "f")
    out = {f.equip: f for f in builtin_registry().run("supply_air_reset", refs, MappingProvider())}
    assert out["VAV-1"].metrics.get("declined")  # a VAV's discharge air is not an AHU's SAT
    for eq in ("AHU-1", "RTU-1"):
        f = out[eq]
        assert f.metrics["sp_behaviour"] == "flat" and f.severity == "warn", eq
        assert "NOT RESET (setpoint flat" in f.summary
