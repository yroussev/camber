"""0.107: the M&V report, propose and plan paths agree with ``camber mv run`` (#205-#211).

One regression test per issue: the entry's extrapolation policy on every path (#205), one ledger
date window for run and report (#206), segments matched to results by version (#207), non-finite
normal-year projections masked and counted (#208), registry labels in the text a person reads
(#209), the CUSUM unit from the meter (#210) and a true fleet median (#211).
"""

import os
import sys
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_mandv_rebaseline import _workspace  # noqa: E402

from camber.cli import main  # noqa: E402
from camber.config import load_config, run_mv_config  # noqa: E402
from camber.mvrun import (  # noqa: E402
    MeterChain,
    _ledger_window,
    chained_report,
    plan_adjust,
    plan_freeze,
    propose,
)

_SEVERE = {
    "support_quantile": 0.2,
    "caveat_share": 0,
    "decline_share": 0.05,
    "caveat_distance": 0,
    "decline_distance": 0,
    "min_points_outside": 1,
}


@pytest.fixture(scope="module")
def v1(tmp_path_factory):
    """v1 frozen on 2016, data and the reporting period through 2017 (before the 2018 step)."""
    tmp = tmp_path_factory.mktemp("mv0107")
    _ws, cfg, store = _workspace(tmp, end="2017-12-31")
    assert main(["mv", "freeze", cfg, "--reason", "init", "--by", "ana", "--apply"]) == 0
    return cfg, store, os.path.dirname(cfg)


def _cfg(v1, **entry):
    cfg, _store, base = v1
    c = load_config(cfg)
    c["mv"][0].update(entry)
    return c, base


def test_205_report_and_propose_apply_the_entry_extrapolation_policy(v1):
    c, base = _cfg(v1, extrapolation=dict(_SEVERE))
    (sav,) = [f for f in run_mv_config(c, base_dir=base) if f.rule == "mv_savings"]
    assert sav.metrics["declined"] and "T4" in sav.metrics["declined_reason"]
    (m,) = chained_report(c, base_dir=base)["meters"]
    # the report reaches the run's verdict: no saving is reported for v1
    assert any(t.id == "T4" and not t.resolved for t in m.triggers)
    assert m.chain is None or m.chain.declined
    assert all(ln.declined for ln in m.links)
    assert m.segments[0].get("declined") or m.segments[0].get("period") is None
    doc = propose(c, base_dir=base)
    keys = [t["key"] for t in doc["meters"][0]["rebaseline"]["triggers"]]
    assert any(k.startswith("T4") for k in keys)
    # without the block, the default policy grades the same data in range
    c0, _ = _cfg(v1)
    (m0,) = chained_report(c0, base_dir=base)["meters"]
    assert m0.links and not m0.links[0].declined
    assert not any(t.id == "T4" for t in m0.triggers)


def test_206_a_ledger_entry_before_the_reporting_start_restates_both_paths(v1, tmp_path):
    c, base = _cfg(v1)
    other = str(tmp_path / "mv.json")
    pf = plan_freeze(c, base_dir=base, reason="r", accepted_by="a", store_path=other)
    pf.store.save(other)
    nra = {
        "kind": "nra",
        "method": "engineering",
        "start": "2017-01-10",  # 5 days before the reporting start (2017-01-15)
        "amount": 5000.0,
        "se": 500.0,
        "reason": "temporary load",
        "evidence": "work order",
    }
    pa = plan_adjust(
        c, base_dir=base, equip="meter", specs=[nra], reason="r", accepted_by="a", store_path=other
    )
    assert pa.changes, pa.refused
    pa.store.save(other)
    c["mv_store"] = other
    # run path
    (sav,) = [f for f in run_mv_config(c, base_dir=base) if f.rule == "mv_savings"]
    run_adj = sav.metrics.get("adjusted_savings")
    assert run_adj is not None
    assert run_adj == pytest.approx(sav.metrics["avoided_energy"] + 5000.0, abs=1.0)
    # report path: the same restated saving
    (m,) = chained_report(c, base_dir=base, store_path=other)["meters"]
    assert m.adjusted[0] is not None
    assert m.adjusted[0].savings == pytest.approx(run_adj, abs=1.0)


