"""#32: the RCx week view covers water-side plants, and G36 advice needs a declared G36 sequence."""

import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.config import run_config  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.report import rcx as rcx_mod  # noqa: E402
from camber.report.rcx import PLANT_FAMILIES, build_rcx_report, select_week  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.store import ParquetStore  # noqa: E402


@pytest.fixture
def captured(monkeypatch):
    figs = []

    def fake(fig, fmt="png", dpi=150):
        import matplotlib.pyplot as plt

        figs.append(fig)
        plt.close(fig)
        return f"data:image/{fmt};base64,AAAA"

    monkeypatch.setattr(rcx_mod, "_render", fake)
    return figs


def chw_plant_frame(weeks=3, seed=0):
    idx = pd.date_range(fx.START, periods=24 * 7 * weeks, freq="h")
    rng = np.random.default_rng(seed)
    h = idx.hour.to_numpy()
    load = np.clip(np.sin((h - 6) / 24 * 2 * np.pi), 0, None) * (idx.dayofweek < 5)
    return pd.DataFrame(
        {
            Role.OAT: 70 + 12 * np.sin((h - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, len(idx)),
            Role.CHW_SUPPLY_TEMP: 44 + rng.normal(0, 0.3, len(idx)),
            Role.CHW_RETURN_TEMP: 44 + 10 * load + rng.normal(0, 0.3, len(idx)),
            Role.CW_SUPPLY_TEMP: 80 + rng.normal(0, 0.5, len(idx)),
            Role.CHW_DIFF_PRESS: 12 + rng.normal(0, 0.2, len(idx)),
            Role.POWER: 50 + 150 * load + rng.normal(0, 2, len(idx)),
            Role.CHW_FLOW: 400 + 300 * load,
            Role.PUMP_STATUS: (load > 0).astype(float),
        },
        index=idx,
    )


def hw_plant_frame(weeks=3, seed=1):
    idx = pd.date_range(fx.START, periods=24 * 7 * weeks, freq="h")
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            Role.HW_SUPPLY_TEMP: 160 + rng.normal(0, 1, len(idx)),
            Role.HW_RETURN_TEMP: 140 + rng.normal(0, 1, len(idx)),
            Role.HW_DIFF_PRESS: 8 + rng.normal(0, 0.2, len(idx)),
            Role.BOILER_STATUS: np.ones(len(idx)),
        },
        index=idx,
    )


def _plant_run(tmp_path, cls, frame, marker, rules):
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(frame, facility_id=fx.FID, equip="Plant1", equip_class=cls)
    st.register_facility(fx.FID, name="Demo facility")
    cfg = {
        "site": "Demo facility",
        "source": {"kind": "store", "store": "store", "facility_id": fx.FID},
        "equipment": [{"class": cls, "marker_role": marker}],
        "rules": rules,
        "report": {"layout": "rcx", "rcx": {}},
    }
    return run_config(cfg, base_dir=str(tmp_path))


@pytest.mark.parametrize(
    "cls, frame, marker, rules, labels",
    [
        (
            "CHW_PLANT",
            chw_plant_frame(),
            "power",
            ["chiller_efficiency"],
            {"Water temperatures", "Loop differential pressure", "Electric power", "Status"},
        ),
        (
            "HW_PLANT",
            hw_plant_frame(),
            "hw_supply_temp",
            ["hw_pump_dp_reset"],
            {"Water temperatures", "Loop differential pressure", "Status"},
        ),
    ],
)
def test_plant_reports_get_a_representative_week(
    tmp_path, captured, cls, frame, marker, rules, labels
):
    rep = build_rcx_report(_plant_run(tmp_path, cls, frame, marker, rules))
    assert not rep.week.declined, rep.week.reason
    assert rep.week.start == pd.Timestamp(fx.START)
    week = next(s for s in rep.sections if s["id"] == "week")
    assert any(b["kind"] == "figure" for b in week["blocks"])
    titles = {ax.get_title() for fig in captured for ax in fig.axes}
    assert {f"Plant1: {t}" for t in labels} <= titles
    assert "Representative week declined" not in rep.to_html()


