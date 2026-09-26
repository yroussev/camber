"""The RCx report layout (camber.report.rcx): week selection, issue pages, tiers, notes, golden."""

import json
import os
import re
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.config import run_config  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.report import rcx as rcx_mod  # noqa: E402
from camber.report.audit import RESEARCH_ONLY_BANNER  # noqa: E402
from camber.report.rcx import (  # noqa: E402
    RcxOptions,
    build_rcx_report,
    load_notes,
    notes_template,
    select_week,
)
from camber.rules.triage import Issue  # noqa: E402

GOLDEN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "golden", "rcx_synthetic.html")


@pytest.fixture
def run(tmp_path):
    fx.make_store(tmp_path)
    return run_config(
        fx.config(loads={"DemoAHU2": {"heating_capacity_kbtuh": 400}}), base_dir=str(tmp_path)
    )


@pytest.fixture
def captured(monkeypatch):
    """Record every figure the report renders (Axes data, not pixels)."""
    figs = []

    def fake(fig, fmt="png", dpi=150):
        import matplotlib.pyplot as plt

        figs.append(fig)
        plt.close(fig)
        return f"data:image/{fmt};base64,AAAA"

    monkeypatch.setattr(rcx_mod, "_render", fake)
    return figs


# --------------------------------------------------------------------------- select_week


def _frames(weeks=4, **kw):
    return {"DemoAHU": fx.ahu_frame(weeks=weeks, **kw)}


def _issue(mask, rank=1, conditional=False):
    iss = Issue(key=f"k{rank}", root=None, members=[], mask=mask, rank=rank)
    if conditional:
        iss.conditional_on = ["x"]
    return iss


def test_select_week_picks_the_planted_fault_week():
    frames = _frames()
    idx = frames["DemoAHU"].index
    planted = pd.Series((idx >= "2026-03-16") & (idx < "2026-03-23") & (idx.hour == 14), index=idx)
    w = select_week(frames, issues=[_issue(planted)])
    assert not w.declined and w.start == pd.Timestamp("2026-03-16")
    assert w.components["evidence"] == 1.0 and w.score == pytest.approx(1.1)
    assert w.runner_up is not None and w.runner_up["score"] == pytest.approx(0.1)
    # the explanation is built only from the numbers it reports
    for num in ("2026-03-16", "1.100", "1.000", "1.00", "0.100", "4 of 4"):
        assert num in w.explanation
    # a conditional issue counts half; a lower-ranked one 1/rank
    w2 = select_week(frames, issues=[_issue(planted, rank=2, conditional=True)])
    assert w2.components["evidence"] == 0.25


def test_select_week_oat_range_prefers_a_window_crossing_the_high_limit():
    frames = _frames()
    fr = frames["DemoAHU"]
    # every week but the third stays below 60 °F; the third swings across 70 °F
    cool = fr.index < "2026-03-16"
    fr.loc[cool | (fr.index >= "2026-03-23"), Role.OAT] = (
        50 + (fr.index.hour.to_numpy() % 5)[cool | (fr.index >= "2026-03-23")]
    )
    w = select_week(frames, mode="oat_range", high_limit_f=70.0)
    assert w.start == pd.Timestamp("2026-03-16")
    assert w.components["crosses_high_limit"] == 1.0
    others = [c for c in w.candidates if c["start"] != w.start]
    assert all(c["components"]["crosses_high_limit"] == 0.0 for c in others)


def test_select_week_tie_breaks_to_the_earliest_week_deterministically():
    frames = _frames()
    a = select_week(frames, mode="evidence")  # no issues -> every eligible week ties
    assert a.start == pd.Timestamp("2026-03-02") and "tie -> earliest" in a.explanation
    b = select_week(dict(reversed(list(frames.items()))), mode="evidence")
    assert b.start == a.start and b.score == a.score


