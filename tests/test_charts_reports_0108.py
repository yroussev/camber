"""0.108 regressions for the charts, the evidence engine and the non-RCx reports (#258-#265).
Rendering runs headless on Agg."""

import builtins
import os
import re
import subprocess
import sys

import matplotlib

matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import matplotlib.dates as mdates  # noqa: E402
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from camber.charts._labels import mpl_text  # noqa: E402
from camber.charts.carpet import carpet_matrix, load_carpet  # noqa: E402
from camber.charts.evidence import finding_evidence, render_evidence  # noqa: E402
from camber.charts.multitrend import fault_multitrend, mask_to_spans  # noqa: E402
from camber.charts.quality_dashboard import quality_matrix  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.overcooling import analyze_overcooling  # noqa: E402
from camber.report.dashboard import build_dashboard, render_evidence_blocks  # noqa: E402
from camber.report.fleet import build_fleet_report  # noqa: E402
from camber.report.linking import (  # noqa: E402
    _BRUSH_JS,
    carpet_svg_html,
    interactive_scatter_html,
    multitrend_svg_html,
)
from camber.report.site import build_site_report  # noqa: E402
from camber.rules.overcooling_rule import OvercoolingMinFlow  # noqa: E402
from camber.rules.triage import rank_findings  # noqa: E402


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


# --------------------------------------------------------------------------- #258 overcooling


def _overcool_frame():
    """Occupied: 1 F below the cooling setpoint at minimum flow with reheat; nights 6 F below."""
    idx = pd.date_range("2025-07-07", periods=24 * 14, freq="h")
    occ = (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 18)
    f = pd.DataFrame(index=idx)
    f[Role.SPACE_TEMP] = np.where(occ, 74.0, 69.0)
    f[Role.COOL_SP] = 75.0
    f[Role.AIRFLOW] = 300.0
    f[Role.AIRFLOW_SP] = 300.0
    f[Role.DAMPER] = 20.0
    f[Role.HEAT_VALVE] = np.where(occ, 40.0, 0.0)
    return f, pd.Series(occ, index=idx)


def test_overcooling_evidence_shades_the_rules_own_samples():
    frame, occ = _overcool_frame()
    rule = OvercoolingMinFlow()
    finding = rule.analyze("VAV-1", frame)
    assert finding.severity == "fault"
    ev = finding_evidence(rule, "VAV-1", frame)
    mask = ev.mask
    # every shaded sample is one the verdict counted: occupied, at min flow, reheat open
    assert int(mask.sum()) == int(occ.sum()) == 110
    assert not (mask & ~occ).any()
    n = finding.metrics["n_considered"]
    assert round(100.0 * mask.sum() / n, 2) == finding.metrics["overcool_with_reheat_pct"]
    # the chart shows the at-min-flow test and the reheat, not only the temperatures
    assert ev.roles == [
        Role.SPACE_TEMP,
        Role.COOL_SP,
        Role.AIRFLOW,
        Role.AIRFLOW_SP,
        Role.HEAT_VALVE,
    ]
    assert set(ev.masks) == {"overcooling at min flow", "with reheat"}
    ax, _ = render_evidence(ev, frame)
    assert len(ax.get_lines()) == 5 and ax.patches


def test_overcooling_evidence_without_valve_shades_at_min_flow():
    frame, occ = _overcool_frame()
    frame = frame.drop(columns=[Role.HEAT_VALVE])
    ev = finding_evidence(OvercoolingMinFlow(), "VAV-1", frame)
    assert Role.HEAT_VALVE not in ev.roles
    assert int(ev.mask.sum()) == 110 and not (ev.mask & ~occ).any()


def test_analyze_overcooling_masks_out_match_the_percentages():
    frame, _ = _overcool_frame()
    legacy = frame.rename(
        columns={
            Role.SPACE_TEMP: "SpaceTemp",
            Role.COOL_SP: "ActCoolSP",
            Role.AIRFLOW: "ActFlow",
            Role.AIRFLOW_SP: "ActFlowSP",
            Role.DAMPER: "Damper",
            Role.HEAT_VALVE: "HWValve",
        }
    )
    legacy.iloc[3:200:7, legacy.columns.get_loc("ActFlow")] = 900.0  # off the minimum
    out: dict = {}
    res = analyze_overcooling(legacy, "VAV-1", masks_out=out)
    assert set(out) == {"satisfied", "overcool", "with_reheat"}
    for m in out.values():
        assert m.index.equals(legacy.index) and m.dtype == bool
    n = res.n_considered
    assert round(100.0 * out["overcool"].sum() / n, 2) == res.overcool_at_minflow_pct
    assert round(100.0 * out["with_reheat"].sum() / n, 2) == res.overcool_with_reheat_pct
    assert analyze_overcooling(legacy, "VAV-1") == res  # masks_out changes nothing else


