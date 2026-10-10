"""Render the docs figures and check that the docs reference them (#113).

    python scripts/docs_figures.py                 # render every figure into docs/img/
    python scripts/docs_figures.py --only viz/     # render the figures whose path starts so
    python scripts/docs_figures.py --screenshots   # also take the browser screenshots
    python scripts/docs_figures.py --check         # verify; renders nothing, needs no matplotlib

Every figure is drawn by CAMBER's own chart code (``camber.charts``, the rules' evidence hooks,
the drift detectors' frozen-baseline bands) on deterministic synthetic data: ``camber.synth``,
the ``ahusim`` / ``driftsim`` / ``vavsim`` / ``pumpsim`` physics simulators and small seeded
generators below. No downloaded dataset is read, so no licence or site name reaches an image.

Output is byte-stable on one machine: a fixed matplotlib style, the Agg backend, a fixed dpi
and figure size, no PNG metadata (no date, no software version), and a palette quantisation
with no dithering. Other machines may differ in font rasterisation; ``--check`` does not
compare bytes.

``--screenshots`` builds a small synthetic store and site, renders the RCx report and the site
report, starts ``camber serve`` and ``camber lab`` on 127.0.0.1, and screenshots the four pages
with headless Google Chrome (``CHROME`` overrides its path). The servers are stopped
afterwards. CI has no browser, so the PNGs are committed and ``--check`` only verifies that
they exist.

The ``docs/LAB.md`` walkthrough shots (``shots/lab-*.png``) are the one exception to "no
downloaded dataset": they show the lab on the open (CC-BY-4.0) ``ornl-frp-ops`` catalog dataset,
as a learner sees it, and their captions credit it. Its file is taken from a local copy
(:data:`LAB_FROM_DIR`) through the same verified ``--from-dir`` path a learner uses, never
downloaded; without the copy those shots are skipped. A lab runs in-process for them, with a small
script appended to its page that ticks a row, opens the dialog or brushes the trend, as a learner
would by hand (the page's CSP is widened by that script's hash, for the screenshots only).

``--check`` fails when an image referenced from ``docs/**/*.md`` or ``README.md`` is missing
or over the size cap, when a docs image has no alt text or no caption line, when a generated
figure or screenshot is missing or not referenced, when a file under ``docs/img/`` is not one
this script makes (stale), or when the images together exceed the total cap.

Dev tooling; not packaged.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(ROOT, "docs")
IMG = os.path.join(DOCS, "img")

#: per-image and total size caps (bytes)
MAX_BYTES = 150 * 1024
MAX_TOTAL = 5 * 1024 * 1024

DPI = 100
WIDE = (9.0, 3.6)
STD = (8.0, 4.2)
COLORS = 160  # palette size after quantisation

#: path under docs/img -> builder returning a matplotlib Figure
FIGURES: dict = {}
#: thumbnail path -> (source figure path, width in px)
THUMBS: dict = {}
#: screenshot path -> what it shows (made by --screenshots only)
SCREENSHOTS = {
    "shots/rcx-report.png": "RCx report",
    "shots/site-report.png": "site report",
    "shots/lab.png": "camber lab catalog page",
    "shots/trend-viewer.png": "camber serve /ui trend viewer",
    # 0.102 (#121): the docs/LAB.md walkthrough, on the open ornl-frp-ops dataset (a local copy)
    "shots/lab-select.png": "camber lab: a dataset ticked, its sizes against the free disk",
    "shots/lab-job.png": "camber lab: a finished Fetch & ingest job and the dataset's links",
    "shots/lab-ack.png": "camber lab: the research-only acknowledgement dialog",
    "shots/lab-trends.png": "camber lab: the trend viewer on the ingested dataset",
    "shots/lab-report.png": "camber lab: the dataset's on-demand report",
}

#: the open (CC-BY-4.0) dataset the LAB.md walkthrough shots use, and where its file is read
#: from: ``CAMBER_DOCS_FROM_DIR`` (a directory holding its default subset's four CSV files), else
#: a source checkout's ``examples/_data/ornl_frp_ops``. Nothing is downloaded; without the files
#: the walkthrough shots are skipped and the committed PNGs stay as they are.
LAB_DATASET = "ornl-frp-ops"
LAB_FROM_DIR = os.environ.get(
    "CAMBER_DOCS_FROM_DIR", os.path.join(ROOT, "examples", "_data", "ornl_frp_ops")
)


def figure(path: str):
    def deco(fn):
        FIGURES[path] = fn
        return fn

    return deco


# --------------------------------------------------------------------------- rendering


def _style():
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    plt.style.use("default")
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 9,
            "axes.titlesize": 10,
            "axes.labelsize": 9,
            "legend.fontsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "figure.dpi": DPI,
            "savefig.dpi": DPI,
            "axes.grid": False,
            "svg.hashsalt": "camber",
            "path.simplify": True,
        }
    )
    import warnings

    warnings.filterwarnings("ignore")
    return plt


def _encode(img, path: str) -> int:
    """Quantise an RGB image to a palette PNG with no metadata; return its size."""
    from PIL import Image

    pal = img.convert("RGB").quantize(
        colors=COLORS, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE
    )
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pal.save(path, format="PNG", optimize=True)
    return os.path.getsize(path)


def _save(fig, path: str):
    """Render ``fig`` to ``path``; return the RGB image (for thumbnails)."""
    import matplotlib.pyplot as plt
    from PIL import Image

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=DPI, metadata={"Software": None}, facecolor="white")
    plt.close(fig)
    buf.seek(0)
    img = Image.open(buf).convert("RGB")
    _encode(img, path)
    return img


def render(only: str = "") -> list:
    plt = _style()
    done = []
    images = {}
    for rel, build in FIGURES.items():
        if only and not rel.startswith(only) and not any(src == rel for src, _ in THUMBS.values()):
            continue
        import numpy as np

        np.random.seed(0)  # belt and braces: every generator below seeds its own Generator
        fig = build()
        images[rel] = _save(fig, os.path.join(IMG, rel))
        done.append(rel)
        plt.close("all")
    from PIL import Image

    for rel, (src, width) in THUMBS.items():
        if src not in images:
            continue
        img = images[src]
        h = round(img.height * width / img.width)
        _encode(img.resize((width, h), Image.Resampling.LANCZOS), os.path.join(IMG, rel))
        done.append(rel)
    return done


# --------------------------------------------------------------------------- synthetic data


def _rng(seed):
    import numpy as np

    return np.random.default_rng(seed)


def _hourly(days, start="2025-07-07"):
    import pandas as pd

    return pd.date_range(start, periods=24 * days, freq="h")  # a Monday


def _occ(idx, start=7, end=18):
    return (idx.dayofweek < 5) & (idx.hour >= start) & (idx.hour < end)


def _oat(idx, rng, *, center=78.0, amp=14.0, weather=6.0, noise=1.0):
    import numpy as np

    day = (idx - idx[0]).days.to_numpy()
    h = idx.hour.to_numpy()
    return (
        center
        + amp * np.sin((h - 9) / 24 * 2 * np.pi)
        + weather * np.sin(day / 3.3)
        + rng.normal(0, noise, len(idx))
    )


def _building_kw(weeks=8, seed=1, *, stuck_weekend=3):
    """Hourly whole-building kW: weekday schedule, cooling with OAT, one weekend left on."""
    import numpy as np
    import pandas as pd

    idx = _hourly(7 * weeks, start="2025-06-02")
    rng = _rng(seed)
    oat = _oat(idx, rng, center=76, amp=12, weather=7)
    occ = _occ(idx, 6, 19)
    week = ((idx - idx[0]).days // 7).to_numpy()
    occ = occ | ((week == stuck_weekend) & (idx.dayofweek >= 5) & (idx.hour >= 6) & (idx.hour < 19))
    cool = np.clip(oat - 62, 0, None) * np.where(occ, 6.0, 1.5)
    kw = 140 + np.where(occ, 210, 0) + cool + rng.normal(0, 9, len(idx))
    return pd.Series(kw, index=idx, name="building_kw"), pd.Series(oat, index=idx, name="oat")


def _ahu(days=21, seed=0, *, fault=None, start="2025-07-07", center=74.0, amp=14.0):
    """A role-named hourly AHU frame (weekday 06-19 schedule); ``fault`` injects one fault."""
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role

    idx = _hourly(days, start)
    rng = _rng(seed)
    n = len(idx)
    oat = _oat(idx, rng, center=center, amp=amp)
    run = _occ(idx, 6, 19)
    if fault == "fan_24_7":
        week = ((idx - idx[0]).days // 7).to_numpy()
        run = run | (week >= 1)
    rat = 73 + rng.normal(0, 0.4, n)
    minimum = 0.2
    econ = (oat < 65) & (oat > 40)
    oaf = np.where(econ, np.clip((rat - 55) / np.maximum(rat - oat, 1), minimum, 1.0), minimum)
    if fault == "oa_stuck_open":
        oaf = np.where(oat >= 65, 0.75 + rng.normal(0, 0.03, n), oaf)
    if fault == "free_cooling_missed":
        oaf = np.full(n, minimum)
    mat = rat - oaf * (rat - oat)
    sat_sp = np.clip(62 - 0.25 * (oat - 60), 55, 62)
    if fault == "sat_no_reset":
        sat_sp = np.full(n, 55.0)
    need = np.clip((mat - sat_sp) * 9, 0, 100)
    cool = np.where(run, need, 0.0) + rng.normal(0, 2, n)
    cool = np.clip(cool, 0, 100)
    sat = np.where(cool > 1, sat_sp, mat + 1.0) + rng.normal(0, 0.35, n)
    heat = np.zeros(n)
    if fault == "simul_hc":
        hot = run & (idx.hour >= 11) & (idx.hour < 16) & (idx.dayofweek < 5)
        heat = np.where(hot, 30 + rng.normal(0, 4, n), 0.0)
    if fault == "leak_cool_valve":
        mild = run & (cool < 1)
        sat = np.where(mild, mat - 6 + rng.normal(0, 0.4, n), sat)
    sat = np.where(run, sat, rat - 2 + rng.normal(0, 0.3, n))
    static_sp = np.full(n, 1.5)
    if fault != "static_no_reset":
        static_sp = np.clip(0.6 + 0.012 * np.clip(cool, 0, 100), 0.6, 1.5)
    static = np.where(run, static_sp + rng.normal(0, 0.04, n), 0.0)
    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.RETURN_AIR_TEMP: np.where(run, rat, rat - 1),
            Role.MIXED_AIR_TEMP: np.where(run, mat, rat - 1),
            Role.SUPPLY_AIR_TEMP: sat,
            Role.SUPPLY_AIR_TEMP_SP: sat_sp,
            Role.OA_DAMPER: np.where(run, 100 * oaf, 0.0),
            Role.COOL_VALVE: cool,
            Role.HEAT_VALVE: np.clip(heat, 0, 100),
            Role.DUCT_STATIC: static,
            Role.DUCT_STATIC_SP: np.where(run, static_sp, 0.0),
            Role.SUPPLY_FAN_STATUS: run.astype(float),
        },
        index=idx,
    )


def _vav(days=21, seed=0, *, fault=None, start="2025-07-07", center=78.0):
    """A role-named hourly VAV-with-reheat zone frame; ``fault`` injects one fault."""
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role

    idx = _hourly(days, start)
    rng = _rng(seed)
    n = len(idx)
    oat = _oat(idx, rng, center=center, amp=12)
    occ = _occ(idx, 7, 18)
    load = np.where(occ, np.clip(np.sin((idx.hour - 7) / 11.0 * np.pi), 0, None), 0.0)
    cool_sp = np.where(occ, 74.0, 80.0)
    heat_sp = np.where(occ, 70.0, 62.0)
    flow_min, flow_max = 300.0, 1200.0
    flow_sp = np.where(occ, flow_min + (flow_max - flow_min) * load, 0.0)
    flow = flow_sp + rng.normal(0, 25, n)
    space = np.where(occ, 72.0 + 0.8 * load, 75.0) + rng.normal(0, 0.25, n)
    heat = np.zeros(n)
    dat = np.full(n, 55.0) + rng.normal(0, 0.3, n)
    if fault == "overcooling":
        flow_sp = np.where(occ, 900 + 300 * load, 0.0)
        flow = flow_sp + rng.normal(0, 25, n)
        space = np.where(occ, 68.0 - 1.2 * (1 - load), 75.0) + rng.normal(0, 0.3, n)
    if fault == "reheat_warm":
        heat = np.where(occ, 35.0 + 15 * (1 - load), 0.0) + rng.normal(0, 3, n)
        heat = np.clip(heat, 0, 100)
        dat = np.where(occ, 55.0 + 0.25 * heat, 55.0) + rng.normal(0, 0.3, n)
    if fault == "reheat_saturated":
        heat = np.where(occ, 100.0, 0.0)
        space = np.where(occ, 67.0 + rng.normal(0, 0.4, n), 63.0)
        dat = np.where(occ, 80.0, 55.0) + rng.normal(0, 0.3, n)
    damper = np.clip(flow / flow_max * 100, 0, 100)
    if fault == "damper_stuck":
        damper = np.full(n, 30.0)
        flow = np.full(n, 0.3 * flow_max) + rng.normal(0, 20, n)
        space = np.where(occ, 72.0 + 5.0 * load, 75.0) + rng.normal(0, 0.25, n)
    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.SPACE_TEMP: space,
            Role.COOL_SP: cool_sp,
            Role.HEAT_SP: heat_sp,
            Role.AIRFLOW: np.clip(flow, 0, None),
            Role.AIRFLOW_SP: flow_sp,
            Role.DAMPER: damper,
            Role.HEAT_VALVE: heat,
            Role.SUPPLY_AIR_TEMP: dat,
            Role.OCCUPANCY: occ.astype(float),
        },
        index=idx,
    )


def _chiller(days=21, seed=0, *, kw_per_ton_extra=0.0, start="2025-07-07"):
    """A role-named hourly chiller frame with part-load efficiency; extra kW/ton degrades it."""
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role

    idx = _hourly(days, start)
    rng = _rng(seed)
    n = len(idx)
    oat = _oat(idx, rng, center=82, amp=12)
    run = _occ(idx, 6, 20)
    tons = np.where(run, np.clip(60 + 8 * (oat - 70) + rng.normal(0, 15, n), 40, 320), 0.0)
    plr = tons / 350.0
    kwpt = 0.52 + 0.35 * (1 - plr) ** 2 + kw_per_ton_extra + rng.normal(0, 0.03, n)
    flow = np.where(run, 600.0, 0.0)
    dt = np.where(run, tons * 24 / np.maximum(flow, 1), 0.0)
    chws = np.where(run, 44 + rng.normal(0, 0.2, n), 62.0)
    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.POWER: np.where(run, tons * kwpt, 0.0),
            Role.CHW_SUPPLY_TEMP: chws,
            Role.CHW_RETURN_TEMP: chws + dt,
            Role.CHW_FLOW: flow,
            Role.COMPRESSOR_STATUS: run.astype(float),
        },
        index=idx,
    )


def _daily_energy(year, seed, *, savings=0.0, savings_from=None, kind="cool"):
    """Daily site energy and mean OAT for one year (change-point shaped)."""
    import numpy as np
    import pandas as pd

    idx = pd.date_range(f"{year}-01-01", f"{year}-12-31", freq="D")
    rng = _rng(seed)
    doy = idx.dayofyear.to_numpy()
    t = 58 + 22 * np.sin(2 * np.pi * (doy - 110) / 365) + rng.normal(0, 4, len(idx))
    if kind == "cool":
        e = 2400 + 55 * np.clip(t - 62, 0, None) + 18 * np.clip(48 - t, 0, None)
    else:  # heating gas, therms/day
        e = 40 + 9.0 * np.clip(60 - t, 0, None)
    e = e * (1 + rng.normal(0, 0.05, len(idx)))
    if savings and savings_from is not None:
        e = np.where(idx >= pd.Timestamp(savings_from), e * (1 - savings), e)
    return pd.Series(t, index=idx, name="oat"), pd.Series(e, index=idx, name="energy")


def _quality_frame():
    """BAS-named trends with the usual problems: a stuck point, spikes, a gap, duty cycling."""
    import numpy as np
    import pandas as pd

    idx = pd.date_range("2025-07-07", periods=24 * 4 * 21, freq="15min")
    rng = _rng(4)
    n = len(idx)
    h = idx.hour.to_numpy() + idx.minute.to_numpy() / 60
    oat = 78 + 13 * np.sin((h - 9) / 24 * 2 * np.pi) + rng.normal(0, 0.8, n)
    on = (idx.dayofweek < 5) & (h >= 6) & (h < 19)
    sat = np.where(on, 55 + rng.normal(0, 0.4, n), 70 + rng.normal(0, 0.6, n))
    rat = 73 + rng.normal(0, 0.4, n)
    rat[24 * 4 * 6 : 24 * 4 * 13] = 72.4  # held for a week
    co2 = np.where(on, 650 + 250 * np.clip(np.sin((h - 6) / 13 * np.pi), 0, None), 430)
    co2 = co2 + rng.normal(0, 15, n)
    spikes = rng.choice(n, 60, replace=False)
    co2[spikes] = rng.choice([0.0, 5000.0], 60)
    oat_s = pd.Series(oat, index=idx)
    oat_s.iloc[24 * 4 * 9 : 24 * 4 * 12] = np.nan  # three days missing
    pump = np.where((np.arange(n) // 8) % 3 == 0, 0.0, 11.5 + rng.normal(0, 0.3, n))
    zone = pd.Series(72 + rng.normal(0, 0.5, n), index=idx)
    zone.iloc[: 24 * 4 * 5] = np.nan  # trended from day 6
    return pd.DataFrame(
        {
            "AHU1_SAT": sat,
            "AHU1_RAT": rat,
            "Bldg_OAT": oat_s,
            "Zone3_CO2": co2,
            "CHWP1_kW": pump,
            "Zone7_Temp": zone,
        },
        index=idx,
    )


def _evidence_fig(rule_name, equip, frame, *, figsize=STD, params=None, title=None):
    """Run a built-in rule on ``frame`` and render the evidence chart it declares."""
    import matplotlib.pyplot as plt

    from camber.charts.evidence import finding_evidence, render_evidence
    from camber.rules.builtin import builtin_registry

    rule = builtin_registry().get(rule_name)
    for k, v in (params or {}).items():
        setattr(rule, k, v)
    ev = finding_evidence(rule, equip, frame)
    if ev is None:
        raise RuntimeError(f"{rule_name}: no evidence")
    fig, ax = plt.subplots(figsize=figsize)
    if title:
        ev.title = title
    render_evidence(ev, frame, ax=ax)
    fig.tight_layout()
    return fig


def _drift_fig(family, sim, fault, severity, rule_name, equip, *, seed=5):
    """Freeze a drift family's baselines on a healthy period and draw the faulted period on one."""
    import matplotlib.pyplot as plt

    from camber.charts.evidence import drift_evidence, render_evidence
    from camber.driftrun import build_drift_suite
    from camber.store.modelstore import BaselineStore

    case = sim.simulate_case(fault, severity, seed=seed, equip=equip)
    store = BaselineStore()
    suite = build_drift_suite(family, store, site="Demo", run_id="docs", freeze_if_missing=True)
    rule = next(r for r in suite if r.name == rule_name)
    rule.analyze_periods(equip, case.baseline, case.current)
    ev = drift_evidence(rule, equip, case.current)
    if ev is None:
        raise RuntimeError(f"{rule_name}: no drift evidence")
    fig, ax = plt.subplots(figsize=STD)
    render_evidence(ev, case.current, ax=ax)
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------------- VISUALIZATION.md


