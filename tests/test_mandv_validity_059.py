"""M&V validity: a short-baseline caveat on the config path; degree-day slope signs (#59)."""

import os

import numpy as np
import pandas as pd
import pytest

from camber.config import run_config
from camber.mandv.degreeday import DegreeDayModel, fit_degree_day
from camber.mandv.stats import logical_signs, model_regression_tests, sep_validity
from camber.model.roles import Role
from camber.mvrun import baseline_window_check
from camber.portfolio import Portfolio


def _store_cfg(tmp_path, days, **entry_extra):
    rng = np.random.default_rng(1)
    idx = pd.date_range("2026-01-01", periods=days * 24, freq="1h")
    doy = idx.normalize().dayofyear.to_numpy()
    oat = 40 + 25 * np.sin((doy - 100) / 365 * 2 * np.pi) + rng.normal(0, 3, len(idx))
    kw = 40 + 1.2 * np.maximum(0, 55 - oat) + rng.normal(0, 3, len(idx))
    ws = str(tmp_path / "ws")
    pf = Portfolio.init(ws)
    pf.add_facility("s", facility_id="f1", reason="t", activate=True)
    pf.store.write_role_frame(
        pd.DataFrame({Role.POWER: kw, Role.OAT: oat}, index=idx),
        facility_id="f1",
        equip="m",
        equip_class="METER",
    )
    end = str((idx[-1]).date())
    entry = {"class": "METER", "role": "power", "period": ["2026-01-01", end], **entry_extra}
    return {
        "source": {"kind": "store", "store": os.path.join(ws, "store"), "facility_id": "f1"},
        "equipment": [{"class": "METER"}],
        "mv": [entry],
    }


def test_config_mv_path_caveats_a_short_baseline(tmp_path):
    cfg = _store_cfg(tmp_path, 200)
    b = [f for f in run_config(cfg).findings if f.rule == "mv_baseline"][0]
    assert b.metrics["short_baseline"] is True
    assert any("200 days" in c and "needs 365" in c for c in b.caveats)
    assert "(short baseline)" in b.summary


def test_the_caveat_follows_to_the_savings_and_proposal_findings(tmp_path):
    cfg = _store_cfg(tmp_path, 200)
    cfg["mv"][0]["period"] = ["2026-01-01", "2026-04-30"]
    cfg["mv"][0]["reporting_period"] = ["2026-05-01", "2026-07-19"]
    for method in ("forecast", "auto"):
        cfg["mv"][0]["method"] = method
        fs = [f for f in run_config(cfg).findings if f.rule != "mv_baseline"]
        assert fs and all(any("short or gappy baseline" in c for c in f.caveats) for f in fs)


def test_a_full_year_baseline_has_no_caveat(tmp_path):
    cfg = _store_cfg(tmp_path, 366)
    b = [f for f in run_config(cfg).findings if f.rule == "mv_baseline"][0]
    assert b.metrics["short_baseline"] is False
    assert not any("short or gappy" in c for c in b.caveats)


def test_baseline_window_check_is_the_one_rule():
    assert baseline_window_check(365, ["2025-01-01", "2025-12-31"], {})[1] is None
    miss, why = baseline_window_check(300, ["2025-01-01", "2025-12-31"], {})
    assert why and miss == pytest.approx(65 / 365)
    assert "needs 365" in baseline_window_check(200, ["2025-01-01", "2025-07-19"], {})[1]


# ------------------------------------------------------------------------------ degree-day signs

T = np.array([19, 21, 40, 47, 57, 71, 74, 69, 65, 54, 39, 22, 16, 28, 40, 51, 59.0])


def _heating_only(seed=2):
    E = 1100 + 40 * np.maximum(0, 55 - T) - 3 * np.maximum(0, T - 55)
    return E + np.random.default_rng(seed).normal(0, 60, len(T))


def test_both_fit_prefers_a_logical_balance_point():
    m = fit_degree_day(T, _heating_only())
    assert m.heating_slope >= 0 and m.cooling_slope >= 0
    assert m.caveats == [] and m.fit.accept


def test_a_wrong_sign_is_declined_with_a_caveat():
    m = fit_degree_day(T, _heating_only(), balance_point=53.0)  # the v0.90.0 pick
    assert m.cooling_slope < 0
    assert m.fit.accept is False
    assert m.caveats and "cooling slope" in m.caveats[0] and "kind='heating'" in m.caveats[0]
    rt = DegreeDayModel.from_dict(m.as_dict())
    assert rt.caveats == m.caveats
    # a heating-only model with a negative slope is declined too
    h = fit_degree_day(T, 2000 - 10 * np.maximum(0, 60 - T), kind="heating", balance_point=60.0)
    assert h.heating_slope < 0 and not h.fit.accept and "heating slope" in h.caveats[0]


def test_logical_signs_cover_degree_day_models_and_the_sep_test_uses_them():
    m = fit_degree_day(T, _heating_only(), balance_point=53.0)
    assert logical_signs(m) == {"heating_slope": 1, "cooling_slope": 1}
    tests = model_regression_tests(m, T, _heating_only())
    v = sep_validity(tests, signs=logical_signs(m))
    assert not v.sep_valid and any("cooling_slope" in f for f in v.failures)
    hm = fit_degree_day(T, _heating_only(), kind="heating")
    assert logical_signs(hm) == {"heating_slope": 1}
    cm = fit_degree_day(T, 500 + 20 * np.maximum(0, T - 60), kind="cooling")
    assert logical_signs(cm) == {"cooling_slope": 1}
