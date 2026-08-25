"""Tests for the Option-D depth additions: 2R2C, multi-zone, and the EnergyPlus bridge.

The 1R1C path (tests/test_rc_model.py) stays untouched — these lock the new surface: 2R2C recovers
known params and *beats* 1R1C on mass-dominated data (earning its extra complexity), multi-zone
stacked-OLS recovers zones with differing schedules and is candid when they don't, the G14
refuse-to-fabricate posture holds, everything is deterministic, and the E+ bridge's compare logic
works with an injected runner (no engine needed).
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.rc_model import (  # noqa: E402
    Calibration,
    MultiZoneModel,
    RC2Model,
    RCModel,
    calibrate,
    calibrate2,
    calibrate_zones,
    daily_schedule,
    option_d_savings,
)
from camber.validation import check_determinism  # noqa: E402

_IDX = pd.date_range("2024-01-01", periods=24 * 28, freq="1h")  # 4 winter weeks, hourly


def _oat(seed=0):
    rng = np.random.default_rng(seed)
    return 40 + 15 * np.sin((_IDX.hour - 6) / 24 * 2 * np.pi) + rng.normal(0, 2, len(_IDX))


def _mass_dominated_energy(seed=1):
    """Hourly energy from a known 2R2C model with a large mass time-constant + noise."""
    truth = RC2Model(ua_env=0.9, uc_mass=1.2, gain_eff=2.0, tau_air=4.0, tau_mass=90.0, w=0.7)
    rng = np.random.default_rng(seed)
    y = truth.predict(_oat(), daily_schedule(_IDX)) + rng.normal(0, 0.05, len(_IDX))
    return truth, np.maximum(y, 0.0)


# --------------------------------------------------------------------------- 2R2C


def test_rc2_predict_nonnegative_and_zero_off_load():
    m = RC2Model(ua_env=0.8, uc_mass=1.0, gain_eff=3.0, tau_air=4.0, tau_mass=60.0, w=0.6)
    e = m.predict(_oat(), daily_schedule(_IDX))
    assert (e >= 0).all()
    warm = m.predict(np.full(len(_IDX), 80.0), daily_schedule(_IDX))
    assert warm.sum() == 0.0  # big gain, warm outdoor -> no heating


def test_calibrate2_recovers_known_params_and_accepts():
    truth, y = _mass_dominated_energy()
    cal = calibrate2(_oat(), daily_schedule(_IDX), y)
    assert cal.accept and cal.fit.cv_rmse < 0.03
    m = cal.model
    assert abs(m.ua_env - truth.ua_env) < 0.15
    assert abs(m.uc_mass - truth.uc_mass) < 0.25
    assert abs(m.tau_mass - truth.tau_mass) < 25.0  # slow constant recovered to the right ballpark


def test_2r2c_beats_1r1c_on_mass_dominated():
    """The feature earns its complexity: the mass node fits a tail one tau cannot."""
    _, y = _mass_dominated_energy()
    cal2 = calibrate2(_oat(), daily_schedule(_IDX), y)
    cal1 = calibrate(_oat(), daily_schedule(_IDX), y)
    assert cal2.fit.cv_rmse < cal1.fit.cv_rmse


def test_calibrate2_p_is_six():
    _, y = _mass_dominated_energy()
    assert calibrate2(_oat(), daily_schedule(_IDX), y).fit.p == 6  # honest G14 param penalty


def test_calibrate2_is_deterministic():
    _, y = _mass_dominated_energy()
    assert check_determinism(
        lambda: calibrate2(_oat(), daily_schedule(_IDX), y).model.as_dict()
    ).deterministic


def test_calibrate2_degrades_on_thin_data_without_raising():
    cal = calibrate2([50.0, 40.0], {"setpoint": [70, 70], "conditioned": [1, 1]}, [5.0, 6.0])
    assert isinstance(cal, Calibration) and not cal.accept  # too few points -> non-accepted


def test_option_d_savings_accepts_rc2model_unchanged():
    _, y = _mass_dominated_energy()
    cal = calibrate2(_oat(), daily_schedule(_IDX), y)
    found = daily_schedule(_IDX, occ_setpoint=72, setback_setpoint=72)  # no setback (wasteful)
    corr = daily_schedule(_IDX, occ_setpoint=70, setback_setpoint=60)  # setback (efficient)
    sv = option_d_savings(cal, _oat(), found, corr)
    assert sv.valid and sv.avoided_energy > 0
    assert sv.frac_savings_uncertainty == sv.frac_savings_uncertainty  # not NaN


# --------------------------------------------------------------------------- multi-zone


def _two_zone_energy(sched_a, sched_b, seed=2):
    ma, mb = RCModel(0.8, 2.0, 20.0), RCModel(1.3, 3.0, 20.0)
    rng = np.random.default_rng(seed)
    y = ma.predict(_oat(), sched_a) + mb.predict(_oat(), sched_b) + rng.normal(0, 0.05, len(_IDX))
    return np.maximum(y, 0.0)


def test_calibrate_zones_stacked_recovers_two_zones_with_differing_schedules():
    sa = daily_schedule(_IDX, occ_start=6, occ_end=16)
    sb = daily_schedule(_IDX, occ_start=10, occ_end=20)  # differing schedules -> identifiable
    cal = calibrate_zones(_oat(), {"A": sa, "B": sb}, _two_zone_energy(sa, sb), order=1)
    assert cal.accept and isinstance(cal.model, MultiZoneModel)
    by = {z.name: z.model for z in cal.model.zones}
    assert abs(by["A"].ua_eff - 0.8) < 0.2 and abs(by["B"].ua_eff - 1.3) < 0.2


def test_multizone_underdetermined_is_candid_but_sum_fits():
    """Identical zone schedules under-determine the split — the sum still fits (honest)."""
    s = daily_schedule(_IDX)
    cal = calibrate_zones(_oat(), {"A": s, "B": s}, _two_zone_energy(s, s), order=1)
    assert cal.accept  # the SUM is fit well
    by = {z.name: z.model for z in cal.model.zones}
    # with identical schedules the per-zone split is not the true (0.8, 1.3); only the total is
    # identifiable — assert the total conductance is right even though the split isn't
    assert abs((by["A"].ua_eff + by["B"].ua_eff) - (0.8 + 1.3)) < 0.3


def test_calibrate_zones_order2_and_deterministic():
    sa = daily_schedule(_IDX, occ_start=6, occ_end=16)
    sb = daily_schedule(_IDX, occ_start=10, occ_end=20)
    y = _two_zone_energy(sa, sb)
    cal = calibrate_zones(_oat(), {"A": sa, "B": sb}, y, order=2)
    assert cal.fit.p == 2 * 3 + 3  # 3 linear/zone + 3 shared nonlinear
    assert check_determinism(
        lambda: calibrate_zones(_oat(), {"A": sa, "B": sb}, y, order=1).fit.cv_rmse
    ).deterministic


def test_calibrate_zones_rejects_bad_order():
    with pytest.raises(ValueError):
        calibrate_zones(_oat(), {"A": daily_schedule(_IDX)}, _oat(), order=3)


def test_multizone_option_d_savings_with_schedule_dict():
    sa = daily_schedule(_IDX, occ_start=6, occ_end=16)
    sb = daily_schedule(_IDX, occ_start=10, occ_end=20)
    cal = calibrate_zones(_oat(), {"A": sa, "B": sb}, _two_zone_energy(sa, sb), order=1)
    warm = daily_schedule(_IDX, setback_setpoint=70)
    found = {"A": warm, "B": warm}
    corr = {"A": sa, "B": sb}
    sv = option_d_savings(cal, _oat(), found, corr)  # reused verbatim with per-zone schedule dicts
    assert sv.valid and sv.avoided_energy > 0


# --------------------------------------------------------------------------- EnergyPlus bridge


def test_energyplus_helpful_error_without_eppy():
    from camber.interop import energyplus

    try:
        import eppy  # noqa: F401

        pytest.skip("eppy installed; the ImportError guard isn't exercised")
    except ImportError:
        pass
    with pytest.raises(ImportError, match=r"camber-toolkit\[energyplus\]"):
        energyplus._require()


def test_compare_option_d_with_injected_runner():
    from camber.interop.energyplus import compare_option_d

    truth, y = _mass_dominated_energy()
    cal = calibrate2(_oat(), daily_schedule(_IDX), y)
    found = daily_schedule(_IDX, occ_setpoint=72, setback_setpoint=72)
    corr = daily_schedule(_IDX, occ_setpoint=70, setback_setpoint=60)
    # a fake E+ runner that agrees ~4% high with the grey box (no engine involved)
    result = compare_option_d(
        "b.idf",
        "w.epw",
        _oat(),
        found,
        corr,
        cal,
        runner=lambda idf, epw, sched: truth.predict(_oat(), sched) * 1.04,
    )
    assert result["agreement"]["within_tol"] is True
    assert result["agreement"]["both_save"] is True
    assert result["camber"]["valid"] is True  # the grey-box sub-dict is the real option_d result


def test_compare_option_d_flags_disagreement_and_invalid_greybox():
    from camber.interop.energyplus import compare_option_d

    # a grey box that failed G14 (noise-only energy) -> no saving claimed -> agreement is honest
    noise = np.abs(np.random.default_rng(9).normal(5, 5, len(_IDX)))
    bad = calibrate2(_oat(), daily_schedule(_IDX), noise)
    found = daily_schedule(_IDX, setback_setpoint=70)
    corr = daily_schedule(_IDX)
    result = compare_option_d(
        "b.idf", "w.epw", _oat(), found, corr, bad, runner=lambda i, e, s: np.full(len(_IDX), 1.0)
    )
    assert result["agreement"]["grey_box_valid"] is False
    assert result["agreement"]["both_save"] is False