@figure("viz/readiness.png")
def _viz_readiness():
    import matplotlib.pyplot as plt

    from camber.charts.readiness import readiness_ribbon

    fig, ax = plt.subplots(figsize=(9.0, 3.2))
    readiness_ribbon(_quality_frame(), ax=ax, max_bins=240)
    fig.tight_layout()
    return fig


@figure("viz/multitrend.png")
def _viz_multitrend():
    import matplotlib.pyplot as plt

    from camber.charts.multitrend import fault_multitrend
    from camber.synth import make_ahu_trends

    df = make_ahu_trends(days=7, fault="reheat", seed=3)
    spans = {"simultaneous heat/cool": (df["AHU1_HeC"] > 5) & (df["AHU1_CC"] > 5)}
    fig, ax = plt.subplots(figsize=WIDE)
    fault_multitrend(df, ["AHU1_CC", "AHU1_HeC", "Bldg_TempOa"], spans=spans, ax=ax, normalize=True)
    fig.tight_layout()
    return fig


@figure("viz/carpet.png")
def _viz_carpet():
    import matplotlib.pyplot as plt

    from camber.charts.carpet import load_carpet

    kw, _ = _building_kw()
    fig, ax = plt.subplots(figsize=WIDE)
    load_carpet(kw, ax=ax, title="Building load: hour of day x date (one weekend left on)")
    fig.tight_layout()
    return fig