def test_select_week_declines_on_sparse_data():
    fr = fx.ahu_frame(weeks=3)
    gappy = [c for c in fr.columns if c != Role.SUPPLY_FAN_STATUS]
    fr.loc[fr.index.hour % 3 != 0, gappy] = np.nan  # a third of the samples survive
    w = select_week({"DemoAHU": fr})
    assert w.declined and w.start is None
    assert "coverage" in w.reason and "best: week of" in w.reason
    # a fan that never runs leaves no gated samples at all
    off = fx.ahu_frame(weeks=2)
    off[Role.SUPPLY_FAN_STATUS] = 0.0
    assert select_week({"DemoAHU": off}).declined
    assert select_week({}).declined


def test_select_week_typical_and_fixed_modes():
    frames = _frames()
    t = select_week(frames, mode="typical")
    assert not t.declined and "rms_from_median_f" in t.components
    f = select_week(frames, mode="fixed:2026-03-18")  # a Wednesday -> its Monday
    assert f.start == pd.Timestamp("2026-03-16") and "as requested" in f.explanation
    out = select_week(frames, mode="fixed:2025-01-01")
    assert out.declined and "outside the data" in out.reason
    sparse = fx.ahu_frame(weeks=2)
    sparse.loc["2026-03-09":, Role.SUPPLY_FAN_STATUS] = 0.0
    bad = select_week({"DemoAHU": sparse}, mode="fixed:2026-03-10")
    assert bad.declined and "not usable" in bad.reason
    with pytest.raises(ValueError):
        select_week(frames, mode="loudest")


def test_week_mode_aliases():
    assert RcxOptions(week="auto").week_mode() == "evidence"
    assert RcxOptions(week="oat-range").week_mode() == "oat_range"
    assert RcxOptions(week="2026-03-10").week_mode() == "fixed:2026-03-10"
    assert RcxOptions(week="typical").week_mode() == "typical"


# --------------------------------------------------------------------------- the report


def test_report_structure_banner_issues_and_evidence(run, captured):
    rep = build_rcx_report(run)
    html = rep.to_html()
    body = html.split("<body", 1)[1].split(">", 1)[1]
    assert body.lstrip().startswith("<div class='camber-nc-banner'")
    assert RESEARCH_ONLY_BANNER in html
    # the banner repeats on every printed page
    assert re.search(r"@media print\{.*\.camber-nc-banner\{position:fixed", html, re.S)
    for css in ("break-before:page", "break-inside:avoid", "table-header-group",
                "print-color-adjust:exact", "counter(pages)", "size:letter"):  # fmt: skip
        assert css in html
    assert "<script" not in html.lower()
    ids = [s["id"] for s in rep.sections]
    assert ids[:4] == ["cover", "summary", "data", "week"]
    assert ids[-5:] == [f"appendix-{c}" for c in "abcde"]
    # ranked issues: the costed simultaneous-heat/cool chain first, economizer chain grouped
    top = rep.issues[0]
    assert top.equip == "DemoAHU2" and "simultaneous_heat_cool" in top.rules and top.cost > 0
    econ = next(i for i in rep.issues if "economizer_high_limit" in i.rules)
    assert econ.rules == ["economizer_high_limit", "outdoor_air_fraction"]
    assert econ.cost is None and "uncosted" in econ.cost_basis_note
    assert rep.kpis["annual_cost_usd"] == pytest.approx(top.cost)
    assert f"id='issue-{econ.key}'" in html and f"href='#issue-{econ.key}'" in html
    assert rep.week.start == pd.Timestamp("2026-03-09")  # the planted economizer fault week
    # json-friendly
    d = json.loads(json.dumps(rep.to_dict()))
    assert d["issues"][0]["key"] == top.key and d["week"]["mode"] == "evidence"
    assert d["slots"] == rep.slots()


def test_p1_is_size_budgeted(tmp_path, captured):
    fx.make_store(tmp_path)
    run = run_config(fx.config(top_n=2), base_dir=str(tmp_path))
    rep = build_rcx_report(run)
    summ = next(s for s in rep.sections if s["id"] == "summary")
    table = next(b for b in summ["blocks"] if b["kind"] == "table")
    assert len(table["rows"]) == 2
    assert all(len(c) <= 200 for r in table["rows"] for c in r)
    assert any("more issue(s)" in b.get("text", "") for b in summ["blocks"])