# --------------------------------------------------------------------------- #259 UTF-8 writes


@pytest.fixture
def cp1252_locale(monkeypatch):
    """Simulate a cp1252 locale: a text-mode open() without an encoding gets cp1252."""
    real_open = builtins.open

    def fake_open(file, mode="r", *args, **kw):
        if "b" not in mode and len(args) < 2 and kw.get("encoding") is None:
            kw["encoding"] = "cp1252"
        return real_open(file, mode, *args, **kw)

    monkeypatch.setattr(builtins, "open", fake_open)
    return fake_open


def test_report_written_as_utf8_under_cp1252(tmp_path, cp1252_locale):
    from test_cli import _make_config

    from camber.cli import main

    cfg = _make_config(str(tmp_path))
    dest = tmp_path / "rep.html"
    assert main(["report", cfg, "--out", str(dest)]) == 0
    text = dest.read_bytes().decode("utf-8")  # strict: the file is valid UTF-8
    assert "charset='utf-8'" in text and "≥" in text


def test_fleet_written_as_utf8_document_under_cp1252(tmp_path, cp1252_locale):
    from test_cli import _make_config

    from camber.cli import main

    _make_config(str(tmp_path / "a"), site="Building ≥ A")
    _make_config(str(tmp_path / "b"), site="Building B")
    dest = tmp_path / "fleet.html"
    assert main(["fleet", str(tmp_path / "*" / "config.json"), "--out", str(dest)]) == 0
    text = dest.read_bytes().decode("utf-8")
    assert text.startswith("<!doctype html>") and "<meta charset='utf-8'>" in text
    assert "Building ≥ A" in text and text.rstrip().endswith("</html>")


def test_fleet_document_wraps_the_fragment():
    r = build_fleet_report([{"site": "S", "eui": 50.0, "findings": []}])
    doc = r.to_html_document()
    assert r.to_html() in doc and doc.startswith("<!doctype html>")


# --------------------------------------------------------------------------- #260 escaping

_MARKER = "<i class='camber-probe'>probe</i>"


def test_interactive_dashboard_escapes_point_names():
    idx = pd.date_range("2024-01-01", periods=200, freq="15min")
    df = pd.DataFrame({"OAT": np.linspace(30, 50, 200), _MARKER: np.linspace(0, 1, 200)}, index=idx)
    html = build_dashboard(df, sections=("B", "E"), interactive=True, carpet_col=_MARKER)
    assert _MARKER not in html
    assert "&lt;i class=&#x27;camber-probe&#x27;&gt;" in html


def test_linking_fragments_escape_labels_and_title():
    idx = pd.date_range("2024-01-01", periods=3, freq="h")
    frag = interactive_scatter_html([1, 2, 3], [3, 2, 1], idx, xlabel=_MARKER, ylabel=_MARKER)
    assert _MARKER not in frag
    carpet = carpet_svg_html(pd.Series([1.0, 2.0, 3.0], index=idx), title=_MARKER)
    assert _MARKER not in carpet and "&lt;i" in carpet


# --------------------------------------------------------------------------- #261 fleet ties


def test_fleet_top_findings_break_ties_by_rule():
    from types import SimpleNamespace as F

    rules = ["simultaneous_heat_cool", "leaking_valve", "control_hunting", "unmet_setpoint_hours"]
    b = [
        {
            "site": "A",
            "eui": None,
            "findings": [F(rule=r, severity="warn", equip="X") for r in rules],
        },
        {
            "site": "B",
            "eui": None,
            "findings": [F(rule="leaking_valve", severity="warn", equip="Y")],
        },
    ]
    top = build_fleet_report(b, top_n=3).fleet_top_rules
    assert top == [("leaking_valve", 2), ("control_hunting", 1), ("simultaneous_heat_cool", 1)]