@figure("viz/quality.png")
def _viz_quality():
    import matplotlib.pyplot as plt

    from camber.charts.quality_dashboard import quality_dashboard

    fig, ax = plt.subplots(figsize=(8.0, 3.6))
    quality_dashboard(
        _quality_frame(),
        ax=ax,
        metrics=("coverage", "score", "flatline_frac", "outlier_frac", "regime_outlier_frac"),
    )
    fig.tight_layout()
    return fig


@figure("viz/oat-scatter.png")
def _viz_oat_scatter():
    import matplotlib.pyplot as plt

    from camber.charts.oat_scatter import oat_scatter

    kw, oat = _building_kw(weeks=6, seed=2, stuck_weekend=-1)
    occ = _occ(kw.index, 6, 19)
    fig, ax = plt.subplots(figsize=STD)
    oat_scatter(kw[occ], oat[occ], ax=ax, ylabel="Occupied load (kW)")
    fig.tight_layout()
    return fig


@figure("viz/diagnostic.png")
def _viz_diagnostic():
    import matplotlib.pyplot as plt

    from camber.charts.diagnostic import TEMPLATES, diagnostic_scatter
    from camber.model.roles import Role

    frame = _ahu(days=28, seed=6, fault="sat_no_reset", center=56, amp=16)
    frame = frame[frame[Role.SUPPLY_FAN_STATUS] > 0.5]  # fan on
    fig, ax = plt.subplots(figsize=STD)
    diagnostic_scatter(frame, TEMPLATES["sat_reset"], ax=ax)
    fig.tight_layout()
    return fig