def test_206_ledger_window_helper():
    a = [SimpleNamespace(start=s) for s in ("2015-12-31", "2016-03-01", "2017-01-10", "2018-01-01")]
    got = _ledger_window(a, "2016-01-01", "2017-12-31")
    assert [x.start for x in got] == ["2016-03-01", "2017-01-10"]
    assert len(_ledger_window(a, None, "2017-12-31")) == 3
    assert _ledger_window(None, "2016-01-01", "2017-12-31") == []


def _result(version, savings):
    return SimpleNamespace(
        baseline_version=version,
        savings=savings,
        declined=False,
        declined_reason=None,
        abs_uncertainty=10.0,
        enpi=0.9,
        coverage={"tier": "in_range"},
    )


def test_207_reported_segments_are_matched_by_version():
    from camber.report.mv import mv_report_html

    segs = [
        {"version": "v1", "period": ["2017-01-02", "2017-06-30"], "declined": "no data"},
        {"version": "v2", "period": ["2018-01-01", "2018-06-30"]},
    ]
    m = MeterChain(
        "meter",
        "mv_power",
        [],
        segs,
        [_result("v2", 4321.0)],
        None,
        [None],
        pd.DataFrame(columns=["version", "projected", "actual"]),
        [],
    )
    html = mv_report_html({"facility_id": "f1", "meters": [m]}, charts=False)
    seg_html = html.split("<h3>Reported segments</h3>")[1]
    rows = seg_html.split("<tr>")[2:]
    assert "v1" in rows[0] and "no data" in rows[0] and "4,321" not in rows[0]
    assert "v2" in rows[1] and "4,321" in rows[1]


def _cooling(T, base=50.0, slope=2.0, cp=65.0):
    return base + slope * np.maximum(0.0, T - cp)


@pytest.mark.parametrize("bad", [np.nan, -237.0])
def test_208_non_finite_normal_year_projections_are_masked(bad):
    from camber.mandv.methods import standard_conditions_savings
    from camber.mandv.models import fit_model
    from camber.mandv.normalized import normalized_savings

    rng = np.random.default_rng(208)
    T = rng.uniform(30, 100, 365)
    mb = fit_model(T, _cooling(T) + rng.normal(0, 1, 365), "3PC")
    mr = fit_model(T, 0.85 * _cooling(T) + rng.normal(0, 1, 365), "3PC")
    normal = rng.uniform(30, 100, 365)
    clean = normalized_savings(mb, mr, normal, baseline_cv_rmse=0.03, n_baseline=365)
    assert clean.n_excluded == 0 and not any("excluded" in c for c in clean.caveats)
    dirty = normal.copy()
    dirty[17] = bad
    r = normalized_savings(mb, mr, dirty, baseline_cv_rmse=0.03, n_baseline=365)
    assert not r.declined and r.n_excluded == 1 and r.n_normal_periods == 364
    for x in (r.nac_baseline, r.normalized_savings, r.abs_uncertainty):
        assert x is not None and np.isfinite(x)
    assert any("1 of 365 normal-year periods were excluded" in c for c in r.caveats)
    keep = np.ones(365, bool)
    keep[17] = False
    ref = normalized_savings(mb, mr, normal[keep], baseline_cv_rmse=0.03, n_baseline=365)
    assert r.normalized_savings == ref.normalized_savings
    # standard conditions: the same rows, the same caveat, finite figures
    for kernel in ("exact", "g14"):
        kw = {} if kernel == "exact" else {"baseline_cv_rmse": 0.03, "n_baseline": 365,
                                           "p_baseline": 3}  # fmt: skip
        sc = standard_conditions_savings(mb, mr, dirty, kernel=kernel, **kw)
        assert not sc.declined and np.isfinite(sc.savings) and np.isfinite(sc.abs_uncertainty)
        assert any("normal-year periods were excluded" in c for c in sc.caveats)
    # none left: declined, never a NaN
    allbad = np.full(10, bad)
    d = normalized_savings(mb, mr, allbad, baseline_cv_rmse=0.03, n_baseline=365)
    assert d.declined and d.normalized_savings is None and d.n_excluded == 10
    assert "no normal-year period could be projected" in d.declined_reason
    sc = standard_conditions_savings(mb, mr, allbad)
    assert sc.declined and sc.savings is None and sc.enpi is None


