"""0.92 (#14): cooling-tower fouling read from fan effort at matched load and wet-bulb."""

import numpy as np
import pandas as pd

from camber.driftrun import build_drift_suite
from camber.model.roles import Role
from camber.plantdrift import diagnose_tower_drift
from camber.rules.tower_fan_effort_rule import CoolingTowerFanEffortDrift
from camber.store.modelstore import BaselineStore


def _tower(*, extra_fan=0.0, sensor_bias=0.0, days=30, seed=0, entering=True, rh=False):
    """Hourly tower holding leaving water at wet-bulb + 7 F; fan % ~ range and wet-bulb."""
    idx = pd.date_range("2026-07-01", periods=days * 24, freq="1h")
    rng = np.random.default_rng(seed)
    wb = 66 + 6 * np.sin(np.arange(len(idx)) / 24 * 2 * np.pi) + rng.normal(0, 1, len(idx))
    rng_f = 4 + 6 * rng.random(len(idx))  # condenser range, F
    fan = np.clip(
        20 + 5.0 * rng_f - 0.8 * (wb - 66) + extra_fan + rng.normal(0, 3, len(idx)), 0, 100
    )
    true_leave = wb + 7.0
    f = pd.DataFrame(
        {
            Role.TOWER_FAN_SPEED: fan / 100.0,  # a 0-1 command
            Role.CW_SUPPLY_TEMP: true_leave + sensor_bias,
            Role.CW_RETURN_TEMP: true_leave + rng_f,
        },
        index=idx,
    )
    if rh:
        f[Role.OAT] = wb + 12.0
        f[Role.OUTDOOR_RH] = 50.0
    else:
        f[Role.WETBULB_TEMP] = wb
    if entering:
        f[Role.COND_ENTERING_WATER_TEMP] = true_leave + 0.3 + rng.normal(0, 0.1, len(idx))
        f[Role.CW_BYPASS_VALVE] = 0.0
    return f


def _run(base, cur, **kw):
    rule = CoolingTowerFanEffortDrift(BaselineStore(), site="S", run_id="R", **kw)
    return rule.analyze_periods("CT-1", base, cur)


def test_fouled_tower_works_harder_at_matched_conditions():
    base = _tower()
    assert _run(base, _tower(seed=1)).severity == "ok"
    warn = _run(base, _tower(extra_fan=7.0, seed=1))
    assert warn.severity == "warn" and warn.metrics["attribution"] == "tower"
    fault = _run(base, _tower(extra_fan=15.0, seed=1))
    assert fault.severity == "fault" and 12 < fault.metrics["fan_effort_drift_pct"] < 18
    assert fault.metrics["covariate"] == "wetbulb" and fault.metrics["load_source"] == "cw_range"
    assert fault.metrics["high_fan_share_current"] >= fault.metrics["high_fan_share_baseline"]
    # one-sided: a tower needing less fan is not a fault
    assert _run(base, _tower(extra_fan=-15.0, seed=1)).severity == "ok"


def test_a_biased_leaving_water_sensor_is_named_not_fouling():
    base = _tower()
    f = _run(base, _tower(extra_fan=15.0, sensor_bias=2.0, seed=1))
    assert f.severity == "info" and f.metrics["attribution"] == "sensor_offset"
    assert abs(f.metrics["sensor_offset_shift_f"] - 2.0) < 0.2
    d = diagnose_tower_drift([f], equip="CT-1")
    assert d.locus == "sensor" and d.family == "tower"


def test_without_the_cross_check_it_says_so():
    base, cur = _tower(entering=False), _tower(extra_fan=15.0, seed=1, entering=False)
    f = _run(base, cur)
    assert f.severity == "fault" and any("cross-check" in c for c in f.caveats)
    assert diagnose_tower_drift([f], equip="CT-1").locus == "tower"


def test_wetbulb_derived_from_oat_and_rh_and_chw_tons_fallback():
    base, cur = _tower(rh=True), _tower(extra_fan=15.0, seed=1, rh=True)
    assert _run(base, cur).severity == "fault"

    # no condenser return: load from the chilled-water side
    def chw(f, seed):
        g = f.drop(columns=[Role.CW_RETURN_TEMP])
        r = np.random.default_rng(seed)
        g[Role.CHW_FLOW] = 400.0
        g[Role.CHW_SUPPLY_TEMP] = 44.0
        # tons track the fan's own load term: range 4-10 F <-> 67-167 tons
        rng_f = (f[Role.CW_RETURN_TEMP] - f[Role.CW_SUPPLY_TEMP]).to_numpy()
        g[Role.CHW_RETURN_TEMP] = 44.0 + rng_f + r.normal(0, 0.05, len(g))
        return g

    x = _run(chw(_tower(), 0), chw(_tower(extra_fan=15.0, seed=1), 1))
    assert x.metrics["load_source"] == "chw_tons" and x.severity == "fault"


def test_declines_honestly():
    base = _tower()
    no_load = base.drop(columns=[Role.CW_RETURN_TEMP])
    assert _run(no_load, no_load).metrics["reason"] == "no_load"
    off = _tower()
    off[Role.TOWER_FAN_SPEED] = 0.0
    assert _run(off, _tower()).metrics["reason"] == "no_baseline"
    no_wb = _tower().drop(columns=[Role.WETBULB_TEMP])
    assert _run(base, no_wb).metrics["reason"] == "no_wetbulb"
    f = _run(no_wb, no_wb)
    assert any("no wet-bulb" in c for c in f.caveats) and f.severity == "ok"
    off2 = CoolingTowerFanEffortDrift(BaselineStore(), freeze_if_missing=False)
    assert off2.analyze_periods("CT-1", base, base).metrics["declined"]


def test_tower_family():
    names = [r.name for r in build_drift_suite("tower", BaselineStore())]
    assert names == ["cooling_tower_approach_drift", "cooling_tower_fan_effort_drift"]
    base = _tower()
    ok = _run(base, _tower(seed=1))
    assert diagnose_tower_drift([ok], equip="CT-1").locus == "steady"
