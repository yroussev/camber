"""0.98 (#88): an M&V refusal for too little baseline data says what data is needed.

``camber.mandv.sufficiency`` states the gap; the CalTRACK entry points raise it on
``InsufficientBaseline`` (a ``ValueError`` with the message unchanged), the config daily decline
carries it in ``metrics["data_needed"]``, and the RCx report prints it.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.config import run_config  # noqa: E402
from camber.mandv.caltrack import caltrack_savings, caltrack_savings_hourly  # noqa: E402
from camber.mandv.sufficiency import InsufficientBaseline, baseline_need  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402


def test_baseline_need_hourly_and_daily():
    h = baseline_need("hourly", 168, min_n=1440)
    assert h == {
        "interval": "hourly",
        "required": 1440,
        "have": 168,
        "shortfall": 1272,
        "unit": "hours",
        "days_short": 53,
        "text": (
            "1,440 baseline hours needed, 168 available: 1,272 more hours (about 53 days of data)"
        ),
    }
    d = baseline_need("daily", 12, min_n=60)
    assert (d["shortfall"], d["days_short"], d["unit"]) == (48, 48, "days")
    assert d["text"] == "60 baseline days needed, 12 available: 48 more days"
    ok = baseline_need("daily", 90, min_n=60)
    assert ok["shortfall"] == 0 and ok["text"] == "60 baseline days needed, 90 available"
    bins = baseline_need("hourly", 120, min_n=168, unit="hour-of-week bins")
    assert bins["days_short"] is None and bins["shortfall"] == 48
    thin = baseline_need("hourly", 3, min_n=4, unit="observations", days_per_unit=7.0)
    assert thin["days_short"] == 7
    with pytest.raises(ValueError, match="interval"):
        baseline_need("weekly", 1, min_n=2)


def test_insufficient_baseline_is_a_value_error_with_the_old_message():
    e = InsufficientBaseline("need >= 1440 baseline hours, got 168", {"shortfall": 1272})
    assert isinstance(e, ValueError)
    assert str(e) == "need >= 1440 baseline hours, got 168"
    assert e.need == {"shortfall": 1272}


def _hourly(start, hours, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=hours, freq="1h")
    t = 60 + 10 * np.sin(np.arange(hours) / 24 * 2 * np.pi) + rng.normal(0, 1, hours)
    e = 50 + np.clip(t - 60, 0, None) * 2 + rng.normal(0, 1, hours)
    return pd.Series(e, index=idx), pd.Series(t, index=idx)


def test_caltrack_entry_points_carry_the_need():
    eb, tb = _hourly("2024-01-01", 168)
    er, tr = _hourly("2024-03-01", 168, seed=1)
    with pytest.raises(InsufficientBaseline) as hi:
        caltrack_savings_hourly(eb, tb, er, tr)
    assert str(hi.value) == "need >= 1440 baseline hours, got 168"  # byte-identical
    assert (hi.value.need["shortfall"], hi.value.need["days_short"]) == (1272, 53)

    with pytest.raises(InsufficientBaseline) as di:
        caltrack_savings(eb, tb, er, tr)
    assert str(di.value) == "need >= 60 baseline days, got 7"
    assert di.value.need["shortfall"] == 53 and di.value.need["unit"] == "days"

    # weekday-only: long enough, but 48 hour-of-week bins never seen
    eb, tb = _hourly("2024-01-01", 24 * 140)
    wk = eb.index.dayofweek < 5
    with pytest.raises(InsufficientBaseline, match="168 hour-of-week bins") as bi:
        caltrack_savings_hourly(eb[wk], tb[wk], er, tr)
    assert bi.value.need["unit"] == "hour-of-week bins" and bi.value.need["have"] == 120

    # three weeks: every bin seen, but 3 < 4 observations each -- one more week needed
    eb, tb = _hourly("2024-01-01", 24 * 21)
    with pytest.raises(InsufficientBaseline, match="fewer than") as ti:
        caltrack_savings_hourly(eb, tb, er, tr, min_hours=24, min_obs_per_bin=4)
    assert (ti.value.need["shortfall"], ti.value.need["days_short"]) == (1, 7)


def _short_run(tmp_path, *, reporting=True, days=10):
    store = ParquetStore(str(tmp_path / "store"))
    e, t = _hourly("2024-01-01", 24 * days)
    frame = pd.DataFrame({Role.POWER: e.to_numpy(), Role.OAT: t.to_numpy()}, index=e.index)
    store.write_role_frame(frame, facility_id="f1", equip="meter", equip_class="M")
    entry = {"class": "M", "role": "power"}
    if reporting:
        entry.update(period=["2024-01-01", "2024-01-05"], reporting_period=["2024-01-06", None])
    cfg = {
        "source": {"kind": "store", "store": str(tmp_path / "store"), "facility_id": "f1"},
        "equipment": [{"class": "M"}],
        "mv": [entry],
    }
    return run_config(cfg, base_dir=str(tmp_path))


def test_config_daily_decline_adds_data_needed(tmp_path):
    res = _short_run(tmp_path)
    by = {f.rule: f for f in res.findings}
    base, sav = by["mv_baseline"], by["mv_savings"]
    # the reason and summary are exactly as before; the need is added alongside
    assert base.metrics["declined_reason"] == "only 5 usable days (< 60)"
    assert base.summary == "meter: M&V baseline declined -- only 5 usable days (< 60)"
    assert sav.metrics["declined_reason"] == "no baseline: only 5 usable days (< 60)"
    for f in (base, sav):
        need = f.metrics["data_needed"]
        assert (need["required"], need["have"], need["shortfall"]) == (60, 5, 55)
        assert need["text"] == "60 baseline days needed, 5 available: 55 more days"


def test_rcx_prints_data_needed(tmp_path):
    from camber.report import build_rcx_report
    from camber.report.rcx import _mv_data_needed

    res = _short_run(tmp_path)
    html = build_rcx_report(res).to_html()
    assert html.count("Data needed (meter): 60 baseline days needed, 5 available") == 1
    assert _mv_data_needed([]) == []