def test_209_registry_labels_in_the_text_a_person_reads(v1, capsys):
    from camber.report.rcx import _sec_mv
    from camber.rules.base import Finding

    c, base = _cfg(v1)
    (b,) = [f for f in run_mv_config(c, base_dir=base) if f.rule == "mv_baseline"]
    assert "frozen M&V baseline v1 (3P cooling," in b.summary
    assert b.metrics["model"] == "3PC"  # the metrics keep the code
    cfg, _store, _base = v1
    assert main(["mv", "list", cfg]) == 0
    out = capsys.readouterr().out
    assert "3P cooling method=forecast" in out
    # the freeze plan text (a dry run against a fresh store)
    assert main(["mv", "freeze", cfg, "--reason", "r", "--store", cfg + ".other.json"]) == 0
    assert "3P cooling R2" in capsys.readouterr().out
    fs = [
        Finding("mv_baseline", "m1", "ok", {"model": "5P", "change_points": []}),
        Finding("mv_baseline", "m2", "ok", {"model": "3PHZ"}),
    ]
    sec = _sec_mv({"ctx": SimpleNamespace(run=SimpleNamespace(drift=None)), "findings": fs})
    (tbl,) = [blk for blk in sec["blocks"] if blk.get("kind") == "table"]
    assert [r[2] for r in tbl["rows"]] == ["2P (5P fallback)", "3P heating, zero base (CAMBER)"]


def test_210_cusum_unit_comes_from_the_meter(v1):
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    from camber.charts.cusum_chart import chained_cusum_plot, cusum_plot
    from camber.report.mv import _energy_unit

    idx = pd.date_range("2017-01-01", periods=5, freq="D")
    f = pd.DataFrame({"version": "v1", "projected": 10.0, "actual": 9.0}, index=idx)
    assert chained_cusum_plot(f).get_ylabel() == "Cumulative energy"
    assert chained_cusum_plot(f, units="therm").get_ylabel() == "Cumulative therm"
    s = pd.Series(10.0, index=idx)
    assert cusum_plot(s, s * 0.9).get_ylabel() == "Cumulative energy"
    plt.close("all")
    c, base = _cfg(v1)
    (m,) = chained_report(c, base_dir=base)["meters"]
    assert m.meter_unit is None and _energy_unit(m) is None
    c["mv"][0]["units"] = "Btu/h"
    (m,) = chained_report(c, base_dir=base)["meters"]
    assert m.meter_unit == "Btu" and _energy_unit(m) == "Btu"
    bill = SimpleNamespace(units=None, billing={"units": "therm"}, meter_unit="therm")
    assert _energy_unit(bill) == "therm"
    sysu = SimpleNamespace(units={"energy_unit": "kBtu"}, billing=None, meter_unit="kWh")
    assert _energy_unit(sysu) == "kBtu"


def test_211_fleet_median_is_a_true_median():
    from camber.report.fleet import build_fleet_report

    r = build_fleet_report([{"site": s, "eui": e, "findings": []}
                            for s, e in zip("ABCD", (40.0, 60.0, 80.0, 200.0))])  # fmt: skip
    assert r.peer_median_eui == 70.0
    by = {b.site: b.pct_vs_median for b in r.buildings}
    assert by == {"A": -43, "B": -14, "C": 14, "D": 186}
    r2 = build_fleet_report([{"site": "A", "eui": 50.0, "findings": []},
                             {"site": "B", "eui": 100.0, "findings": []}])  # fmt: skip
    assert r2.peer_median_eui == 75.0


def test_225_t4_is_dated_at_the_reporting_start_on_every_path(v1):
    c, base = _cfg(v1, extrapolation=dict(_SEVERE))
    start = c["mv"][0]["reporting_period"][0]
    run = [f for f in run_mv_config(c, base_dir=base) if f.rule == "mv_trigger"]
    run_t4 = sorted(f.metrics["key"] for f in run if f.metrics["key"].startswith("T4"))
    (m,) = chained_report(c, base_dir=base)["meters"]
    rep_t4 = sorted(t.key for t in m.triggers if t.id == "T4")
    doc = propose(c, base_dir=base)
    prop_t4 = sorted(
        t["key"] for t in doc["meters"][0]["rebaseline"]["triggers"] if t["id"] == "T4"
    )
    assert run_t4 == rep_t4 == prop_t4 == [f"T4:{start}"]
    # the report declines the saving from the reporting start, as the run does
    assert m.segments[0].get("declined", "").startswith("unresolved trigger T4")
