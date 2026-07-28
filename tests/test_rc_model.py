"""Tests for the IPMVP Option-D grey-box RC model + calibration + savings
(camber.mandv.rc_model)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.mandv.rc_model import (  # noqa: E402
    Calibration,
    OptionDSavings,
    RCModel,
    calibrate,
    daily_schedule,
    option_d_savings,
)
from camber.validation import check_determinism  # noqa: E402

_IDX = pd.date_range("2024-01-01", periods=24 * 28, freq="1h")  # 4 winter weeks, hourly


def _oat(seed=0):
    rng = np.random.default_rng(seed)
    return 45 + 15 * np.sin((_IDX.hour - 6) / 24 * 2 * np.pi) + rng.normal(0, 2, len(_IDX))


def _sched(**kw):
    return daily_schedule(_IDX, **kw)


# --- forward model ----------------------------------------------------------


def test_predict_is_nonnegative_and_zero_off_load():
    m = RCModel(ua_eff=0.8, gain_eff=3.0, tau=24.0)
    e = m.predict(_oat(), _sched())
    assert (e >= 0).all()
    # in warm outdoor air with a big internal gain, a conditioned hour needs no heating
    warm = m.predict(np.full(len(_IDX), 80.0), _sched())
    assert warm.sum() == 0.0


def test_colder_weather_uses_more_energy():
    m = RCModel(1.0, 0.0, 24.0)
    cold = m.predict(np.full(len(_IDX), 20.0), _sched()).sum()
    mild = m.predict(np.full(len(_IDX), 50.0), _sched()).sum()
    assert cold > mild > 0


def test_more_conductance_more_energy():
    oat, s = _oat(), _sched()
    lo = RCModel(0.5, 0.0, 24.0).predict(oat, s).sum()
    hi = RCModel(1.5, 0.0, 24.0).predict(oat, s).sum()
    assert hi > lo


# --- calibration ------------------------------------------------------------


def test_calibrate_recovers_known_parameters():
    oat = _oat()
    s = _sched(occ_setpoint=70, setback_setpoint=60)
    true = RCModel(ua_eff=0.8, gain_eff=3.0, tau=24.0)
    cal = calibrate(oat, s, true.predict(oat, s))
    assert isinstance(cal, Calibration) and cal.accept
    assert cal.fit.cv_rmse < 0.02
    assert abs(cal.model.ua_eff - 0.8) < 0.05
    assert abs(cal.model.gain_eff - 3.0) < 0.2
    assert abs(cal.model.tau - 24.0) < 3.0


def test_calibration_is_deterministic():
    oat = _oat()
    s = _sched()
    energy = RCModel(0.8, 3.0, 24.0).predict(oat, s)
    d = check_determinism(lambda: calibrate(oat, s, energy).model.as_dict())
    assert d.deterministic


def test_noise_fails_g14_acceptance():
    oat = _oat()
    s = _sched()
    rng = np.random.default_rng(7)
    cal = calibrate(oat, s, rng.normal(50, 50, len(_IDX)))  # unstructured noise
    assert not cal.accept


# --- Option-D savings -------------------------------------------------------


def _found_corrected():
    # as-found: 24/7 hold at 72; as-corrected: add a weekday night/weekend setback to 60
    found = _sched(
        occ_setpoint=72, setback_setpoint=72, occ_start=0, occ_end=24, weekdays_only=False
    )
    corrected = _sched(occ_setpoint=72, setback_setpoint=60, occ_start=7, occ_end=18)
    return found, corrected


def test_option_d_savings_from_setback():
    oat = _oat()
    found, corrected = _found_corrected()
    cal = calibrate(oat, found, RCModel(0.8, 3.0, 24.0).predict(oat, found))
    sv = option_d_savings(cal, oat, found, corrected)
    assert isinstance(sv, OptionDSavings) and sv.valid
    assert sv.avoided_energy > 0 and 0 < sv.fractional_savings < 1
    assert sv.frac_savings_uncertainty == sv.frac_savings_uncertainty  # not NaN
    assert sv.basis == "IPMVP Option D (calibrated simulation)"


def test_savings_matches_direct_profile_difference():
    oat = _oat()
    found, corrected = _found_corrected()
    cal = calibrate(oat, found, RCModel(0.8, 3.0, 24.0).predict(oat, found))
    sv = option_d_savings(cal, oat, found, corrected)
    direct = cal.model.predict(oat, found).sum() - cal.model.predict(oat, corrected).sum()
    assert abs(sv.avoided_energy - direct) < 1e-6


def test_failed_calibration_claims_no_saving():
    oat = _oat()
    found, corrected = _found_corrected()
    rng = np.random.default_rng(3)
    cal = calibrate(oat, found, rng.normal(50, 50, len(_IDX)))
    sv = option_d_savings(cal, oat, found, corrected)
    assert not sv.valid and sv.avoided_energy is None
    assert "no saving claimed" in sv.basis


def test_savings_as_dict_json_friendly():
    import json

    oat = _oat()
    found, corrected = _found_corrected()
    cal = calibrate(oat, found, RCModel(0.8, 3.0, 24.0).predict(oat, found))
    json.dumps(option_d_savings(cal, oat, found, corrected).as_dict())  # must not raise


def test_ecm_modeled_savings_bridges_to_option_d():
    from camber.mandv.ecm_savings import modeled_savings

    oat = _oat()
    found, corrected = _found_corrected()
    cal = calibrate(oat, found, RCModel(0.8, 3.0, 24.0).predict(oat, found))
    sv = modeled_savings(cal, oat, found, corrected)
    assert isinstance(sv, OptionDSavings) and sv.valid
    assert sv.basis == "IPMVP Option D (calibrated simulation)" and sv.avoided_energy > 0