@figure("viz/evidence.png")
def _viz_evidence():
    return _evidence_fig("simultaneous_heat_cool", "AHU-1", _ahu(seed=7, fault="simul_hc"))


@figure("viz/cohort.png")
def _viz_cohort():
    import pandas as pd

    from camber.charts.cohort import cohort_small_multiples
    from camber.model.roles import Role

    frames = {}
    for i in range(1, 9):
        f = _vav(days=7, seed=20 + i)
        if i == 6:
            f[Role.AIRFLOW] = f[Role.AIRFLOW] * 0.35  # starved box
        frames[f"VAV-{i}"] = pd.DataFrame({Role.AIRFLOW: f[Role.AIRFLOW]})
    fig, _ = cohort_small_multiples(frames, Role.AIRFLOW, ncols=4, figsize=(9.0, 4.0))
    fig.tight_layout()
    return fig


def _savings_inputs():
    from camber.mandv.models import N_PARAMS, best_model
    from camber.mandv.stats import fit_stats

    t_b, e_b = _daily_energy(2024, 11)
    t_r, e_r = _daily_energy(2025, 12, savings=0.10, savings_from="2025-01-01")
    model = best_model(t_b.to_numpy(), e_b.to_numpy())
    st = fit_stats(e_b.to_numpy(), model.predict(t_b.to_numpy()), N_PARAMS[model.kind])
    return model, st, t_b, e_b, t_r, e_r


@figure("viz/savings.png")
def _viz_savings():
    import matplotlib.pyplot as plt

    from camber.charts.savings import savings_chart

    model, st, _tb, _eb, t_r, e_r = _savings_inputs()
    fig, ax = plt.subplots(figsize=STD)
    savings_chart(
        model,
        t_r.to_numpy(),
        e_r,
        n_baseline=st.n,
        p_baseline=st.p,
        cv_rmse=st.cv_rmse,
        ax=ax,
        ylabel="kWh",
    )
    fig.tight_layout()
    return fig


@figure("viz/load-profile.png")
def _viz_load_profile():
    import matplotlib.pyplot as plt

    from camber.charts.loadprofile_chart import load_duration_chart, load_profile_chart

    kw, _ = _building_kw(seed=3, stuck_weekend=-1)
    fig, (a1, a2) = plt.subplots(1, 2, figsize=WIDE)
    load_profile_chart(kw, ax=a1, split=True)
    load_duration_chart(kw, ax=a2, price=0.15)
    fig.tight_layout()
    return fig


@figure("viz/box-by-hour.png")
def _viz_box_by_hour():
    import matplotlib.pyplot as plt

    from camber.charts.boxhour import box_by_hour
    from camber.model.roles import Role

    frame = _ahu(seed=8, fault="static_no_reset")
    fan = frame[Role.SUPPLY_FAN_STATUS] > 0.5
    fig, ax = plt.subplots(figsize=WIDE)
    box_by_hour(
        frame[Role.DUCT_STATIC],
        mask=fan,
        ax=ax,
        ylabel="Duct static (in. w.c.)",
        title="Duct static by hour, fan on: never reset down",
    )
    fig.tight_layout()
    return fig


@figure("viz/cusum.png")
def _viz_cusum():
    import matplotlib.pyplot as plt
    import pandas as pd

    from camber.charts.cusum_chart import cusum_plot
    from camber.mandv.models import best_model

    t_b, e_b = _daily_energy(2024, 11)
    t_r, e_r = _daily_energy(2025, 13, savings=0.08, savings_from="2025-05-01")
    model = best_model(t_b.to_numpy(), e_b.to_numpy())
    projected = pd.Series(model.predict(t_r.to_numpy()), index=t_r.index)
    fig, ax = plt.subplots(figsize=WIDE)
    cusum_plot(projected, e_r, ax=ax, title="CUSUM: savings begin after the May retrofit")
    fig.tight_layout()
    return fig


@figure("viz/energy-signature.png")
def _viz_energy_signature():
    import matplotlib.pyplot as plt

    from camber.charts.energy_signature import energy_signature

    t, e = _daily_energy(2024, 11)
    fig, ax = plt.subplots(figsize=STD)
    energy_signature(t.to_numpy(), e.to_numpy(), ax=ax, ylabel="Daily energy (kWh)")
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------------- analytics pages


@figure("analytics/free-cooling.png")
def _an_free_cooling():
    # the rule judges fan-on hours only (#120): no pre-filter here
    frame = _ahu(days=21, seed=9, fault="free_cooling_missed", center=58, amp=14)
    return _evidence_fig("free_cooling_missed", "AHU-1", frame)


@figure("analytics/chiller-drift.png")
def _an_chiller_drift():
    from camber import driftsim

    return _drift_fig("chiller", driftsim, "condenser_fouling", 3, "chiller_approach_drift", "CH-1")


@figure("analytics/ahu-drift.png")
def _an_ahu_drift():
    from camber import ahusim

    return _drift_fig("ahu", ahusim, "filter_loading", 3, "filter_loading_drift", "AHU-1")


@figure("analytics/vav-drift.png")
def _an_vav_drift():
    from camber import vavsim

    return _drift_fig("vav", vavsim, "damper_authority_loss", 3, "vav_airflow_drift", "VAV-1")


@figure("analytics/pump-drift.png")
def _an_pump_drift():
    from camber import pumpsim

    return _drift_fig("pump", pumpsim, "impeller_wear", 3, "pump_head_drift", "CHWP-1")


