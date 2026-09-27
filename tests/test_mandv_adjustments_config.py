"""The ``mv[].adjustments`` config key and the adjustment waterfall chart (#21 phase 21c)."""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_store_backed import _mv_store  # noqa: E402

from camber.config import run_config  # noqa: E402


def _cfg(tmp_path, adjustments, **extra):
    cfg = _mv_store(tmp_path)
    cfg["mv"][0].update(
        {
            "period": ["2016-01-01", "2016-06-30"],
            "reporting_period": ["2016-09-15", "2016-11-30"],
            "adjustments": adjustments,
            **extra,
        }
    )
    return cfg


def _savings(cfg):
    (s,) = [f for f in run_config(cfg).findings if f.rule == "mv_savings"]
    return s


_ENG = {
    "kind": "nra",
    "method": "engineering",
    "start": "2016-10-01",
    "reason": "temporary chiller rental",
    "amount": 900.0,
    "se": 100.0,
    "evidence": "rental invoice",
}
_IND = {"kind": "nra", "method": "indicator", "start": "2016-10-20", "reason": "suspected step"}


def test_mv_adjustments_restate_the_saving(tmp_path):
    cfg = _cfg(tmp_path, [_ENG, _IND])
    s = _savings(cfg)
    m = s.metrics
    assert m["adjusted"] is True
    assert [e["method"] for e in m["adjustments"]] == ["engineering", "indicator"]
    ind = m["adjustments"][1]
    assert ind["fit_period"] == "reporting"
    assert ind["fit"]["df"] == ind["fit"]["n"] - ind["fit"]["p"]
    # the engineering entry is material (900 >= 2 x 100); the indicator's flag is computed too
    assert m["adjustments"][0]["material"] is True and isinstance(ind["material"], bool)
    assert m["adjusted_savings"] == pytest.approx(
        m["avoided_energy"] + 900.0 + ind["resolved_amount"], abs=0.05
    )
    assert m["adjusted_abs_uncertainty"] > m["abs_uncertainty"]
    assert m["waterfall"][0]["label"] == "baseline projection"
    assert "adjusted for 2 non-routine/static entries (" in s.summary
    assert any("IPMVP 2012 §8.2" in c for c in s.caveats)
    json.dumps(m, allow_nan=False)


def test_mv_adjustments_refused_near_an_ecm_are_reported_not_applied(tmp_path):
    cfg = _cfg(tmp_path, [_IND], ecm_dates=["2016-10-25"])
    s = _savings(cfg)
    assert s.metrics["adjusted"] is None and "settle window" in s.metrics["adjustments_refused"]
    assert "adjustments refused" in s.summary
    assert s.metrics["avoided_energy"] is not None  # the unadjusted saving still stands
    cfg = _cfg(tmp_path, [_ENG], validity="sep")
    s = _savings(cfg)
    assert "approved_by" in s.metrics["adjustments_refused"]


def test_mv_adjustments_config_errors(tmp_path):
    static = {
        "kind": "static",
        "method": "proportional",
        "factor": "floor area",
        "start": "2016-09-15",
        "reason": "new wing",
        "baseline_value": 100,
        "reporting_value": 110,
    }
    with pytest.raises(ValueError, match="affected_share"):
        run_config(_cfg(tmp_path, [static]))
    with pytest.raises(ValueError, match="fit_period"):
        run_config(_cfg(tmp_path, [{**_IND, "fit_period": "later"}]))
    with pytest.raises(ValueError, match="list"):
        run_config(_cfg(tmp_path, {"kind": "nra"}))
    with pytest.raises(ValueError, match="unknown method"):
        run_config(_cfg(tmp_path, [{"kind": "nra", "method": "guess", "start": "2016-10-01"}]))
    cfg = _cfg(tmp_path, [_ENG])
    del cfg["mv"][0]["reporting_period"]
    with pytest.raises(ValueError, match="reporting_period"):
        run_config(cfg)
    ok = _cfg(tmp_path, [{**static, "affected_share": 0.4}])
    m = _savings(ok).metrics
    assert m["adjustments"][0]["multiplier"] == pytest.approx(1.04)


def test_baseline_period_indicator_from_config(tmp_path):
    ind = {**_IND, "start": "2016-03-01", "end": "2016-03-20", "reason": "spring break"}
    m = _savings(_cfg(tmp_path, [ind])).metrics
    assert m["adjusted"] is True and m["adjustments"][0]["fit_period"] == "baseline"
    assert m["waterfall"][1]["label"] == "baseline refit with indicator"


def test_adjustment_waterfall_chart():
    import matplotlib

    matplotlib.use("Agg")
    from camber.charts import adjustment_waterfall
    from camber.mandv.adjustments import NonRoutineAdjustment, apply_adjustments
    from camber.mandv.models import fit_model
    from camber.mandv.stats import avoided_energy_savings

    rng = np.random.default_rng(0)
    Tb, Tr = rng.uniform(40, 95, 200), rng.uniform(40, 95, 200)
    m = fit_model(Tb, 50 + 2 * np.maximum(0, Tb - 65) + rng.normal(0, 2, 200), "3PC")
    yr = 45 + 1.8 * np.maximum(0, Tr - 65) + rng.normal(0, 2, 200)
    sav = avoided_energy_savings(m, Tr, yr, cv_rmse=0.03, n_baseline=200, p_baseline=3)
    entries = [
        NonRoutineAdjustment(method="engineering", start="2024-01-01", reason="new load",
                             amount=800.0, se=50.0, evidence="study"),
        NonRoutineAdjustment(method="submeter", start="2024-02-01", reason="tenant out",
                             amount=-300.0, se=400.0, evidence="sub-meter"),
    ]  # fmt: skip
    adj = apply_adjustments(sav, entries, baseline_actual=11000.0)
    ax = adjustment_waterfall(adj)
    assert len(ax.patches) == len(adj.waterfall) == 8
    labels = [t.get_text() for t in ax.get_xticklabels()]
    assert "NRA engineering: new load *" in labels  # material
    assert "NRA submeter: tenant out" in labels  # not material (300 < 2 x 400)
    assert "adjusted savings" in ax.get_title()
    assert pd.notna(adj.abs_uncertainty)