def test_select_week_scores_plants_on_the_plant_roles():
    chw = chw_plant_frame()
    chw.iloc[: 24 * 4] = np.nan  # the plant logged nothing for the first four days
    frames = {"AHU": fx.ahu_frame(weeks=3), "CHW": chw}
    plant_roles = tuple(r for _u, _t, roles in PLANT_FAMILIES for r in roles)
    # on the air-side roles alone the plant contributes nothing; per-equipment roles count it
    air_only = select_week(frames)
    both = select_week(frames, roles_for=lambda e: plant_roles if e == "CHW" else rcx_mod._P3_ROLES)
    assert not both.declined
    assert both.candidates[0]["coverage"] != air_only.candidates[0]["coverage"]
    # an air-only site is unchanged by passing the per-equipment roles
    ahu = {"AHU": frames["AHU"]}
    assert (
        select_week(ahu).as_dict()
        == select_week(ahu, roles_for=lambda e: rcx_mod._P3_ROLES).as_dict()
    )


def test_a_plant_without_its_data_still_declines_honestly(tmp_path):
    empty = select_week({"CHW": chw_plant_frame().iloc[:0]})
    assert empty.declined and "no equipment data" in empty.reason


# --------------------------------------------------------------------------- G36 advice


def _run_with(tmp_path, finding, soo=None):
    fx.make_store(tmp_path)
    cfg = fx.config()
    if soo:
        cfg["soo"] = soo
    run = run_config(cfg, base_dir=str(tmp_path))
    # the finding stands alone on its unit (not linked under another issue there)
    run.findings[:] = [f for f in run.findings if f.equip != finding.equip] + [finding]
    return run


def _stuck_damper_g36(equip="DemoAHU2"):
    return Finding(
        "reheat_minimization_g36",
        equip,
        "warn",
        metrics={"reheat_hours": 40.0},
        summary="reheating while the box sits above its G36 minimum airflow",
    )


def test_no_g36_advice_without_a_declared_g36_sequence(tmp_path, captured):
    rep = build_rcx_report(_run_with(tmp_path, _stuck_damper_g36()))
    html = rep.to_html()
    assert "Apply G36 reheat minimization" not in html
    assert "no ASHRAE Guideline 36 sequence is declared for this unit" in html
    page = next(s for s in rep.sections if "Reheat minimization g36" in s["title"])
    text = " ".join(b.get("text", "") for b in page["blocks"] if b["kind"] == "p")
    assert "Recommended action: engineer to specify" in text
    assert "Suggested:" not in text


def test_g36_advice_when_the_class_declares_a_g36_sequence(tmp_path, captured):
    run = _run_with(tmp_path, _stuck_damper_g36(), soo=[{"class": "AHU", "library": "g36_ahu"}])
    html = build_rcx_report(run).to_html()
    assert "Apply G36 reheat minimization" in html
    assert "no ASHRAE Guideline 36 sequence is declared" not in html


def test_g36_worded_advice_on_a_generic_rule_is_qualified():
    rec = types.SimpleNamespace(
        title="Minimize reheat",
        action="Apply G36 reheat minimization: x.",
        parameter="",
        suggested="",
    )
    iss = types.SimpleNamespace(root=Finding("reheat_penalty", "V1", "warn"), equip="V1")
    title, action, _ = rcx_mod._advice({"g36_declared": lambda e: False}, iss, rec)
    assert title == "Minimize reheat"
    assert action.startswith("Apply G36 reheat minimization: x.")
    assert "G36 reference practice" in action
    _t, plain, _s = rcx_mod._advice({"g36_declared": lambda e: True}, iss, rec)
    assert plain == "Apply G36 reheat minimization: x."


def test_g36_declaration_by_class_and_for_terminals():
    refs = [
        types.SimpleNamespace(equip="A", equip_class="AHU"),
        types.SimpleNamespace(equip="V", equip_class="VAV"),
        types.SimpleNamespace(equip="C", equip_class="CHW_PLANT"),
    ]
    none = rcx_mod._g36_declared_for({}, refs)
    assert not any(none(e) for e in "AVC")
    ahu = rcx_mod._g36_declared_for({"soo": [{"class": "AHU", "library": "g36_ahu"}]}, refs)
    assert ahu("A") and ahu("V") and not ahu("C")
    spec = rcx_mod._g36_declared_for({"soo": [{"class": "AHU", "spec": "mine.json"}]}, refs)
    assert not spec("A")