def test_economizer_chart_uses_the_rules_configured_envelope(run, captured):
    build_rcx_report(run, options=RcxOptions(sections=("economizer",)))
    econ_axes = [
        ax for fig in captured for ax in fig.axes if "economizer lockout" in ax.get_title()
    ]
    assert econ_axes, "no economizer chart rendered"
    ax = econ_axes[0]
    assert "68°F" in ax.get_title() and "35%" in ax.get_title()  # min_oa 30 + margin 5
    rule = run.registry.get("economizer_high_limit")
    frame = run.frame_for("DemoAHU")
    f = next(x for x in run.findings if x.rule == "economizer_high_limit" and x.equip == "DemoAHU")
    # the red ("violating") points are exactly the samples the verdict counted
    red = next(c for c in ax.collections if c.get_label() == "violating")
    ev = rule.evidence("DemoAHU", frame)
    drawn = ev.frame.dropna()
    judged = int((drawn[Role.OAT] > 68.0).sum())
    assert round(100.0 * len(red.get_offsets()) / judged, 2) == f.metrics["not_locked_out_pct"]


def test_sat_census_tier3_declines_and_keeps_g36_out_of_the_dollars(tmp_path, captured):
    fx.make_store(tmp_path)
    cfg = fx.config(loads={"DemoAHU": {"heating_capacity_kbtuh": 400}})
    # hold SAT colder than the G36 map on DemoAHU so the compliance rule warns
    run = run_config(cfg, base_dir=str(tmp_path))
    fake = next(f for f in run.findings if f.rule == "supply_air_reset_compliance")
    fake.severity = "warn"
    fake.metrics.update(pct_below_g36_target=90.0, mean_gap_f=4.0)
    rep = build_rcx_report(run, options=RcxOptions.from_config(cfg["report"]))
    sat = next(s for s in rep.sections if s["id"] == "sat")
    banners = [b["text"] for b in sat["blocks"] if b["kind"] == "banner"]
    assert any("verdict declined: no site sequence known" in t for t in banners)
    assert not any("G36 map" in ax.get_legend_handles_labels()[1][0] for fig in captured
                   for ax in fig.axes if ax.get_legend_handles_labels()[1])  # fmt: skip
    iss = next(i for i in rep.issues if "supply_air_reset_compliance" in i.rules)
    assert iss.cost is None and "no site sequence known" in iss.cost_basis_note
    assert (
        iss.confidence == "L" or iss.conditional or iss.root.rule != "supply_air_reset_compliance"
    )
    # asked for explicitly, the G36 line appears -- labelled a reference, never a verdict
    captured.clear()
    opts = RcxOptions.from_config(cfg["report"])
    opts.g36_reference, opts.sections = True, ("sat",)
    build_rcx_report(run, options=opts)
    labels = [
        lbl for fig in captured for ax in fig.axes for lbl in ax.get_legend_handles_labels()[1]
    ]
    assert "G36 map — reference, not a verdict" in labels