def test_fleet_top_findings_independent_of_hash_seed():
    code = (
        "from types import SimpleNamespace as F\n"
        "from camber.report.fleet import build_fleet_report\n"
        "rules=['simultaneous_heat_cool','leaking_valve','control_hunting','unmet_setpoint_hours',"
        "'filter_fouling','co2_ventilation','airflow_tracking']\n"
        "fs=[F(rule=r,severity='warn',equip='X') for r in rules]\n"
        "b=[{'site':'A','eui':None,'findings':fs}]\n"
        "print([r for r,_ in build_fleet_report(b, top_n=3).fleet_top_rules])\n"
    )
    outs = set()
    for seed in ("1", "2", "3"):
        env = {**os.environ, "PYTHONHASHSEED": seed, "PYTHONPATH": _ROOT}
        res = subprocess.run(
            [sys.executable, "-c", code], env=env, capture_output=True, text=True, check=True
        )
        outs.add(res.stdout.strip())
    assert len(outs) == 1


# --------------------------------------------------------------------------- #262 spans


def _shaded_hours(df, mask):
    ax = fault_multitrend(df, ["sat"], spans={"v": mask})
    return [round(p.get_width() * 24, 2) for p in ax.patches]


def test_each_flagged_sample_is_shaded_over_its_own_interval():
    idx = pd.date_range("2024-07-01", periods=24, freq="h")
    df = pd.DataFrame({"sat": np.arange(24.0)}, index=idx)
    for hours in ([8], [8, 9], [8, 9, 10]):
        mask = pd.Series(idx.hour.isin(hours), index=idx)
        assert _shaded_hours(df, mask) == [float(len(hours))]
    # the last sample of the frame is shaded over one step too
    last = pd.Series(idx.hour == 23, index=idx)
    assert mask_to_spans(last) == [(idx[23], idx[23] + pd.Timedelta("1h"))]


def test_spans_split_at_data_gaps():
    idx = pd.DatetimeIndex(
        list(pd.date_range("2024-07-01 20:00", periods=4, freq="h"))
        + list(pd.date_range("2024-07-03 08:00", periods=4, freq="h"))
    )
    mask = pd.Series([False, False, False, True, True, False, False, False], index=idx)
    assert mask_to_spans(mask) == [
        (pd.Timestamp("2024-07-01 23:00"), pd.Timestamp("2024-07-02 00:00")),
        (pd.Timestamp("2024-07-03 08:00"), pd.Timestamp("2024-07-03 09:00")),
    ]


def test_spans_accept_an_unsorted_mask():
    idx = pd.date_range("2024-07-01", periods=6, freq="h")
    mask = pd.Series([False, True, True, False, False, False], index=idx)[::-1]
    assert mask_to_spans(mask) == [(idx[1], idx[3])]


# --------------------------------------------------------------------------- #263 carpets


def _gappy_series():
    idx = pd.date_range("2024-01-01", "2024-01-03 23:00", freq="h").append(
        pd.date_range("2024-01-20", "2024-01-22 23:00", freq="h")
    )
    return pd.Series(1.0, index=idx)


def test_carpet_matrix_keeps_missing_days_as_blank_columns():
    mat, _, dates = carpet_matrix(_gappy_series())
    assert mat.shape == (24, 22)
    assert pd.Timestamp(dates[0]) == pd.Timestamp("2024-01-01")
    assert pd.Timestamp(dates[-1]) == pd.Timestamp("2024-01-22")
    assert np.isnan(mat[:, 3:19]).all() and not np.isnan(mat[:, :3]).any()
    ax = load_carpet(_gappy_series())
    assert "22 days" in ax.get_title()


def test_carpet_matrix_continuous_across_dst():
    idx = pd.date_range("2024-03-08", "2024-03-12 23:00", freq="h", tz="America/New_York")
    s = pd.Series(1.0, index=idx).drop(idx[(idx.day == 10)])
    mat, _, dates = carpet_matrix(s)
    assert mat.shape[1] == 5 and np.isnan(mat[:, 2]).all()


def test_svg_carpet_places_days_on_a_continuous_axis():
    svg = carpet_svg_html(_gappy_series(), width=250, height=260)
    xs = sorted({float(x) for x in re.findall(r"x='([0-9.]+)'", svg)})
    cw = (250 - 30) / 22
    assert len(xs) == 6
    assert xs[3] == pytest.approx(30 + 19 * cw, abs=0.11)  # Jan 20 is column 19


# --------------------------------------------------------------------------- #264 dollar signs


def test_mpl_text_escapes_dollars_once():
    assert mpl_text("VAV$_$1") == r"VAV\$_\$1"
    assert mpl_text(mpl_text("VAV $5 to $10")) == r"VAV \$5 to \$10"
    assert mpl_text(None) == ""


