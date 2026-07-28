"""Tests for the visualization MVP (readiness, multitrend, quality dashboard, HTML assembler).

Headless Agg backend; checks the chart logic + that the assembled HTML carries the expected
sections, inlined images, and ranked findings.
"""

import os
import sys

import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.charts.multitrend import fault_multitrend, mask_to_spans  # noqa: E402
from camber.charts.quality_dashboard import quality_dashboard, quality_matrix  # noqa: E402
from camber.charts.readiness import presence_matrix, readiness_ribbon  # noqa: E402
from camber.report.dashboard import build_dashboard, fig_to_base64  # noqa: E402
from camber.rules.base import Finding  # noqa: E402


def _frame(days=10, gappy=False):
    idx = pd.date_range("2024-06-01", periods=days * 24, freq="1h")
    rng = np.random.default_rng(0)
    occ = ((idx.hour >= 7) & (idx.hour <= 18) & (idx.dayofweek < 5)).astype(float)
    df = pd.DataFrame(
        {
            "load_kw": 40 + 60 * occ + rng.normal(0, 2, len(idx)),
            "sat": 55 + rng.normal(0, 1, len(idx)),
        },
        index=idx,
    )
    if gappy:
        df.loc[df.index[50:120], "sat"] = np.nan  # a coverage gap on one point
    return df


# --------------------------------------------------------------------------- A: readiness


def test_presence_matrix_and_coverage():
    df = _frame(gappy=True)
    mat, bins, cov = presence_matrix(df, max_bins=100)
    assert mat.shape[0] == 2 and mat.shape[1] <= 101  # ~max_bins (+1 boundary bin)
    assert cov[0] == 1.0 and cov[1] < 1.0  # sat has the gap


def test_readiness_ribbon_returns_axes_with_image():
    ax = readiness_ribbon(_frame())
    assert len(ax.images) == 1 and ax.get_ylabel() == ""  # ribbon drawn


def test_readiness_empty():
    ax = readiness_ribbon(pd.DataFrame())
    assert "no data" in ax.get_title()


# --------------------------------------------------------------------------- B: multitrend


def test_mask_to_spans_contiguous():
    idx = pd.date_range("2024-01-01", periods=6, freq="h")
    mask = pd.Series([False, True, True, False, True, False], index=idx)
    spans = mask_to_spans(mask)
    assert len(spans) == 2 and spans[0] == (idx[1], idx[2]) and spans[1] == (idx[4], idx[4])


def test_fault_multitrend_shades_spans():
    df = _frame()
    viol = df["load_kw"] > 90  # the "fault" mask
    ax = fault_multitrend(df, ["load_kw", "sat"], spans={"high_load": viol}, normalize=True)
    assert len(ax.lines) == 2  # both trends
    assert len(ax.patches) >= 1  # at least one shaded span
    assert any("high_load" == t.get_text() for t in ax.get_legend().get_texts())


# --------------------------------------------------------------------------- I: quality


def test_quality_matrix_goodness_orientation():
    df = _frame(gappy=True)
    raw, good, points, labels = quality_matrix(df, metrics=("coverage", "flatline_frac"))
    assert points == ["load_kw", "sat"] and labels == ["coverage", "flatline"]
    # coverage is higher-is-better (good==raw); flatline is lower-is-better (good==1-raw)
    j_cov, j_flat = 0, 1
    assert np.allclose(good[:, j_cov], raw[:, j_cov])
    assert np.allclose(good[:, j_flat], 1.0 - raw[:, j_flat])


def test_quality_dashboard_axes():
    ax = quality_dashboard(_frame(gappy=True))
    assert len(ax.images) == 1 and len(ax.get_xticklabels()) == 4


# --------------------------------------------------------------------------- assembler


def test_fig_to_base64_data_uri():
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 1])
    uri = fig_to_base64(fig)
    assert uri.startswith("data:image/png;base64,") and len(uri) > 100


def test_build_dashboard_has_sections_images_and_findings():
    df = _frame()
    findings = [
        Finding(
            rule="simultaneous_heat_cool",
            equip="AHU-1",
            severity="fault",
            metrics={"annual_cost_usd": 12000},
            summary="both coils open",
        ),
        Finding(rule="ok_rule", equip="AHU-2", severity="ok", metrics={}, summary="fine"),
    ]
    spans = {"high_load": df["load_kw"] > 90}
    html = build_dashboard(
        df, findings=findings, spans=spans, carpet_col="load_kw", rank_by="cost", title="Test DB"
    )
    assert "<html" in html and "Test DB" in html
    for letter in ("A.", "B.", "E.", "I."):
        assert letter in html  # all four sections present
    assert html.count("data:image/png;base64,") == 4  # four inlined figures
    assert "simultaneous_heat_cool" in html and "$12,000" in html  # ranked finding + cost
    assert "ok_rule" not in html  # non-actionable dropped


def test_build_dashboard_section_subset():
    df = _frame()
    html = build_dashboard(df, sections=("E", "I"), carpet_col="load_kw")
    assert html.count("data:image/png;base64,") == 2
    assert "A." not in html and "E." in html
