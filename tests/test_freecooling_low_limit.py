"""0.100: the economizer low-limit lockout (``low_limit_f``) in ``free_cooling_missed`` and
``camber.freecooling.free_cooling_opportunity``.

Below the lockout the sequence holds the OA damper at its minimum by design, so mechanical cooling
there is not missed free cooling. ``None`` (the default) must keep both outputs byte-identical.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.freecooling import free_cooling_opportunity  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.freecoolingmissed_rule import FreeCoolingMissed  # noqa: E402


def _frame(days=20):
    """Hourly OAT cycling 10-58 °F; cooling runs (damper at minimum) in every hour below 34 °F
    and in 1 of 4 hours above it -- a unit whose 'missed' hours are mostly lockout weather."""
    idx = pd.date_range("2025-01-01", periods=days * 24, freq="1h")
    n = len(idx)
    oat = pd.Series(34.0 + 24.0 * np.sin(np.arange(n) * 2 * np.pi / 24), index=idx)
    cold = oat < 33.8
    cool = pd.Series(np.where(cold | (np.arange(n) % 4 == 0), 40.0, 0.0), index=idx)
    rat = pd.Series(70.0, index=idx)
    mat = 0.1 * oat + 0.9 * rat  # 10 % OA: the damper at its minimum
    damper = pd.Series(10.0, index=idx)
    return pd.DataFrame(
        {
            Role.OAT: oat,
            Role.COOL_VALVE: cool,
            Role.OA_DAMPER: damper,
            Role.MIXED_AIR_TEMP: mat,
            Role.RETURN_AIR_TEMP: rat,
        }
    )


def _dump(f) -> str:
    return json.dumps(f.as_dict(), sort_keys=True, default=str)


def test_default_is_byte_identical_and_adds_no_metrics():
    fr = _frame()
    default = FreeCoolingMissed().analyze("AHU-1", fr)
    explicit = FreeCoolingMissed(low_limit_f=None).analyze("AHU-1", fr)
    assert _dump(default) == _dump(explicit)
    assert not any(k.startswith(("low_limit", "n_low_limit")) for k in default.metrics)
    assert "low-limit" not in default.summary
    assert not any("low-limit" in c for c in default.caveats)


def test_default_matches_the_pre_0100_finding():
    """The pre-0.100 test, written out: available = OAT < high limit & both signals present."""
    fr = _frame()
    f = FreeCoolingMissed().analyze("AHU-1", fr)
    oat, cool = fr[Role.OAT], fr[Role.COOL_VALVE]
    avail = (oat < 60.0) & oat.notna() & cool.notna()
    missed = avail & (cool > 5.0)
    assert f.metrics["n_free_cooling_samples"] == int(avail.sum())
    assert f.metrics["missed_pct"] == round(100.0 * missed.sum() / avail.sum(), 2)


def test_low_limit_takes_lockout_hours_out_of_the_opportunity():
    fr = _frame()
    before = FreeCoolingMissed().analyze("AHU-1", fr)
    after = FreeCoolingMissed(low_limit_f=33.8).analyze("AHU-1", fr)
    oat = fr[Role.OAT]
    n_low = int((oat < 33.8).sum())
    m = after.metrics
    assert m["low_limit_f"] == 33.8
    assert m["n_low_limit_excluded_samples"] == n_low
    assert m["low_limit_excluded_hours"] == float(n_low)  # hourly
    assert m["low_limit_cooling_hours"] == float(n_low)  # cooling ran in every lockout hour
    assert m["n_free_cooling_samples"] == before.metrics["n_free_cooling_samples"] - n_low
    assert m["missed_pct"] < before.metrics["missed_pct"]
    rest = (oat >= 33.8) & (oat < 60.0)
    expect = 100.0 * float((rest & (fr[Role.COOL_VALVE] > 5.0)).sum()) / float(rest.sum())
    assert m["missed_pct"] == round(expect, 2)
    assert before.severity == "fault" and after.severity == "fault"
    assert "low-limit lockout not judged" in after.summary
    assert any("33.8 °F economizer low-limit lockout" in c for c in after.caveats)


def test_low_limit_clears_a_unit_that_only_cools_in_lockout_weather():
    fr = _frame()
    fr[Role.COOL_VALVE] = np.where(fr[Role.OAT] < 33.8, 40.0, 0.0)
    assert FreeCoolingMissed().analyze("AHU-1", fr).severity == "fault"
    f = FreeCoolingMissed(low_limit_f=33.8).analyze("AHU-1", fr)
    assert f.severity == "ok" and f.metrics["missed_pct"] == 0.0
    assert f.metrics["missed_cause"] is None


def test_all_weather_below_the_low_limit_is_info_with_the_count():
    fr = _frame()
    fr[Role.OAT] = 20.0
    f = FreeCoolingMissed(low_limit_f=33.8).analyze("AHU-1", fr)
    assert f.severity == "info"
    assert f.metrics["n_low_limit_excluded_samples"] == len(fr)
    assert FreeCoolingMissed().analyze("AHU-1", fr).metrics.get("low_limit_f") is None


def test_low_limit_must_be_below_the_high_limit():
    with pytest.raises(ValueError, match="low_limit_f"):
        FreeCoolingMissed(high_limit_f=60.0, low_limit_f=60.0)
    oat = pd.Series([40.0, 50.0], index=pd.date_range("2025-01-01", periods=2, freq="1h"))
    with pytest.raises(ValueError, match="low_limit_f"):
        free_cooling_opportunity(oat, oat * 0 + 0.5, high_limit_f=55.0, low_limit_f=70.0)


def test_opportunity_default_is_byte_identical():
    fr = _frame()
    oat, sig = fr[Role.OAT], fr[Role.COOL_VALVE] / 100.0
    kw = pd.Series(np.where(sig > 0, 30.0, 0.0), index=oat.index)
    a = free_cooling_opportunity(oat, sig, cooling_kw=kw, price_per_kwh=0.1)
    b = free_cooling_opportunity(oat, sig, cooling_kw=kw, price_per_kwh=0.1, low_limit_f=None)
    assert json.dumps(a.as_dict(), sort_keys=True) == json.dumps(b.as_dict(), sort_keys=True)
    assert set(a.as_dict()) == {
        "hours_available",
        "hours_missed",
        "missed_fraction",
        "addressable_kwh",
        "recoverable_kwh",
        "savings_usd",
        "high_limit_f",
    }


def test_opportunity_and_rule_count_the_same_weather():
    fr = _frame()
    oat, sig = fr[Role.OAT], fr[Role.COOL_VALVE] / 100.0
    r = free_cooling_opportunity(oat, sig, low_limit_f=33.8)
    f = FreeCoolingMissed(low_limit_f=33.8).analyze("AHU-1", fr)
    assert r.low_limit_f == 33.8
    assert r.hours_low_limit_excluded == f.metrics["low_limit_excluded_hours"]
    assert r.hours_available == f.metrics["n_free_cooling_hours"]
    d = r.as_dict()
    assert d["low_limit_f"] == 33.8 and d["hours_low_limit_excluded"] > 0
    full = free_cooling_opportunity(oat, sig)
    assert r.hours_available == full.hours_available - r.hours_low_limit_excluded
    assert r.hours_missed < full.hours_missed


def test_stuck_damper_cause_survives_the_low_limit():
    """A damper commanded open that delivers ~minimum OA keeps damper_not_delivering."""
    fr = _frame()
    fr[Role.COOL_VALVE] = 40.0
    fr[Role.OA_DAMPER] = np.where(fr[Role.OAT] < 33.8, 10.0, 100.0)
    f = FreeCoolingMissed(low_limit_f=33.8).analyze("AHU-1", fr)
    assert f.metrics["missed_cause"] == "damper_not_delivering"
    assert f.metrics["commanded_open_pct"] == 100.0


def test_lbnl_sdahu_template_sets_the_documented_low_limit():
    from importlib import resources

    def fcm(name):
        cfg = json.loads(
            resources.files("camber.datasets").joinpath("configs", name).read_text("utf-8")
        )
        return next(
            r for r in cfg["rules"] if isinstance(r, dict) and r["name"] == "free_cooling_missed"
        )

    sd = fcm("lbnl-sdahu.json")
    assert sd["params"]["low_limit_f"] == 33.8
    assert sd["basis"]["low_limit_f"].startswith("documented:")
    FreeCoolingMissed(**sd["params"])  # a valid pair of limits
    # lbnl-ddahu documents no lockout, and its damper modulates below 33.8 F
    assert "low_limit_f" not in fcm("lbnl-ddahu.json")["params"]