@figure("analytics/sensor-health.png")
def _an_sensor_health():
    import matplotlib.pyplot as plt
    import pandas as pd

    from camber.charts.multitrend import fault_multitrend
    from camber.model.roles import Role
    from camber.sensorhealth import sensor_trust

    frame = _ahu(seed=10)
    ref = frame[Role.OAT]
    bas = ref + 0.4 + _rng(26).normal(0, 0.3, len(ref))  # the BAS sensor, close to the reference
    bas.iloc[24 * 8 : 24 * 11 + 9] = 71.6  # ... until it holds one value for three days
    tr = sensor_trust(bas, Role.OAT)
    stuck = pd.Series(False, index=bas.index)
    for iv in tr.stuck_intervals:
        stuck |= (bas.index >= pd.Timestamp(iv["start"])) & (bas.index <= pd.Timestamp(iv["end"]))
    df = pd.DataFrame({"BAS outdoor air temp": bas, "weather-station reference": ref})
    fig, ax = plt.subplots(figsize=WIDE)
    fault_multitrend(
        df,
        spans={f"stuck: {tr.verdict}, trust {tr.trust:.2f}": stuck},
        ax=ax,
        normalize=False,
        title="Outdoor-air temperature sensor held at one value",
    )
    ax.set_ylabel("°F")
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------------- workbook


@figure("workbook/air-economizer.png")
def _wb_air_economizer():
    return _evidence_fig("outdoor_air_fraction", "AHU-1", _ahu(seed=11, fault="oa_stuck_open"))


@figure("workbook/air-heat-cool.png")
def _wb_air_heat_cool():
    frame = _ahu(days=7, seed=12, fault="leak_cool_valve", center=62, amp=12)
    return _evidence_fig("leaking_valve", "AHU-1", frame, figsize=WIDE)


@figure("workbook/air-sat-reset.png")
def _wb_air_sat_reset():
    frame = _ahu(seed=13, fault="sat_no_reset", center=68, amp=16)
    return _evidence_fig("supply_air_reset", "AHU-1", frame)


@figure("workbook/air-scheduling.png")
def _wb_air_scheduling():
    return _evidence_fig(
        "night_weekend_setback", "AHU-1", _ahu(days=28, seed=15, fault="fan_24_7"), figsize=WIDE
    )


@figure("workbook/zone-reheat-overcooling.png")
def _wb_zone_reheat():
    return _evidence_fig("reheat_penalty", "VAV-2", _vav(seed=16, fault="reheat_warm"))


@figure("workbook/zone-bad-box.png")
def _wb_zone_bad_box():
    frame = _vav(days=7, seed=17, fault="damper_stuck")
    return _evidence_fig("unmet_setpoint_hours", "VAV-4", frame, figsize=WIDE)


@figure("workbook/zone-reheat-saturated.png")
def _wb_zone_saturated():
    frame = _vav(days=14, seed=18, fault="reheat_saturated", start="2025-01-06", center=35)
    return _evidence_fig("reheat_capacity_shortfall", "VAV-5", frame, figsize=WIDE)


@figure("workbook/zone-dcv.png")
def _wb_zone_dcv():
    from camber.faultlab import dcv_sim

    frame = dcv_sim(_hourly(14), control="static")
    return _evidence_fig("dcv_verification", "Room-1", frame, figsize=WIDE)


@figure("workbook/zone-min-oa.png")
def _wb_zone_min_oa():
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role

    idx = _hourly(14)
    rng = _rng(19)
    occ = _occ(idx, 8, 17)
    rise = np.clip(np.sin((idx.hour - 8) / 9 * np.pi), 0, None)
    co2 = np.where(occ, 700 + 650 * rise, 450) + rng.normal(0, 20, len(idx))
    frame = pd.DataFrame({Role.CO2: co2, Role.OCCUPANCY: occ.astype(float)}, index=idx)
    return _evidence_fig("co2_ventilation", "Zone-12", frame, figsize=WIDE)


@figure("workbook/plant-chiller-efficiency.png")
def _wb_chiller_eff():
    import matplotlib.pyplot as plt
    import pandas as pd

    from camber.charts.diagnostic import band, diagnostic_scatter
    from camber.model.roles import Role

    frame = _chiller(seed=20, kw_per_ton_extra=0.4)
    on = frame[Role.COMPRESSOR_STATUS] > 0.5
    f = frame[on]
    tons = f[Role.CHW_FLOW] * (f[Role.CHW_RETURN_TEMP] - f[Role.CHW_SUPPLY_TEMP]) / 24.0
    derived = pd.DataFrame({"tons": tons, "kw_per_ton": f[Role.POWER] / tons})
    design = 0.85
    tmpl = band(
        "tons",
        "kw_per_ton",
        low=0.3,
        high=design * 1.2,
        name="chiller kW/ton vs the warn ceiling (1.2 x 0.85 design)",
        xlabel="Cooling load (tons)",
        ylabel="kW/ton",
    )
    fig, ax = plt.subplots(figsize=STD)
    diagnostic_scatter(derived, tmpl, ax=ax)
    fig.tight_layout()
    return fig


