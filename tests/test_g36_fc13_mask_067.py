"""#67 (0.92): the G36 -> plant link reads FC13's own hours, not the any-FC union.

Synthetic fixtures only.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(__file__))

from camber import faultlab  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.g36_rule import G36AFDD  # noqa: E402
from camber.rules.triage import link_findings  # noqa: E402


def _idx(days=14):
    return pd.date_range("2025-07-07", periods=days * 24, freq="h")


def test_g36_evidence_exposes_per_fc_masks_under_the_union():
    frame = faultlab._g36_afdd(_idx(), faulty=True)
    rule = G36AFDD()
    f = rule.analyze("AHU-1", frame)
    ev = rule.evidence("AHU-1", frame)
    assert ev is not None and isinstance(ev.masks, dict)
    assert "FC13" in ev.masks and "FC13" in f.metrics["flagged_fcs"]
    fc13 = ev.masks["FC13"]
    assert fc13.index.equals(frame.index) and fc13.dtype == bool and fc13.any()
    # every per-FC mask sits inside the union mask, and the union is their OR
    union = np.zeros(len(frame), dtype=bool)
    for m in ev.masks.values():
        assert not (m & ~ev.mask).any()
        union |= m.to_numpy()
    assert (union == ev.mask.to_numpy()).all()
    # FC13's hours match the finding's FC13 hour count (hourly frame)
    assert float(fc13.sum()) == f.metrics["fc"]["FC13"]["hours"]
    # declined / omitted FCs carry no mask
    for label, entry in f.metrics["fc"].items():
        if entry["status"] in ("declined", "omitted"):
            assert label not in ev.masks


def _g36(equip="AHU-1"):
    return Finding(
        "g36_afdd", equip, "fault", {"flagged_fcs": ["FC1", "FC13"]}, f"{equip}: G36 FC1, FC13"
    )


def _tracking(equip="CH-1"):
    return Finding("chw_supply_tracking", equip, "fault", {"above_pct": 80.0}, f"{equip}: short")


def _masks():
    idx = pd.date_range("2026-07-06", periods=100, freq="h")
    n = np.arange(100)
    fc1 = pd.Series(n < 60, index=idx)  # duct static: most of the union
    fc13 = pd.Series((n >= 80) & (n < 90), index=idx)  # SAT too high: 10 h, plant fine then
    union = fc1 | fc13
    plant = pd.Series(n < 50, index=idx)  # short only in FC1 hours
    return {"AHU-1": union, "CH-1": plant}, {("AHU-1", "FC13"): fc13}


def test_plant_link_uses_the_fc13_hours_not_the_any_fc_union():
    masks, parts = _masks()
    fs = [_g36(), _tracking()]
    # before #67: the union (FC1 hours) overlaps the plant's short hours, so the link is made
    loose = link_findings(fs, mask_for=lambda f: masks.get(f.equip))
    a = next(i for i in loose if i.equip == "AHU-1")
    assert [c.equip for c in a.upstream_causes] == ["CH-1"]
    assert a.upstream_causes[0].unit_hours == "finding"
    # with FC13's own hours there is no coincidence: no link
    tight = link_findings(
        fs,
        mask_for=lambda f: masks.get(f.equip),
        part_mask_for=lambda f, part: parts.get((f.equip, part)),
    )
    a = next(i for i in tight if i.equip == "AHU-1")
    assert not a.upstream_causes


def test_plant_link_on_fc13_hours_reports_its_basis():
    masks, parts = _masks()
    idx = masks["CH-1"].index
    masks["CH-1"] = pd.Series(np.arange(100) >= 80, index=idx)  # short exactly in FC13's hours
    issues = link_findings(
        [_g36(), _tracking()],
        mask_for=lambda f: masks.get(f.equip),
        part_mask_for=lambda f, part: parts.get((f.equip, part)),
    )
    c = next(i for i in issues if i.equip == "AHU-1").upstream_causes[0]
    assert c.unit_hours == "FC13" and c.overlap_share == 1.0 and c.overlap_hours == 10.0
    why = " ".join(next(i for i in issues if i.equip == "AHU-1").why)
    assert "of this unit's FC13 hours" in why


def test_supply_air_control_keeps_its_whole_mask():
    idx = pd.date_range("2026-07-06", periods=100, freq="h")
    sac = Finding(
        "supply_air_control",
        "AHU-1",
        "fault",
        {"too_warm_pct": 60.0, "too_cold_pct": 0.0},
        "AHU-1: SAT off setpoint",
    )
    masks = {
        "AHU-1": pd.Series(np.arange(100) < 40, index=idx),
        "CH-1": pd.Series(np.arange(100) < 30, index=idx),
    }
    issues = link_findings(
        [sac, _tracking()],
        mask_for=lambda f: masks.get(f.equip),
        part_mask_for=lambda f, part: None,
    )
    c = next(i for i in issues if i.equip == "AHU-1").upstream_causes[0]
    assert c.unit_hours == "finding" and c.overlap_share == 0.75


def test_rcx_context_part_mask_for_reads_evidence_masks():
    from camber.charts.evidence import Evidence
    from camber.report.rcx import _Ctx

    idx = _idx(1)
    m = pd.Series(np.arange(len(idx)) < 5, index=idx)
    ctx = object.__new__(_Ctx)
    ctx._evidence = {}
    f = _g36()
    ctx._evidence[id(f)] = Evidence("multitrend", mask=m, masks={"FC13": m})
    got = ctx.part_mask_for(f, "FC13")
    assert got is not None and int(got.sum()) == 5
    assert ctx.part_mask_for(f, "FC12") is None
    g = _tracking()
    ctx._evidence[id(g)] = Evidence("multitrend", mask=m)
    assert ctx.part_mask_for(g, "FC13") is None