def test_sat_census_tier2_declared_sequence_and_tier1_setpoint(tmp_path, captured):
    fx.make_store(tmp_path)
    cfg = fx.config(sequence={"sat_reset": {"oat": [50, 70], "sat": [60, 55], "tol_f": 2}})
    run = run_config(cfg, base_dir=str(tmp_path))
    rep = build_rcx_report(run, options=RcxOptions.from_config(cfg["report"]))
    sat = next(s for s in rep.sections if s["id"] == "sat")
    texts = " ".join(b.get("text", "") for b in sat["blocks"])
    assert "tier 2 (declared site sequence)" in texts and "fan-on, occupied hours" in texts
    # a declared sequence re-judges the compliance findings against the site's own map, so they
    # are verdicts (and priced) -- the fixture holds SAT at 55 °F, colder than this 60 °F reset
    seq = {"sat_reset": {"oat": [70, 80], "sat": [62, 60], "tol_f": 1}}
    cfg = fx.config(sequence=seq, loads={"DemoAHU": {"heating_capacity_kbtuh": 400}})
    run = run_config(cfg, base_dir=str(tmp_path))
    rep = build_rcx_report(run)
    iss = next(
        i for i in rep.issues if "supply_air_reset_compliance" in i.rules and i.equip == "DemoAHU"
    )
    comp = next(f for f in iss.members if f.rule == "supply_air_reset_compliance")
    assert comp.metrics["reset_source"] == "site sequence (report.rcx.sequence)"
    assert iss.cost is not None and "excluded" not in iss.cost_basis_note

    # tier 1: a trended SAT setpoint
    st = fx.make_store(tmp_path / "t1")
    fr = fx.ahu_frame()
    fr[Role.SUPPLY_AIR_TEMP_SP] = 55.0
    st.write_role_frame(fr, facility_id=fx.FID, equip="DemoAHU", equip_class="AHU")
    run = run_config(fx.config(), base_dir=str(tmp_path / "t1"))
    rep = build_rcx_report(run, options=RcxOptions(sections=("sat",)))
    texts = " ".join(b.get("text", "") for s in rep.sections for b in s["blocks"])
    assert "tier 1 (trended SAT setpoint)" in texts


def test_oat_reference_drift_makes_economizer_conditional(tmp_path, captured):
    fx.make_store(tmp_path)
    oat = fx.ahu_frame(fault_week=1)[Role.OAT]
    pd.DataFrame({"time": oat.index, "oat_f": oat.to_numpy() - 8.0}).to_csv(
        tmp_path / "ref.csv", index=False
    )
    cfg = fx.config(oat_reference={"csv": "ref.csv"})
    run = run_config(cfg, base_dir=str(tmp_path))
    rep = build_rcx_report(run)
    drift = next(i for i in rep.issues if i.rules == ["sensor_drift:oat"])
    econ = next(i for i in rep.issues if "economizer_high_limit" in i.rules)
    assert econ.conditional and econ.confidence == "L"
    assert {f.rule for f in drift.dependents} >= {"economizer_high_limit"}
    ec = next(s for s in rep.sections if s["id"] == "economizer")
    assert any(b["kind"] == "banner" and "conditional on OAT" in b["text"] for b in ec["blocks"])
    data = next(s for s in rep.sections if s["id"] == "data")
    assert any(b["kind"] == "table" and "Bias °F" in b["header"] for b in data["blocks"])


def test_fan_off_flatlines_are_not_counted_as_stuck(tmp_path, captured):
    fx.make_store(tmp_path)
    st = fx.make_store(tmp_path)
    fr = fx.ahu_frame()
    fr.loc["2026-03-11":"2026-03-15", Role.SUPPLY_FAN_STATUS] = 0.0  # a long shutdown
    fr.loc["2026-03-11":"2026-03-15", Role.SUPPLY_AIR_TEMP] = 70.0
    st.write_role_frame(fr, facility_id=fx.FID, equip="DemoAHU", equip_class="AHU")
    run = run_config(fx.config(), base_dir=str(tmp_path))
    rep = build_rcx_report(run, options=RcxOptions(sections=("data",)))
    table = next(b for s in rep.sections for b in s["blocks"] if b["kind"] == "table"
                 and b["header"][:2] == ["Equipment", "Point"])  # fmt: skip
    row = next(r for r in table["rows"] if r[:2] == ["DemoAHU", "supply_air_temp"])
    assert "stuck" not in row[2] and row[4] == "fan status"
    assert not any(i.conditional for i in rep.issues)


