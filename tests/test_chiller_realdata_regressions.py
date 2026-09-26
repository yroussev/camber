"""Regressions for chiller refrigerant-side defects found on open real datasets.

Each test reproduces one defect on **synthetic** data shaped like what the real data showed (a 5-ton
chiller, a CO2 gas cooler, a 1-minute trend whose residual wanders slowly, ...). Nothing here is
drawn from a measured dataset; the before/after numbers on the real data are recorded in
docs/VALIDATION.md.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.chillerbaseline import (  # noqa: E402
    LoadBaseline,
    fit_load_baseline,
    load_drift_stats,
    residual_lag1,
    size_relative_load_gates,
    unscoreable_reason,
)
from camber.chillerdrift import ApproachDriftMonitor  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules.chiller_drift_rule import ChillerApproachDrift  # noqa: E402
from camber.rules.chiller_head_pressure_rule import ChillerHeadPressureDrift  # noqa: E402
from camber.rules.chiller_subcooling_rule import ChillerSubcoolingDrift  # noqa: E402
from camber.rules.chiller_suction_pressure_rule import ChillerSuctionPressureDrift  # noqa: E402
from camber.rules.chiller_superheat_rule import ChillerSuperheatDrift  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402


def _chiller(n, start, tons, **metrics):
    """A chiller role-frame whose derived load is exactly ``tons`` (10 degF CHW dT)."""
    idx = pd.date_range(start, periods=n, freq="1h")
    out = pd.DataFrame(
        {
            Role.CHW_FLOW: np.asarray(tons, dtype=float) * 2.4,
            Role.CHW_SUPPLY_TEMP: 45.0,
            Role.CHW_RETURN_TEMP: 55.0,
        },
        index=idx,
    )
    for role, vals in metrics.items():
        out[Role(role)] = vals
    return out


# =========================================================================== 1. superheat/subcool


def _superheat_case(current_superheat, seed=0):
    rng = np.random.default_rng(seed)
    tons = rng.uniform(20, 100, 500)
    base = _chiller(
        500, "2024-01-01", tons, superheat_temp=10 + 0.02 * tons + rng.normal(0, 0.5, 500)
    )
    cur = _chiller(200, "2024-06-01", rng.uniform(20, 100, 200), superheat_temp=current_superheat)
    return ChillerSuperheatDrift(BaselineStore()).analyze_periods("CH", base, cur)


def test_liquid_floodback_below_zero_superheat_is_scored_not_declined():
    """-3 degF superheat (liquid at the suction) is the rule's most urgent fault -- it used to be
    filtered out by a 0-50 degF plausibility band and the rule declined."""
    rng = np.random.default_rng(1)
    f = _superheat_case(-3 + rng.normal(0, 0.5, 200))
    assert f.metrics.get("declined") is not True
    assert f.severity == "fault"
    assert f.metrics["superheat_drift_direction"] == "down"
    assert f.metrics["superheat_n_current"] == 200


def test_half_flooded_period_is_not_reported_ok():
    """Half the period flooded: the flooded half used to be dropped, leaving an 'ok -0.1 degF'."""
    rng = np.random.default_rng(2)
    f = _superheat_case(np.r_[-3 + rng.normal(0, 0.5, 100), 11 + rng.normal(0, 0.5, 100)])
    assert f.severity == "fault"
    assert f.metrics["superheat_n_current"] == 200


def test_starved_evaporator_above_50F_superheat_is_scored():
    rng = np.random.default_rng(3)
    f = _superheat_case(55 + rng.normal(0, 2, 200))
    assert f.metrics.get("declined") is not True
    assert f.severity == "fault" and f.metrics["superheat_drift_direction"] == "up"


def test_superheat_sentinels_stay_out():
    rng = np.random.default_rng(4)
    vals = 11 + rng.normal(0, 0.5, 200)
    vals[::10] = -999.0  # sentinel-coded dropouts
    f = _superheat_case(vals)
    assert f.metrics["superheat_n_current"] == 180
    assert f.severity == "ok"


def test_flash_gas_negative_subcooling_is_scored():
    rng = np.random.default_rng(5)
    tons = rng.uniform(20, 100, 500)
    base = _chiller(
        500, "2024-01-01", tons, subcooling_temp=8 + 0.02 * tons + rng.normal(0, 0.4, 500)
    )
    cur = _chiller(
        200, "2024-06-01", rng.uniform(20, 100, 200), subcooling_temp=-2 + rng.normal(0, 0.3, 200)
    )
    f = ChillerSubcoolingDrift(BaselineStore()).analyze_periods("CH", base, cur)
    assert f.metrics.get("declined") is not True
    assert f.severity == "fault" and f.metrics["subcooling_drift_direction"] == "down"


# =========================================================================== 2. size-relative gates


def test_size_relative_gates_never_exceed_and_keep_large_machines_unchanged():
    assert size_relative_load_gates(np.linspace(40, 400, 100)) == (5.0, 10.0)
    assert size_relative_load_gates(np.linspace(20, 60, 100)) == (5.0, 10.0)  # p95 ~58 t >= 50 t
    lo, span = size_relative_load_gates(np.linspace(3.5, 5.5, 100))  # a 5-ton chiller
    assert lo == pytest.approx(0.1 * np.percentile(np.linspace(3.5, 5.5, 100), 95))
    assert span == pytest.approx(2 * lo)
    assert size_relative_load_gates([3.0], min_load=1.0, min_load_span=0.5) == (1.0, 0.5)
    assert size_relative_load_gates([np.nan, 0.0]) == (5.0, 10.0)


def test_a_5_ton_chiller_is_scored_and_stays_specific():
    """3.5-5.5 t: below the old 5-ton floor and 10-ton span -- every detector declined 100%."""
    rng = np.random.default_rng(6)
    tb, tc = rng.uniform(3.5, 5.5, 400), rng.uniform(3.5, 5.5, 200)
    base = _chiller(400, "2025-01-01", tb, subcooling_temp=9 + 0.5 * tb + rng.normal(0, 0.4, 400))
    healthy = _chiller(
        200, "2025-03-01", tc, subcooling_temp=9 + 0.5 * tc + rng.normal(0, 0.4, 200)
    )
    leak = healthy.copy()
    leak[Role.SUBCOOLING_TEMP] -= 4.0
    ok = ChillerSubcoolingDrift(BaselineStore()).analyze_periods("CH", base, healthy)
    bad = ChillerSubcoolingDrift(BaselineStore()).analyze_periods("CH", base, leak)
    assert ok.metrics.get("declined") is not True and ok.severity == "ok"
    assert bad.severity == "fault"
    assert ok.metrics["subcooling_min_tons"] < 1.0


def test_gates_are_constructor_configurable():
    rng = np.random.default_rng(7)
    tb = rng.uniform(3.5, 5.5, 400)
    base = _chiller(400, "2025-01-01", tb, subcooling_temp=9 + rng.normal(0, 0.4, 400))
    cur = _chiller(200, "2025-03-01", rng.uniform(3.5, 5.5, 200), subcooling_temp=9.0)
    f = ChillerSubcoolingDrift(BaselineStore(), min_tons=6.0).analyze_periods("CH", base, cur)
    # an explicit floor above this machine's capacity: declines, and says precisely why
    assert f.metrics["declined"] is True
    assert any("no samples at tons >= 6" in c for c in f.caveats)


def test_fixed_capacity_machine_gets_a_flat_level_scored_only_near_its_load():
    rng = np.random.default_rng(8)
    base = _chiller(
        300, "2025-01-01", np.full(300, 4.0), superheat_temp=9 + rng.normal(0, 0.5, 300)
    )
    cur_t = np.r_[np.full(100, 4.0), np.full(100, 40.0)]  # half at its load, half far off it
    cur = _chiller(200, "2025-03-01", cur_t, superheat_temp=9 + rng.normal(0, 0.5, 200))
    f = ChillerSuperheatDrift(BaselineStore()).analyze_periods("CH", base, cur)
    assert f.metrics["superheat_baseline_model"] == "level"
    assert f.metrics["superheat_n_current"] == 100
    assert f.severity == "ok"
    assert any("flat level" in c for c in f.caveats)


def test_large_chiller_approach_behaviour_is_unchanged():
    """50+ ton machines keep the absolute 5 t / 10 t gates exactly."""
    rng = np.random.default_rng(9)
    tb = rng.uniform(40, 300, 500)
    base = _chiller(
        500, "2025-01-01", tb, cond_approach_temp=2 + 0.01 * tb + rng.normal(0, 0.25, 500)
    )
    tc = rng.uniform(40, 300, 200)
    cur = _chiller(
        200, "2025-03-01", tc, cond_approach_temp=4 + 0.01 * tc + rng.normal(0, 0.25, 200)
    )
    new = ChillerApproachDrift(BaselineStore()).analyze_periods("CH", base, cur)
    old = ChillerApproachDrift(BaselineStore(), min_tons=5.0, min_tons_span=10.0).analyze_periods(
        "CH", base, cur
    )
    assert new.severity == old.severity == "fault"
    assert new.metrics["cond_drift_f"] == old.metrics["cond_drift_f"]


# =========================================================================== 3./4. pressures


def _co2_gas_cooler(n, start, seed, offset=0.0):
    rng = np.random.default_rng(seed)
    kw = rng.uniform(4, 12, n)
    amb = rng.uniform(60, 95, n)
    p = 400 + 12.0 * amb + 8.0 * kw + offset + rng.normal(0, 5, n)  # ~1150-1650 psig
    return _chiller(n, start, kw, discharge_pressure=p, oat=amb)


def test_co2_transcritical_head_pressure_is_scored_not_declined():
    base, cur = _co2_gas_cooler(400, "2025-01-01", 1), _co2_gas_cooler(200, "2025-03-01", 2)
    assert base[Role.DISCHARGE_PRESSURE].max() > 1100
    f = ChillerHeadPressureDrift(BaselineStore()).analyze_periods("GC", base, cur)
    assert f.metrics.get("declined") is not True
    assert f.severity == "ok"


def test_pressure_sentinels_are_still_rejected():
    base, cur = _co2_gas_cooler(400, "2025-01-01", 1), _co2_gas_cooler(200, "2025-03-01", 2)
    cur.iloc[::10, cur.columns.get_loc(Role.DISCHARGE_PRESSURE)] = 9999.0
    f = ChillerHeadPressureDrift(BaselineStore()).analyze_periods("GC", base, cur)
    assert f.metrics["head_pressure_n_current"] == 180 and f.severity == "ok"


def test_head_pressure_regressed_on_heat_sink_temperature_sees_a_blockage():
    """Ambient swings 60-95 F: a load-only fit leaves ~120 psi of scatter, burying a +44 psi
    condenser blockage; regressed on OAT the scatter is ~5 psi and the blockage is plain."""
    base = _co2_gas_cooler(400, "2025-01-01", 1)
    cur = _co2_gas_cooler(200, "2025-03-01", 2, offset=44.0)
    with_oat = ChillerHeadPressureDrift(BaselineStore()).analyze_periods("GC", base, cur)
    load_only = ChillerHeadPressureDrift(
        BaselineStore(), normalize_on_condition=False
    ).analyze_periods("GC", base, cur)
    assert with_oat.metrics["head_pressure_covariate"] == "oat"
    assert with_oat.metrics["head_pressure_baseline_sigma_psi"] < 10
    assert load_only.metrics["head_pressure_baseline_sigma_psi"] > 50
    assert with_oat.severity == "fault" and with_oat.metrics["head_pressure_drift_sigma"] > 4
    assert load_only.severity == "ok"
    assert "outdoor-air temperature" in with_oat.summary
    assert any("load only" in c for c in load_only.caveats)


def test_entering_cw_is_preferred_and_wrong_sign_covariate_is_rejected():
    rng = np.random.default_rng(11)
    n = 400
    tons = rng.uniform(100, 300, n)
    cw = rng.uniform(70, 85, n)
    base = _chiller(
        n,
        "2025-01-01",
        tons,
        discharge_pressure=100 + 0.1 * tons + 1.5 * cw + rng.normal(0, 1, n),
        cw_supply_temp=cw,
        oat=rng.uniform(50, 90, n),
    )
    f = ChillerHeadPressureDrift(BaselineStore()).analyze_periods("CH", base, base.iloc[:100])
    assert f.metrics["head_pressure_covariate"] == "cw_supply_temp"
    # a covariate whose fitted effect has the physically wrong sign is not used
    bad = base.copy()
    bad[Role.CW_SUPPLY_TEMP] = 160 - cw  # anti-correlated with head pressure
    bad = bad.drop(columns=[Role.OAT])
    g = ChillerHeadPressureDrift(BaselineStore()).analyze_periods("CH", bad, bad.iloc[:100])
    assert "head_pressure_covariate" not in g.metrics
    assert any("wrong sign" in c for c in g.caveats)


def test_chilled_water_reset_does_not_false_alarm_suction_pressure():
    """Suction pressure follows leaving-CHW temperature: a reset to 52.7 F lifted it ~40 psi."""
    rng = np.random.default_rng(12)
    n = 400
    tons = rng.uniform(3, 6, n)
    chws = rng.uniform(40, 46, n)
    idx = pd.date_range("2025-01-01", periods=n, freq="1h")
    base = pd.DataFrame(
        {
            Role.CHW_FLOW: tons * 2.4,
            Role.CHW_SUPPLY_TEMP: chws,
            Role.CHW_RETURN_TEMP: chws + 10,
            Role.SUCTION_PRESSURE: 20 + 2.0 * tons + 2.0 * chws + rng.normal(0, 1.0, n),
        },
        index=idx,
    )
    cur = base.iloc[:120].copy()
    cur.index = pd.date_range("2025-03-01", periods=120, freq="1h")
    cur[Role.CHW_SUPPLY_TEMP] = 52.7
    cur[Role.CHW_RETURN_TEMP] = 62.7
    cur[Role.SUCTION_PRESSURE] = 20 + 2.0 * tons[:120] + 2.0 * 52.7 + rng.normal(0, 1.0, 120)
    f = ChillerSuctionPressureDrift(BaselineStore()).analyze_periods("CH", base, cur)
    off = ChillerSuctionPressureDrift(
        BaselineStore(), normalize_on_condition=False
    ).analyze_periods("CH", base, cur)
    assert f.metrics["suction_pressure_covariate"] == "chw_supply_temp"
    assert f.severity == "ok"
    assert off.severity == "fault"  # the old behaviour: a setpoint change read as a fault
    assert any("outside the range" in c for c in f.caveats)  # 52.7 F is extrapolated, and said


def test_covariate_baseline_round_trips_and_requires_its_covariate():
    rng = np.random.default_rng(13)
    n = 300
    fr = pd.DataFrame({"tons": rng.uniform(10, 50, n), "c": rng.uniform(60, 90, n)})
    fr["m"] = 5 + 0.2 * fr.tons + 0.5 * fr.c + rng.normal(0, 0.2, n)
    b = fit_load_baseline(fr, metric_col="m", covariate_col="c", min_covariate_span=2.0)
    assert b.covariate == "c" and b.covariate_slope == pytest.approx(0.5, abs=0.02)
    assert LoadBaseline.from_dict(b.as_dict()) == b
    assert b.predict(30.0, b.covariate_ref) == pytest.approx(b.predict(30.0))
    with pytest.raises(ValueError):
        load_drift_stats(b, fr, metric_col="m")
    # a covariate that only jitters is not identified
    fr["flat"] = 75 + rng.normal(0, 0.3, n)
    assert (
        fit_load_baseline(fr, metric_col="m", covariate_col="flat", min_covariate_span=2.0) is None
    )


# =========================================================================== 7. CUSUM cadence


def _ar1(n, rho, sigma, rng):
    e = rng.normal(0, sigma * np.sqrt(1 - rho**2), n)
    x = np.empty(n)
    x[0] = rng.normal(0, sigma)
    for i in range(1, n):
        x[i] = rho * x[i - 1] + e[i]
    return x


def _minute_frame(n, start, rng, rho, offset=0.0):
    idx = pd.date_range(start, periods=n, freq="1min")
    tons = rng.uniform(20, 100, n)
    return pd.DataFrame(
        {"tons": tons, "m": 10 + 0.02 * tons + offset + _ar1(n, rho, 0.5, rng)}, index=idx
    )


def test_residual_lag1_measures_serial_correlation_and_ignores_white_noise():
    rng = np.random.default_rng(14)
    wander = _minute_frame(3000, "2025-01-01", rng, 0.9)
    white = _minute_frame(3000, "2025-01-01", rng, 0.0)
    b1 = fit_load_baseline(wander, metric_col="m")
    b0 = fit_load_baseline(white, metric_col="m")
    assert b1.resid_lag1 == pytest.approx(0.9, abs=0.05) and b1.sample_seconds == 60.0
    assert b0.resid_lag1 == 0.0  # not significant -> no correction
    assert residual_lag1(pd.RangeIndex(50), np.zeros(50)) == (0.0, 0.0)


def test_cusum_does_not_false_alarm_on_slowly_wandering_minute_data():
    """Healthy days at 1-minute cadence, residual lag-1 ~0.9: the uncorrected CUSUM counted one
    slow excursion as dozens of independent samples and alarmed on most normal days."""
    rng = np.random.default_rng(15)
    base = _minute_frame(3000, "2025-01-01", rng, 0.9)
    b = fit_load_baseline(base, metric_col="m")
    raised = {True: 0, False: 0}
    for day in range(20):
        cur = _minute_frame(600, f"2025-02-{day + 1:02d}", rng, 0.9)
        for corr in (True, False):
            run = ApproachDriftMonitor(b, direction="both", autocorr=corr).run(
                cur, approach_col="m"
            )
            raised[corr] += run.alarmed
    assert raised[False] >= 8  # the defect, reproduced
    assert raised[True] <= 2


def test_cusum_still_catches_a_real_step_at_minute_cadence():
    rng = np.random.default_rng(16)
    b = fit_load_baseline(_minute_frame(3000, "2025-01-01", rng, 0.9), metric_col="m")
    cur = _minute_frame(600, "2025-02-01", rng, 0.9, offset=4 * b.sigma_f)
    run = ApproachDriftMonitor(b, direction="both").run(cur, approach_col="m")
    assert run.alarmed and run.alarm_direction == "up"
    assert run.sigma_inflation > 3


def test_cusum_carries_correlation_to_a_coarser_cadence():
    rng = np.random.default_rng(17)
    b = fit_load_baseline(_minute_frame(3000, "2025-01-01", rng, 0.9), metric_col="m")
    hourly = _minute_frame(3000, "2025-02-01", rng, 0.9).resample("1h").mean()
    run = ApproachDriftMonitor(b, direction="both").run(hourly, approach_col="m")
    # rho(1 h) = rho(1 min) ** 60 -- effectively independent, so essentially no inflation
    assert run.autocorr_lag1 == pytest.approx(b.resid_lag1**60, abs=1e-4)
    assert run.sigma_inflation < 1.01


# =========================================================================== decline reasons


def test_unscoreable_reason_walks_the_guards_in_order():
    fr = pd.DataFrame({"tons": [1.0, 2.0, 3.0], "m": [1.0, 2.0, 3.0]})
    kw = dict(metric_col="m", load_col="tons", metric_range=(0.0, 50.0), min_samples=2)
    assert "absent" in unscoreable_reason(fr.drop(columns="m"), min_load=0, **kw)
    assert ">= 5" in unscoreable_reason(fr, min_load=5, **kw)
    assert "plausible range" in unscoreable_reason(fr * [1, 100], min_load=0, **kw)
    assert "only 3 usable" in unscoreable_reason(fr, min_load=0, **{**kw, "min_samples": 10})
    assert "range is only" in unscoreable_reason(fr, min_load=0, min_load_span=10, **kw)