@figure("workbook/plant-cooling-tower.png")
def _wb_tower():
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role

    idx = _hourly(21)
    rng = _rng(21)
    n = len(idx)
    wb = 66 + 6 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 0.8, n)
    week = ((idx - idx[0]).days // 7).to_numpy()
    approach = 6.5 + 0.15 * (wb - 66) + np.where(week >= 1, 4.0 * week / 2, 0.0)
    approach = approach + rng.normal(0, 0.5, n)
    frame = pd.DataFrame({Role.WETBULB_TEMP: wb, Role.CW_SUPPLY_TEMP: wb + approach}, index=idx)
    return _evidence_fig("cooling_tower_approach", "CT-1", frame)


@figure("workbook/plant-chw-reset-pumping.png")
def _wb_chw_reset():
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role

    idx = _hourly(21)
    rng = _rng(22)
    n = len(idx)
    oat = _oat(idx, rng, center=68, amp=16)
    chws = 44 + rng.normal(0, 0.3, n)  # held at 44 F whatever the weather
    chwr = chws + 10 + rng.normal(0, 0.5, n)
    frame = pd.DataFrame(
        {
            Role.OAT: oat,
            Role.CHW_SUPPLY_TEMP: chws,
            Role.CHW_RETURN_TEMP: chwr,
            Role.COMPRESSOR_STATUS: np.ones(n),
        },
        index=idx,
    )
    return _evidence_fig("chw_plant_reset", "CHW-1", frame, figsize=WIDE)


@figure("workbook/plant-boiler.png")
def _wb_boiler():
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role

    idx = pd.date_range("2025-01-06", periods=4 * 24 * 2, freq="15min")
    rng = _rng(23)
    n = len(idx)
    oat = 40 + 9 * np.sin((idx.hour - 9) / 24 * 2 * np.pi) + rng.normal(0, 1, n)
    cycling = (np.arange(n) % 3 == 0).astype(float)  # 15 min on, 30 min off
    status = np.where(oat < 36, 1.0, cycling)  # fires steadily only on the coldest hours
    frame = pd.DataFrame({Role.BOILER_STATUS: status, Role.OAT: oat}, index=idx)
    return _evidence_fig("boiler_short_cycle", "BLR-1", frame, figsize=WIDE)


@figure("workbook/plant-sensor-vs-equipment.png")
def _wb_sensor_vs_equipment():
    import numpy as np
    import pandas as pd

    from camber.model.roles import Role

    idx = _hourly(14)
    rng = _rng(24)
    n = len(idx)
    load = 0.5 + 0.5 * np.clip(np.sin((idx.hour - 6) / 24 * 2 * np.pi), 0, None)
    tower = 78 + 4 * load + rng.normal(0, 0.3, n)
    ret = tower + 10 * load + rng.normal(0, 0.3, n)
    entering = 0.6 * tower + 0.4 * ret + rng.normal(0, 0.3, n)
    frame = pd.DataFrame(
        {
            Role.CW_BYPASS_VALVE: np.zeros(n),
            Role.CW_SUPPLY_TEMP: tower,
            Role.CW_RETURN_TEMP: ret,
            Role.COND_ENTERING_WATER_TEMP: entering,
            Role.COMPRESSOR_STATUS: np.ones(n),
        },
        index=idx,
    )
    return _evidence_fig("condenser_bypass_leak", "CH-1", frame, figsize=WIDE)


@figure("workbook/data-trend-quality.png")
def _wb_trend_quality():
    import matplotlib.pyplot as plt

    from camber.charts.quality_dashboard import quality_dashboard

    fig, ax = plt.subplots(figsize=(7.0, 3.4))
    quality_dashboard(_quality_frame(), ax=ax)
    fig.tight_layout()
    return fig


@figure("workbook/data-energy-charting.png")
def _wb_energy_charting():
    import matplotlib.pyplot as plt

    from camber.charts.carpet import load_carpet

    kw, _ = _building_kw(weeks=6, seed=5, stuck_weekend=2)
    fig, ax = plt.subplots(figsize=WIDE)
    load_carpet(kw, ax=ax, title="Whole-building load, hour of day x date")
    fig.tight_layout()
    return fig


@figure("workbook/mv-baselines.png")
def _wb_mv():
    import matplotlib.pyplot as plt

    from camber.charts.energy_signature import energy_signature

    t, e = _daily_energy(2024, 25, kind="heat")
    fig, ax = plt.subplots(figsize=STD)
    energy_signature(
        t.to_numpy(),
        e.to_numpy(),
        ax=ax,
        ylabel="Daily gas (therms)",
        title="Heating baseline: a three-parameter change-point fit",
    )
    fig.tight_layout()
    return fig


# --------------------------------------------------------------------------- README thumbnails

THUMBS.update(
    {
        "thumbs/multitrend.png": ("viz/multitrend.png", 420),
        "thumbs/chiller-drift.png": ("analytics/chiller-drift.png", 420),
        "thumbs/savings.png": ("viz/savings.png", 420),
    }
)


# --------------------------------------------------------------------------- screenshots

CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


def _free_port() -> int:
    import socket

    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _chrome_shot(url: str, out: str, *, size=(1280, 1100), wait_ms=4000) -> None:
    import subprocess
    import tempfile
    import time

    from PIL import Image

    with tempfile.TemporaryDirectory() as prof:
        raw = os.path.join(prof, "shot.png")
        log = os.path.join(prof, "chrome.log")
        with open(log, "wb") as fh:
            proc = subprocess.Popen(
                [
                    CHROME,
                    "--headless",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--hide-scrollbars",
                    "--force-device-scale-factor=1",
                    f"--user-data-dir={prof}",
                    f"--window-size={size[0]},{size[1]}",
                    f"--virtual-time-budget={wait_ms}",
                    f"--screenshot={raw}",
                    url,
                ],
                stdout=fh,
                stderr=subprocess.STDOUT,
            )
            # Chrome on macOS writes the screenshot and then may not exit: wait for its
            # "bytes written" line, then stop it ourselves
            try:
                for _ in range(600):
                    with open(log, "rb") as lf:
                        if b"bytes written to file" in lf.read():
                            break
                    if proc.poll() is not None:
                        break
                    time.sleep(0.2)
                else:
                    raise RuntimeError(f"Chrome wrote no screenshot of {url}")
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
        img = Image.open(raw).convert("RGB")
    width = 1000
    img = img.resize((width, round(img.height * width / img.width)), Image.Resampling.LANCZOS)
    size_b = _encode(img, out)
    if size_b > MAX_BYTES:  # a busy page: trim the palette until it fits
        for colors in (96, 64, 48):
            pal = img.quantize(colors=colors, method=Image.Quantize.MEDIANCUT)
            pal.save(out, format="PNG", optimize=True)
            if os.path.getsize(out) <= MAX_BYTES:
                break


def _shot_site(tmp: str):
    """A neutral two-AHU synthetic site in a store under ``tmp``; returns the config path."""
    import json

    from camber.store import ParquetStore

    st = ParquetStore(os.path.join(tmp, "store"))
    a = _ahu(days=21, seed=30, fault="oa_stuck_open", start="2026-03-02", center=70, amp=16)
    b = _ahu(days=21, seed=31, fault="simul_hc", start="2026-03-02", center=70, amp=16)
    st.write_role_frame(a, facility_id="demo-site", equip="AHU-1", equip_class="AHU")
    st.write_role_frame(b, facility_id="demo-site", equip="AHU-2", equip_class="AHU")
    st.register_facility("demo-site", name="Demo site (synthetic)")
    cfg = {
        "site": "Demo site (synthetic)",
        "source": {"kind": "store", "store": "store", "facility_id": "demo-site"},
        "equipment": [{"class": "AHU"}],
        "rules": [
            "outdoor_air_fraction",
            "economizer_high_limit",
            "supply_air_reset",
            "simultaneous_heat_cool",
            "leaking_valve",
            "static_pressure_reset",
        ],
        "report": {"layout": "rcx"},
    }
    path = os.path.join(tmp, "site.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh)
    return path, {"AHU-1": a, "AHU-2": b}


def screenshots() -> list:
    import subprocess
    import tempfile
    import time
    import urllib.request

    if not os.path.exists(CHROME):
        raise SystemExit(f"no Chrome at {CHROME}; set CHROME")
    _style()
    done = []
    out = {k: os.path.join(IMG, k) for k in SCREENSHOTS}
    with tempfile.TemporaryDirectory(prefix="camber-shots-") as tmp:
        cfg, frames = _shot_site(tmp)
        here = os.getcwd()
        os.chdir(tmp)
        try:
            from camber.cli import main as cli

            cli(["report", "site.json", "--out", "rcx.html", "--layout", "rcx"])
            import pandas as pd

            from camber.report import build_site_report
            from camber.rules.builtin import builtin_registry

            reg = builtin_registry()
            findings = []
            for equip, fr in frames.items():
                for name in ("outdoor_air_fraction", "simultaneous_heat_cool", "supply_air_reset"):
                    findings.append(reg.get(name).analyze(equip, fr))
            kw, oat = _building_kw(weeks=3, seed=32, stuck_weekend=-1)
            site_df = pd.DataFrame({"building_kw": kw.to_numpy(), "oat": oat.to_numpy()})
            site_df.index = frames["AHU-1"].index  # the site's own three weeks
            html = build_site_report(
                site_df,
                findings=findings,
                rules=reg,
                frames=frames,
                title="Demo site (synthetic): site report",
            )
            with open("site.html", "w", encoding="utf-8") as fh:
                fh.write(html)
        finally:
            os.chdir(here)
        _chrome_shot("file://" + os.path.join(tmp, "rcx.html"), out["shots/rcx-report.png"])
        _chrome_shot("file://" + os.path.join(tmp, "site.html"), out["shots/site-report.png"])
        done += ["shots/rcx-report.png", "shots/site-report.png"]

        py = sys.executable
        env = dict(os.environ, PYTHONPATH=ROOT)
        boot = "import sys; from camber.cli import main; sys.exit(main(sys.argv[1:]))"
        servers = []
        try:
            p_api, p_lab = _free_port(), _free_port()
            servers.append(
                subprocess.Popen(
                    [py, "-c", boot, "serve", "store", "--port", str(p_api)],
                    cwd=tmp,
                    env=env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
            )
            lab_dir = os.path.join(tmp, "demo")  # the lab prints the store's last two path parts
            os.makedirs(os.path.join(lab_dir, "cache"), exist_ok=True)
            # every lab route needs the access token (#127): read the launch URL the lab prints
            # (its launch file goes to a folder inside the temporary directory)
            lab_env = dict(
                env,
                XDG_CONFIG_HOME=os.path.join(tmp, "cfg"),
                XDG_RUNTIME_DIR=os.path.join(tmp, "run"),
            )
            lab = subprocess.Popen(
                [
                    py,
                    "-c",
                    boot,
                    "lab",
                    "--store",
                    "lab_store",
                    "--dir",
                    "cache",
                    "--port",
                    str(p_lab),
                ],  # fmt: skip
                cwd=lab_dir,
                env=lab_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            servers.append(lab)
            lab_url = next((ln.strip() for ln in lab.stdout if "?token=" in ln), None)
            if lab_url is None:
                raise RuntimeError("camber lab printed no launch URL")
            for _ in range(100):
                try:
                    urllib.request.urlopen(f"http://127.0.0.1:{p_api}/ui", timeout=2)
                    break
                except OSError:
                    time.sleep(0.2)
            _chrome_shot(
                f"http://127.0.0.1:{p_api}/ui?facility_id=demo-site",
                out["shots/trend-viewer.png"],
                size=(1280, 900),
                wait_ms=6000,
            )
            _chrome_shot(lab_url, out["shots/lab.png"], size=(1280, 1000))
            done += ["shots/trend-viewer.png", "shots/lab.png"]
        finally:
            for s in servers:
                s.terminate()
                try:
                    s.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    s.kill()
        done += _lab_walkthrough(tmp, out)
    return done


# The LAB.md walkthrough. Each driver is a small script appended to the page for one screenshot:
# it does what a learner does by hand (search, tick, press, brush), once the page has loaded.
_WHEN = (
    "function when(test,fn,n){n=n||0;if(test()){fn();return;}if(n>300)return;"
    "setTimeout(function(){when(test,fn,n+1);},50);}"
    "function rows(){return document.querySelectorAll('#rows tr').length>0;}"
    "function fire(el,type){el.dispatchEvent(new Event(type,{bubbles:true}));}"
)
_DRIVE_SELECT = (
    "when(rows,function(){var q=document.getElementById('q');q.value='ornl';fire(q,'input');"
    "var cb=document.querySelector('input[aria-label=\"select %(id)s\"]');"
    "cb.checked=true;fire(cb,'change');});"
)
_DRIVE_JOB = (
    "when(function(){return rows()&&document.querySelector('#jobs .job');},function(){"
    "var q=document.getElementById('q');q.value='%(id)s';fire(q,'input');});"
)
_DRIVE_ACK = (
    "when(rows,function(){var l=document.getElementById('lic');l.value='research';fire(l,'change');"
    "var cb=document.querySelector('input[aria-label=\"select at-30bldg-sensors\"]');"
    "cb.checked=true;fire(cb,'change');document.getElementById('go').click();"
    "when(function(){return document.getElementById('ack').open;},function(){"
    "var b=document.getElementById('ack-check');b.checked=true;fire(b,'change');"
    "var t=document.getElementById('ack-typed');t.value='at-30bldg-sensors';fire(t,'input');});});"
)
_DRIVE_TRENDS = (
    "var WANT=['supply_air_temp','return_air_temp','supply_fan_status'];"
    "var eq=document.getElementById('equip');"
    "function upd(){return document.getElementById('updated').textContent;}"
    "when(function(){return document.querySelectorAll('#roles input').length>3&&"
    "/points/.test(upd());},function(){"
    "document.querySelectorAll('#roles input').forEach(function(c){"
    "c.checked=WANT.indexOf(c.value)>=0;});document.getElementById('live').checked=false;"
    "eq.value='RTU__sb_heating';"
    "setTimeout(function(){document.getElementById('refresh').click();"
    "when(function(){return document.querySelectorAll('#legend > span').length===WANT.length;},"
    "function(){setTimeout(function(){var svg=document.getElementById('trend'),"
    "r=svg.getBoundingClientRect(),y=r.top+90,xa=r.left+r.width*0.40,xb=r.left+r.width*0.56;"
    "function m(t,x,el){(el||svg).dispatchEvent(new MouseEvent(t,{clientX:x,clientY:y,"
    "bubbles:true}));}"
    "m('mousedown',xa);m('mousemove',xb);m('mouseup',xb,window);m('mousemove',xb);},300);});"
    "},1100);});"
)


def _lab_walkthrough(tmp: str, out: dict) -> list:
    """The LAB.md walkthrough shots: the lab, in-process, on an open catalog dataset."""
    import json
    import threading
    import time
    import urllib.request

    from camber import datasets

    files = [f["name"] for f in datasets.get(LAB_DATASET).subset_files("default")]
    if not all(os.path.isfile(os.path.join(LAB_FROM_DIR, f)) for f in files):
        print(
            f"skipping the LAB.md walkthrough shots: no local copy of {LAB_DATASET} (set "
            f"CAMBER_DOCS_FROM_DIR to a directory holding {', '.join(files)})"
        )
        return []
    import camber.api.server as api_server
    from camber.datasets._ops import adopt_local_files
    from camber.lab import LabApp, make_lab_server
    from camber.lab import _server as lab_server
    from camber.lab._ui import _sha256_source

    lab_dir = os.path.join(tmp, "walk", "demo")  # the page shows the last two path parts
    cache = os.path.join(lab_dir, "cache")
    os.makedirs(cache, exist_ok=True)
    app = LabApp(store=os.path.join(lab_dir, "lab_store"), data_dir=cache)
    httpd = make_lab_server(app, port=0)
    base = f"http://127.0.0.1:{app.port}"
    page0, csp0, ui0 = lab_server.lab_page_html, lab_server.LAB_CSP, api_server.live_dashboard_html
    drive = {"lab": "", "ui": ""}

    def lab_page(token):
        page = page0(token)
        js = drive["lab"]
        return page.replace("</body>", f"<script>{js}</script></body>") if js else page

    def ui_page():
        page = ui0()
        return page.replace("</body>", f"<script>{drive['ui']}</script></body>")

    def set_lab(js: str) -> None:
        drive["lab"] = (_WHEN + js) if js else ""
        extra = _sha256_source(drive["lab"]) + " " if js else ""
        lab_server.LAB_CSP = csp0.replace("script-src ", "script-src " + extra, 1)

    lab_server.lab_page_html = lab_page
    api_server.live_dashboard_html = ui_page
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    names = [f"shots/lab-{k}.png" for k in ("select", "job", "ack", "trends", "report")]

    # every lab route needs the access token (#127): each headless Chrome (a fresh profile) opens
    # the launch URL, which sets the session cookie and redirects to the page without the token.
    # The token is on Chrome's command line here only because this throwaway lab lives for the
    # few seconds of the shots; `camber lab` itself never puts it in argv.
    def url(path, query=""):
        return app.launch_url(path) + (f"&{query}" if query else "")

    try:
        # 1. the catalog, searched and one dataset ticked (nothing fetched yet)
        set_lab(_DRIVE_SELECT % {"id": LAB_DATASET})
        _chrome_shot(url("/lab"), out[names[0]], size=(1280, 900))
        # 2. Fetch & ingest. The verified local copy stands in for the download: the fetch finds
        #    the pinned file already in the cache and downloads nothing; the ingest runs as usual
        adopt_local_files(datasets.get(LAB_DATASET), LAB_FROM_DIR, data_dir=cache)
        req = urllib.request.Request(
            f"{base}/lab/jobs/fetch",
            data=json.dumps({"ids": [LAB_DATASET], "ingest": True}).encode(),
            headers={
                "Content-Type": "application/json",
                "Origin": base,
                "Authorization": f"Bearer {app.access_token}",
                lab_server.TOKEN_HEADER: app.token,
            },
        )
        job = json.loads(urllib.request.urlopen(req, timeout=10).read())["job"]
        for _ in range(600):
            job = app.job(job["id"])
            if job["state"] in ("done", "failed", "cancelled"):
                break
            time.sleep(0.2)
        if job["state"] != "done":
            raise RuntimeError(f"walkthrough job {job['state']}: {job.get('error')}")
        set_lab(_DRIVE_JOB % {"id": LAB_DATASET})
        _chrome_shot(url("/lab"), out[names[1]], size=(1280, 760))
        # 3. the research-only dialog (the dialog only: nothing is acknowledged or fetched)
        set_lab(_DRIVE_ACK)
        _chrome_shot(url("/lab"), out[names[2]], size=(1280, 760))
        set_lab("")
        # 4. trends: the economizer's temperatures and damper, a span brushed
        drive["ui"] = _WHEN + _DRIVE_TRENDS
        _chrome_shot(
            url("/ui", f"facility_id=ds-{LAB_DATASET}"),
            out[names[3]],
            size=(1280, 820),
            wait_ms=9000,
        )
        # 5. the on-demand report (built here first, so the shot does not wait on it)
        app.report_html(f"ds-{LAB_DATASET}")
        _chrome_shot(url(f"/lab/reports/ds-{LAB_DATASET}"), out[names[4]], size=(1280, 1000))
    finally:
        lab_server.lab_page_html, lab_server.LAB_CSP = page0, csp0
        api_server.live_dashboard_html = ui0
        httpd.shutdown()
        httpd.server_close()
        app.close()
    return names


# --------------------------------------------------------------------------- --check

_MD_IMG = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HTML_IMG = re.compile(r"<img\b[^>]*\bsrc=[\"']([^\"']+)[\"'][^>]*>", re.I)


def _md_files() -> list:
    out = [os.path.join(ROOT, "README.md")]
    for d, _dirs, files in os.walk(DOCS):
        out += [os.path.join(d, f) for f in files if f.endswith(".md")]
    return sorted(out)


def _image_refs(path: str) -> list:
    """``(line_no, alt, target, caption_ok)`` for each local image a Markdown file references."""
    with open(path, encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    refs, fence = [], False
    for i, ln in enumerate(lines):
        if ln.lstrip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        found = [(m.group(1), m.group(2)) for m in _MD_IMG.finditer(ln)]
        found += [("<img>", m.group(1)) for m in _HTML_IMG.finditer(ln)]
        for alt, target in found:
            if re.match(r"^[a-z]+:", target):
                continue  # a URL (badge, external image)
            # the caption is the next non-blank line, an italic paragraph of its own
            nxt = next((x.strip() for x in lines[i + 1 :] if x.strip()), "")
            standalone = ln.strip().startswith("![")
            italic = nxt.startswith("*") and not nxt.startswith("**") and nxt.endswith("*")
            cap_ok = (not standalone) or italic
            refs.append((i + 1, alt, target.split("#")[0], cap_ok))
    return refs


def expected_outputs() -> list:
    return sorted(set(FIGURES) | set(THUMBS) | set(SCREENSHOTS))


def check() -> list:
    errs = []
    referenced = set()
    for md in _md_files():
        rel_md = os.path.relpath(md, ROOT)
        for line, alt, target, cap_ok in _image_refs(md):
            full = os.path.normpath(os.path.join(os.path.dirname(md), target))
            where = f"{rel_md}:{line}"
            if not os.path.isfile(full):
                errs.append(f"{where}: missing image {target}")
                continue
            size = os.path.getsize(full)
            if size > MAX_BYTES:
                errs.append(f"{where}: {target} is {size // 1024} KB (cap {MAX_BYTES // 1024} KB)")
            if not alt.strip():
                errs.append(f"{where}: {target} has no alt text")
            if not cap_ok and md.startswith(DOCS):
                errs.append(f"{where}: {target} needs an italic caption line after it")
            if full.startswith(IMG + os.sep):
                referenced.add(os.path.relpath(full, IMG).replace(os.sep, "/"))
    expected = set(expected_outputs())
    on_disk = set()
    total = 0
    if os.path.isdir(IMG):
        for d, _dirs, files in os.walk(IMG):
            for f in files:
                rel = os.path.relpath(os.path.join(d, f), IMG).replace(os.sep, "/")
                on_disk.add(rel)
                total += os.path.getsize(os.path.join(d, f))
    for rel in sorted(expected - on_disk):
        errs.append(f"docs/img/{rel}: not generated (run scripts/docs_figures.py)")
    for rel in sorted(on_disk - expected):
        errs.append(f"docs/img/{rel}: stale (no generator in scripts/docs_figures.py)")
    for rel in sorted((expected & on_disk) - referenced):
        errs.append(f"docs/img/{rel}: generated but referenced from no page")
    if total > MAX_TOTAL:
        errs.append(f"docs/img: {total // 1024} KB in total (cap {MAX_TOTAL // 1024} KB)")
    return errs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="verify only; render nothing")
    ap.add_argument("--screenshots", action="store_true", help="also take browser screenshots")
    ap.add_argument("--only", default="", help="render figures whose path starts with this")
    ap.add_argument("--no-figures", action="store_true", help="skip the matplotlib figures")
    args = ap.parse_args(argv)
    if args.check:
        errs = check()
        for e in errs:
            print(e)
        n = len(expected_outputs())
        print(f"docs figures: {n} expected, {len(errs)} problem(s)")
        return 1 if errs else 0
    sys.path.insert(0, ROOT)
    done = [] if args.no_figures else render(args.only)
    if args.screenshots:
        done += screenshots()
    for rel in done:
        print(f"{os.path.getsize(os.path.join(IMG, rel)) // 1024:>4} KB  docs/img/{rel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