def test_notes_matched_orphaned_escaped_and_template(run, captured, tmp_path):
    rep0 = build_rcx_report(run)
    key = rep0.issues[0].key
    notes = {
        "exec_summary": {
            "text": "Walked the site.\n\nAll AHUs <b>ran</b>.",
            "author": "A. Engineer",
            "date": "2026-03-30",
        },  # fmt: skip
        f"issue:{key}": "Valve actuator replaced <script>alert(1)</script>",
        "section:week": [{"text": "Chosen week matches the complaint log."}],
        "issue:deadbeef0000": {"text": "Old issue, since resolved."},
        "section:nope": "",
    }
    p = tmp_path / "notes.json"
    p.write_text(json.dumps(notes))
    rep = build_rcx_report(run, notes=load_notes(str(p)))
    html = rep.to_html()
    assert "Engineer&#x27;s note — A. Engineer, 2026-03-30" in html
    assert "<p>Walked the site.</p><p>All AHUs &lt;b&gt;ran&lt;/b&gt;.</p>" in html
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html and "<script>" not in html
    assert set(rep.orphans) == {"issue:deadbeef0000"}
    appx = html.split("id='appendix-e'", 1)[1]
    assert "Old issue, since resolved." in appx and "[issue:deadbeef0000]" in appx
    tmpl = notes_template(rep)
    assert "exec_summary" in tmpl and f"issue:{key}" in tmpl and "section:week" in tmpl
    assert load_notes(None) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2]")
    with pytest.raises(ValueError):
        load_notes(str(bad))


def test_lifecycle_notes_follow_the_issue_key(tmp_path, captured):
    from camber.faultlifecycle import FaultLifecycle

    fx.make_store(tmp_path)
    cfg = fx.config(lifecycle=True)
    cfg["faults"] = {"store": "faults.json", "run_id": "r1"}
    run = run_config(cfg, base_dir=str(tmp_path))
    lc = FaultLifecycle.load(str(tmp_path / "faults.json"))
    rec = next(r for r in lc.records() if r.rule == "economizer_high_limit")
    lc.add_note(rec.fingerprint, "2026-03-25: damper linkage slipped")
    lc.save()
    rep = build_rcx_report(run)
    econ = next(i for i in rep.issues if "economizer_high_limit" in i.rules)
    assert any("damper linkage slipped" in n["text"] for n in rep.notes[f"issue:{econ.key}"])


def test_html_escapes_untrusted_names(tmp_path, captured):
    st = fx.make_store(tmp_path)
    st.write_role_frame(
        fx.ahu_frame(fault_week=0), facility_id=fx.FID, equip="<img src=x>", equip_class="AHU"
    )
    run = run_config(fx.config(), base_dir=str(tmp_path))
    html = build_rcx_report(run).to_html()
    assert "<img src=x>" not in html and "&lt;img src=x&gt;" in html


def test_svg_charts_and_a4(run):
    rep = build_rcx_report(run, options=RcxOptions(chart_format="svg", paper="a4",
                                                   sections=("week",)))  # fmt: skip
    html = rep.to_html()
    assert "data:image/svg+xml;base64," in html and "size:A4" in html


def test_no_fan_signal_is_reported_as_ungated(tmp_path, captured):
    from camber.store import ParquetStore

    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(fx.ahu_frame(fan=False), facility_id=fx.FID, equip="DemoAHU",
                        equip_class="AHU")  # fmt: skip
    st.register_facility(fx.FID, name="Demo facility")
    run = run_config(fx.config(), base_dir=str(tmp_path))
    rep = build_rcx_report(run)
    html = rep.to_html()
    assert "ungated — no fan signal" in html
    assert "<div class='camber-nc-banner'" not in html


# --------------------------------------------------------------------------- golden


def _normalize(html: str) -> str:
    html = re.sub(r"data:image/(png|svg\+xml);base64,[A-Za-z0-9+/=]+",
                  r"data:image/\1;base64,<N bytes>", html)  # fmt: skip
    return re.sub(r"CAMBER \d+\.\d+\.\d+(\S*)", "CAMBER <version>", html)


