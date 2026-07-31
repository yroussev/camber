"""Regression guard for the "declare what you couldn't evaluate" convention.

Each of these rules once silently flipped a verdict when an absent OPTIONAL role made a
sub-check impossible: an absent input read as an asserted negative (a False metric, a raised
severity, or a summary asserting something untested). See camber/rules/base.py.

The property under test: dropping the flip-inducing optional role must NOT turn the finding
into a more confident verdict without saying so. Concretely -- it never raises severity to a
higher actionable tier, it never asserts a clean "ok" where it previously found a problem, and
whenever a sub-check was declined the finding records a caveat.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.boiler_rule import BoilerSummerLockout  # noqa: E402
from camber.rules.chwplant_rule import CHWPlantReset  # noqa: E402
from camber.rules.chwpump_rule import CHWPumpDPReset  # noqa: E402
from camber.rules.satcontrol_rule import SupplyAirControl  # noqa: E402
from camber.rules.satreset_rule import SupplyAirReset  # noqa: E402
from camber.rules.unmet_rule import UnmetHours  # noqa: E402

_RANK = {"ok": 0, "info": 0, "warn": 1, "fault": 2}  # actionable tiers are warn/fault


def _hourly(n, start="2025-07-07"):
    return pd.date_range(start, periods=n, freq="1h")


# --------------------------------------------------------------------------- exemplar (the repro)


def _chwplant_frame(with_oat=True):
    n = 300
    idx = _hourly(n)
    rng = np.random.default_rng(0)
    oat = pd.Series(60 + 18 * np.sin(np.arange(n) / 12) + rng.normal(0, 1, n), index=idx)
    chws = 44 + 0.15 * (oat - 60)  # a WORKING reset: CHWST rises materially with OAT
    frame = {Role.CHW_SUPPLY_TEMP: chws, Role.CHW_RETURN_TEMP: chws + 12.0}  # healthy deltaT
    if with_oat:
        frame[Role.OAT] = oat
    return pd.DataFrame(frame, index=idx)


def test_chwplant_reset_without_oat_does_not_flip_to_false_no_reset():
    """The field repro: the reset works, but dropping OAT must not report 'no reset'/warn."""
    with_oat = CHWPlantReset().analyze("CHW", _chwplant_frame(with_oat=True))
    no_oat = CHWPlantReset().analyze("CHW", _chwplant_frame(with_oat=False))

    # with OAT: a real, evaluated, working reset
    assert with_oat.metrics["chwst_reset_present"] is True
    assert with_oat.severity == "ok"
    # without OAT: declined, not a confident negative
    assert no_oat.metrics["chwst_reset_present"] is None  # not False
    assert no_oat.metrics["chwst_slope_per_F"] is None
    assert "no reset" not in no_oat.summary.lower()
    assert no_oat.caveats and any("OAT" in c for c in no_oat.caveats)
    assert _RANK[no_oat.severity] <= _RANK[with_oat.severity]  # never raised by absence
    # the Registry backstop is separate; the rule itself must self-report here


# --------------------------------------------------------------------------- parametrized guard


def _satcontrol_frames():
    # SAT tracks its 55F setpoint while the fan runs (occupied hours) but drifts to 68F when the
    # fan is OFF. WITH fan status those off hours are excluded -> ok. WITHOUT it, the old code
    # assumed all-running and folded the drift into a false warn/fault.
    n = 240
    idx = _hourly(n, "2024-07-01")
    on = (idx.hour >= 7) & (idx.hour <= 18)
    sat = pd.Series(np.where(on, 55.0, 68.0), index=idx)
    full = pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: sat,
            Role.SUPPLY_AIR_TEMP_SP: pd.Series(55.0, index=idx),
            Role.SUPPLY_FAN_STATUS: pd.Series(on.astype(float), index=idx),
        }
    )
    return full, full.drop(columns=[Role.SUPPLY_FAN_STATUS])


def _boiler_frames():
    # boiler runs hot-weather afternoons (a real summer-lockout fault) -- but only OAT proves it.
    n = 24 * 21
    idx = _hourly(n)
    hot = (idx.hour >= 12) & (idx.hour < 17)
    full = pd.DataFrame(
        {
            Role.BOILER_STATUS: pd.Series(np.where(hot, 1.0, 0.0), index=idx),
            Role.HW_SUPPLY_TEMP: pd.Series(150.0, index=idx),
            Role.OAT: pd.Series(np.where(hot, 95.0, 60.0), index=idx),
        }
    )
    return full, full.drop(columns=[Role.OAT])


def _unmet_frames():
    # a genuinely too-COLD zone -- only the heating setpoint can catch it.
    n = 24 * 10
    idx = _hourly(n, "2024-07-01")
    occ = ((idx.hour >= 7) & (idx.hour <= 18)).astype(float)
    st = pd.Series(np.where(occ > 0, 64.0, 71.0), index=idx)  # 64F occupied, below the 68 heat SP
    full = pd.DataFrame(
        {
            Role.SPACE_TEMP: st,
            Role.HEAT_SP: pd.Series(68.0, index=idx),
            Role.COOL_SP: pd.Series(74.0, index=idx),
            Role.OCCUPANCY: pd.Series(occ, index=idx),
        }
    )
    return full, full.drop(columns=[Role.HEAT_SP])


def _chwpump_frames():
    # a healthy modulating pump with a resetting DP setpoint -> ok, reset present.
    n = 24 * 21
    idx = _hourly(n)
    rng = np.random.default_rng(0)
    spd = np.clip(55 + 20 * np.sin(np.arange(n) / 12) + rng.normal(0, 3, n), 20, 85)
    full = pd.DataFrame(
        {
            Role.CHW_PUMP_SPEED: pd.Series(spd, index=idx),
            Role.CHW_DIFF_PRESS_SP: pd.Series(10 + 3 * np.sin(np.arange(n) / 12), index=idx),
        }
    )
    return full, full.drop(columns=[Role.CHW_DIFF_PRESS_SP])


def _satreset_frames():
    # SAT pinned cold with no upward reset -> warn (the cold-dominant check needs no OAT);
    # OAT only decides the reset-direction verdict.
    n = 24 * 30
    idx = _hourly(n)
    rng = np.random.default_rng(0)
    oat = pd.Series(90 + 12 * np.sin(np.arange(n) / 24) + rng.normal(0, 1, n), index=idx)
    sat = pd.Series(55 + rng.normal(0, 0.4, n), index=idx)  # pinned cold, flat
    full = pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat, Role.COOL_VALVE: 60.0, Role.OAT: oat})
    return full, full.drop(columns=[Role.OAT])


def test_satcontrol_without_fan_signal_declines_not_false_fault():
    full, without = _satcontrol_frames()
    fw, fo = SupplyAirControl().analyze("AHU", full), SupplyAirControl().analyze("AHU", without)
    assert fw.severity == "ok"  # tracking during running hours
    assert fo.severity == "info"  # declines rather than false-faulting on off-hour drift
    assert fo.metrics["off_setpoint_pct"] is None
    assert fo.caveats
    assert _RANK[fo.severity] <= _RANK[fw.severity]


def test_boiler_without_oat_declines_not_false_clean():
    full, without = _boiler_frames()
    rule = BoilerSummerLockout(summer_lockout_oat_f=70.0)
    fw, fo = rule.analyze("HWP", full), rule.analyze("HWP", without)
    assert fw.severity == "fault"  # a real summer-lockout violation
    assert fo.severity == "info"  # NOT a confident "ok" -- declined
    assert fo.metrics["summer_run_pct"] is None
    assert fo.caveats


def test_unmet_one_sided_setpoint_declines_not_false_clean():
    full, without = _unmet_frames()
    fw, fo = UnmetHours().analyze("Z", full), UnmetHours().analyze("Z", without)
    assert fw.severity == "fault" and fw.metrics["too_cold_pct"] > 90
    assert fo.severity == "info"  # too-cold direction unevaluated -> declines, not "ok"
    assert fo.metrics["too_cold_pct"] is None
    assert fo.caveats


def test_chwpump_without_dp_setpoint_no_false_flat_claim():
    full, without = _chwpump_frames()
    fw, fo = CHWPumpDPReset().analyze("CHWP", full), CHWPumpDPReset().analyze("CHWP", without)
    assert fw.metrics["dp_sp_reset_present"] is True
    assert fo.metrics["dp_sp_reset_present"] is None  # not a confident "flat DP setpoint"
    assert "flat DP setpoint" not in fo.summary
    assert fo.severity == fw.severity  # severity rides on speed, unchanged
    assert fo.caveats


def test_satreset_without_oat_no_false_load_tracking_verdict():
    full, without = _satreset_frames()
    fw, fo = SupplyAirReset().analyze("AHU", full), SupplyAirReset().analyze("AHU", without)
    assert fo.metrics["slope_per_F"] is None
    assert "not evaluated" in fo.summary.lower()
    assert _RANK[fo.severity] <= _RANK[fw.severity]
    assert fo.caveats


def test_registry_backstop_records_missing_optional():
    """The Registry self-reporting backstop: absent optional roles land on the finding."""
    from camber.rules.base import Finding, _missing_optional, _note_missing_optional

    class _Rule:
        roles_optional = (Role.OAT, Role.OA_DAMPER)

    frame = pd.DataFrame({Role.OAT: [1.0]})  # OA_DAMPER absent
    missing = _missing_optional(_Rule(), frame)
    assert missing == [Role.OA_DAMPER]

    f = Finding(rule="r", equip="E", severity="ok")
    _note_missing_optional(f, missing)
    assert f.metrics["_missing_optional"] == [Role.OA_DAMPER.value]
    # nothing missing -> no key added
    f2 = Finding(rule="r", equip="E", severity="ok")
    _note_missing_optional(f2, [])
    assert "_missing_optional" not in f2.metrics
