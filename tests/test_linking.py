"""Tests for interactive linking — the brush-able inline-SVG scatter (camber.report.linking)
and its dashboard wiring. Rendering runs headless on Agg."""

import json
import os
import re
import sys

import matplotlib
matplotlib.use("Agg")  # headless, before pyplot is imported anywhere

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import pytest  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _close_figs():
    yield
    plt.close("all")


from camber.model.roles import Role  # noqa: E402
from camber.report.dashboard import _pick_link_cols, build_dashboard  # noqa: E402
from camber.report.linking import interactive_scatter_html  # noqa: E402


def _df(n=200, seed=0):
    idx = pd.date_range("2024-07-01", periods=n, freq="1h")
    rng = np.random.default_rng(seed)
    return pd.DataFrame({Role.OAT: pd.Series(rng.uniform(30, 95, n), index=idx),
                         Role.SUPPLY_AIR_TEMP: pd.Series(55 + rng.normal(0, 1, n), index=idx)})


def _payload(fragment, elem_id="camber-link"):
    m = re.search(rf"application/json' id='{elem_id}-data'>(.*?)</script>", fragment, re.S)
    return json.loads(m.group(1))


def test_fragment_has_svg_json_readout_and_brush_script():
    df = _df()
    frag = interactive_scatter_html(df[Role.OAT], df[Role.SUPPLY_AIR_TEMP], df.index)
    for token in ("<svg", "camber-link-data", "camber-link-out", "mousedown", "mouseup",
                  "application/json"):
        assert token in frag
    assert "http" not in frag.replace("http://www.w3.org", "")   # no external URLs (CSP-safe)


def test_payload_is_timestamp_and_floats_and_skips_nan():
    df = _df(n=10)
    y = df[Role.SUPPLY_AIR_TEMP].copy()
    y.iloc[3] = np.nan                                   # a gap
    frag = interactive_scatter_html(df[Role.OAT], y, df.index)
    pts = _payload(frag)
    assert len(pts) == 9                                 # the NaN point dropped
    t, x, yy = pts[0]
    assert isinstance(t, str) and isinstance(x, float) and isinstance(yy, float)


def test_pick_link_cols_defaults_to_oat_then_other():
    df = _df()
    x, y = _pick_link_cols(df, None, None)
    assert x == Role.OAT and y == Role.SUPPLY_AIR_TEMP   # OAT-like x, first other y
    # explicit override honored
    x2, y2 = _pick_link_cols(df, Role.SUPPLY_AIR_TEMP, Role.OAT)
    assert x2 == Role.SUPPLY_AIR_TEMP and y2 == Role.OAT


def test_dashboard_interactive_adds_section_style_and_script():
    df = _df()
    html = build_dashboard(df, sections=("A",), interactive=True, carpet_col=Role.OAT)
    assert "Interactive — brush to select" in html
    assert "camber-pt" in html                           # LINK_STYLE injected
    assert "camber-link-data" in html and "<script>" in html


def test_dashboard_static_by_default_has_no_interactivity():
    df = _df()
    html = build_dashboard(df, sections=("A",), carpet_col=Role.OAT)
    assert "camber-link-data" not in html and "Interactive — brush" not in html
    assert "camber-pt" not in html                       # LINK_STYLE not injected


def test_interactive_section_empty_when_not_renderable():
    # a single-column frame has no (x, y) pair -> no interactive section, no error
    idx = pd.date_range("2024-07-01", periods=20, freq="1h")
    one = pd.DataFrame({Role.OAT: pd.Series(range(20), index=idx)})
    html = build_dashboard(one, sections=("A",), interactive=True, carpet_col=Role.OAT)
    assert "camber-link-data" not in html