def test_golden_synthetic_report(run):
    got = _normalize(build_rcx_report(run).to_html())
    if os.environ.get("CAMBER_UPDATE_GOLDEN") == "1" or not os.path.exists(GOLDEN):
        os.makedirs(os.path.dirname(GOLDEN), exist_ok=True)
        with open(GOLDEN, "w") as fh:
            fh.write(got)
        if os.environ.get("CAMBER_UPDATE_GOLDEN") != "1":
            pytest.fail("golden file was missing and has been written; re-run the test")
    with open(GOLDEN) as fh:
        want = fh.read()
    assert got == want, "rcx golden changed -- inspect the diff, then CAMBER_UPDATE_GOLDEN=1"


def test_box_by_hour_bins_by_hour_and_drops_masked_samples():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from camber.charts.boxhour import box_by_hour, hourly_groups

    idx = pd.date_range("2026-03-02", periods=48, freq="h")
    s = pd.Series(idx.hour.astype(float), index=idx)
    on = pd.Series((idx.hour >= 6) & (idx.hour < 18), index=idx)
    groups = hourly_groups(s, mask=on)
    assert sorted(groups) == list(range(6, 18))
    assert all((v == h).all() and len(v) == 2 for h, v in groups.items())
    fig, ax = plt.subplots()
    box_by_hour(s, mask=on, ax=ax, ylabel="in.w.c.")
    assert [t.get_text() for t in ax.get_xticklabels()][:3] == ["0", "2", "4"]
    assert "24 samples" in ax.get_title()
    plt.close(fig)


def test_mv_section_lists_baselines_and_declines(run, captured):
    from camber.rules.base import Finding

    run.findings += [
        Finding("mv_baseline", "DemoMeter", "ok", {"model": "3PC", "r2": 0.91, "cv_rmse": 0.12}),
        Finding("mv_baseline", "OtherMeter", "info", {"declined": True}, "OtherMeter: declined"),
    ]
    rep = build_rcx_report(run, options=RcxOptions(sections=("mv", "appendix")))
    mv = next(s for s in rep.sections if s["id"] == "mv")
    rows = next(b for b in mv["blocks"] if b["kind"] == "table")["rows"]
    assert rows[0][:5] == ["DemoMeter", "ok", "3PC", "0.91", "12.0%"]
    assert rows[1][1] == "declined"
    appx = next(s for s in rep.sections if s["id"] == "appendix-a")
    decl = next(b for b in appx["blocks"] if b["kind"] == "table")
    assert ["mv_baseline", "OtherMeter", "OtherMeter: declined"] in decl["rows"]
    assert rep.kpis["n_declined"] == 1
    # no drift and no M&V -> no section at all
    assert all(s["id"] != "mv" for s in build_rcx_report(
        run_config(fx.config(), base_dir=run.base_dir), options=RcxOptions(sections=("mv",))
    ).sections)  # fmt: skip


def test_oat_reference_fetch_is_opt_in_and_errors_are_reported(run, captured, monkeypatch):
    from camber import weather_source

    calls = []

    def fake(lat, lon, start, end, *, tz="UTC", **kw):
        calls.append((lat, lon, tz))
        oat = run.frame_for("DemoAHU")[Role.OAT]
        return (oat + 0.2).rename("oat_f")

    monkeypatch.setattr(weather_source, "oat_reference", fake)
    fetch = {"fetch": "nasa_power", "latitude": 1.0, "longitude": 2.0, "tz": "Etc/UTC"}
    opts = RcxOptions(sections=("data",), oat_reference=fetch)
    rep = build_rcx_report(run, options=opts)
    assert calls == [(1.0, 2.0, "Etc/UTC")]
    data = rep.sections[0]
    ref = next(b for b in data["blocks"] if b["kind"] == "table" and "Bias °F" in b["header"])
    assert ref["rows"][0][0] == "ok"
    bad = RcxOptions(sections=("data",), oat_reference={"csv": "/no/such/file.csv"})
    texts = [b.get("text", "") for b in build_rcx_report(run, options=bad).sections[0]["blocks"]]
    assert any("could not be loaded" in t for t in texts)