@pytest.mark.parametrize("equip", ["VAV$_$1", "VAV $5 to $10"])
def test_evidence_renders_for_dollar_equipment_names(equip):
    frame, _ = _overcool_frame()
    rule = OvercoolingMinFlow()
    finding = rule.analyze(equip, frame)
    html = render_evidence_blocks(
        rank_findings([finding], actionable_only=True), {rule.name: rule}, lambda _e: frame
    )
    assert html.count("<figure>") == 1
    fig, ax = plt.subplots()
    render_evidence(finding_evidence(rule, equip, frame), frame, ax=ax)
    fig.canvas.draw()
    assert ax.get_title().startswith(equip.replace("$", r"\$"))


def test_dashboard_with_dollar_column_builds():
    idx = pd.date_range("2024-01-01", periods=200, freq="15min")
    df = pd.DataFrame(
        {"VAV$2d1_$20Temp": np.random.default_rng(0).normal(70, 1, 200), "OAT": np.arange(200.0)},
        index=idx,
    )
    html = build_dashboard(df, sections=("A", "B", "E", "I"))
    assert html.count("<img src='data:image/png") == 4 and "chart-missing" not in html


def test_a_failing_section_image_leaves_a_note(monkeypatch):
    import camber.report.dashboard as dash

    def boom(*_a, **_k):
        raise RuntimeError("cannot draw")

    monkeypatch.setattr(dash, "readiness_ribbon", boom)
    idx = pd.date_range("2024-01-01", periods=48, freq="h")
    df = pd.DataFrame({"sat": np.linspace(50, 60, 48)}, index=idx)
    html = build_dashboard(df, sections=("A", "I"))
    assert "chart-missing" in html and html.count("<img src='data:image/png") == 1
    assert plt.get_fignums() == []  # the failed figure was closed


# --------------------------------------------------------------------------- #265 robustness


def test_svg_trend_breaks_at_nan_and_skips_all_nan_columns():
    idx = pd.date_range("2024-01-01", periods=6, freq="h")
    df = pd.DataFrame(
        {"a": [1.0, 2, np.nan, np.nan, 3, 4], "b": np.nan, "c": [5.0, 6, 7, 8, 9, 10]}, index=idx
    )
    svg = multitrend_svg_html(df, ["a", "b", "c"])
    polys = re.findall(r"points='([^']*)'", svg)
    assert [len(p.split()) for p in polys] == [2, 2, 6]  # a in two runs, b skipped, c whole
    bottom = 300 - 24
    assert not any(p.endswith(f",{bottom:.1f}") and len(p.split()) == 6 for p in polys[:2])
    assert multitrend_svg_html(df, ["b"]) == ""


def test_brush_range_is_computed_without_spreading_the_points():
    assert "Math.min.apply" not in _BRUSH_JS and "Math.max.apply" not in _BRUSH_JS


@pytest.mark.parametrize("interactive", [False, True])
def test_dashboard_and_site_on_an_empty_frame(interactive):
    empty = pd.DataFrame({"sat": pd.Series([], dtype=float)}, index=pd.DatetimeIndex([]))
    assert "<html>" in build_dashboard(empty, interactive=interactive)
    assert "<html>" in build_site_report(empty)


@pytest.mark.parametrize("interactive", [False, True])
def test_text_columns_are_skipped(interactive):
    idx = pd.date_range("2024-01-01", periods=48, freq="h")
    df = pd.DataFrame({"mode": ["occ"] * 48, "sat": np.linspace(1, 2, 48)}, index=idx)
    html = build_dashboard(df, interactive=interactive)
    assert "chart-missing" not in html
    assert "chart-missing" not in build_site_report(df)
    _, _, points, _ = quality_matrix(df)
    assert points == ["sat"]


def test_unit_normalisation_keeps_same_unit_series_comparable():
    idx = pd.date_range("2024-01-01", periods=4, freq="h")
    df = pd.DataFrame(
        {
            Role.SPACE_TEMP: [70.0, 72, 74, 76],
            Role.COOL_SP: 75.0,
            Role.AIRFLOW: [100.0, 200, 300, 400],
        },
        index=idx,
    )
    ax = fault_multitrend(df, list(df.columns), normalize="unit")
    sp = ax.get_lines()[1].get_ydata()
    assert np.allclose(sp, (75 - 70) / (76 - 70))  # the setpoint sits on the temperature scale
    assert np.allclose(ax.get_lines()[2].get_ydata(), [0, 1 / 3, 2 / 3, 1])
    assert mdates  # the module is importable headless
