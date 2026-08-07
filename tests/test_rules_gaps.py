"""Coverage-lifting tests for rule-wrapper branches the aggregate suites skip.

Each rule below has an unexercised middle severity tier (warn), a caveat path, an
alternate running-signal branch, or an ``evidence()`` renderer. Frames are Role-keyed
(``pd.DataFrame({Role.X: series}, index=...)``) exactly as the migrated-rule suites build
them; the underlying analyzer thresholds are read from source to land in the target band.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.boilercycle_rule import BoilerShortCycle  # noqa: E402
from camber.rules.chiller_approach_rule import ChillerApproachFouling  # noqa: E402
from camber.rules.chwplant_rule import CHWPlantReset  # noqa: E402
from camber.rules.compressor_cycle_rule import CompressorShortCycle  # noqa: E402
from camber.rules.compressor_stage_rule import CompressorStaging  # noqa: E402
from camber.rules.heatpump_rule import HeatPumpDefrost  # noqa: E402
from camber.rules.hwplant_deltat_rule import HWPlantDeltaT  # noqa: E402
from camber.rules.iaq_rule import CO2Ventilation  # noqa: E402
from camber.rules.overcooling_rule import OvercoolingMinFlow  # noqa: E402
from camber.rules.satcontrol_rule import SupplyAirControl  # noqa: E402
from camber.rules.satreset_rule import SupplyAirReset  # noqa: E402
from camber.rules.setback_rule import NightWeekendSetback  # noqa: E402
from camber.rules.static_rule import DamperCensus  # noqa: E402


def _days2_hourly():
    """49 hourly points spanning exactly 2 days (so starts/day = pulses / 2)."""
    return pd.date_range("2025-01-06", periods=49, freq="1h")


def _pulses(idx, n_pulses):
    """A 0/1 series with ``n_pulses`` isolated single-sample 'on' spikes.

    Each spike is one off->on transition (one start) and one 0->1->0 pair (two level
    changes) — lets the cycling-rate rules land in a chosen band.
    """
    x = np.zeros(len(idx))
    step = len(idx) // (n_pulses + 1)
    for k in range(1, n_pulses + 1):
        x[k * step] = 1.0
    return pd.Series(x, index=idx)


# --------------------------------------------------------------------------- cycling warn tiers


def test_boiler_short_cycle_warn_tier():
    idx = _days2_hourly()
    frame = pd.DataFrame({Role.BOILER_STATUS: _pulses(idx, 8)}, index=idx)  # 4 starts/day
    f = BoilerShortCycle(max_starts_per_day=3.0).analyze("BLR-1", frame)
    assert f.severity == "warn"


def test_compressor_short_cycle_warn_tier():
    idx = _days2_hourly()
    frame = pd.DataFrame({Role.COMPRESSOR_STATUS: _pulses(idx, 8)}, index=idx)  # 4 starts/day
    f = CompressorShortCycle(max_starts_per_day=3.0).analyze("DX-1", frame)
    assert f.severity == "warn"


def test_compressor_stage_warn_tier():
    idx = _days2_hourly()
    # 4 pulses -> 8 level changes over 2 days -> 4 changes/day
    frame = pd.DataFrame({Role.COMPRESSOR_STAGE: _pulses(idx, 4)}, index=idx)
    f = CompressorStaging(max_changes_per_day=3.0).analyze("DX-1", frame)
    assert f.severity == "warn"


def test_heatpump_defrost_warn_tier():
    idx = _days2_hourly()
    frame = pd.DataFrame({Role.REVERSING_VALVE_CMD: _pulses(idx, 4)}, index=idx)  # 4/day
    f = HeatPumpDefrost(max_reversals_per_day=3.0).analyze("HP-1", frame)
    assert f.severity == "warn"


# --------------------------------------------------------------------------- approach / iaq warn


def test_chiller_approach_warn_tier():
    idx = pd.date_range("2025-07-07", periods=100, freq="1h")
    # median 8F vs design 5F -> ratio 1.6 -> warn (1.5 <= r < 2.0)
    frame = pd.DataFrame({Role.COND_APPROACH_TEMP: np.full(len(idx), 8.0)}, index=idx)
    f = ChillerApproachFouling(cond_design_f=5.0).analyze("CH-1", frame)
    assert f.severity == "warn"
    assert f.metrics["worst_approach_ratio"] == 1.6


def test_co2_ventilation_warn_tier():
    idx = pd.date_range("2025-06-02", periods=30 * 24, freq="1h")  # a Monday
    # ~1/11 occupied hours breach the 1120 ppm high threshold -> under_vent ~9% -> warn
    co2 = np.where(idx.hour == 12, 1300.0, 700.0)
    f = CO2Ventilation().analyze("ZONE-1", pd.DataFrame({Role.CO2: co2}, index=idx))
    assert f.severity == "warn"
    assert 5.0 <= f.metrics["under_vent_pct"] < 20.0


# ------------------------------------------------------------------- chwplant caveat + reset


def test_chwplant_reset_flat_warn_and_deltat_caveat():
    idx = pd.date_range("2025-07-07", periods=24 * 14, freq="1h")
    # CHWST flat 44F (in running band) while OAT swings -> reset judged absent -> warn;
    # no CHW return temp -> loop deltaT not evaluated -> the caveat path fires.
    oat = 70.0 + 15.0 * np.sin(np.arange(len(idx)) * 2 * np.pi / 24)
    frame = pd.DataFrame({Role.CHW_SUPPLY_TEMP: np.full(len(idx), 44.0), Role.OAT: oat}, index=idx)
    f = CHWPlantReset().analyze("CHW", frame)
    assert f.severity == "warn"
    assert any("deltaT not evaluated" in c for c in f.caveats)


# --------------------------------------------------------------------------- setback warn


def test_setback_missing_warn_tier():
    idx = pd.date_range("2025-07-07", periods=24 * 14, freq="1h")
    # fan on ~40% of *all* hours, uniformly -> occupied run == unoccupied run (no setback)
    # and unoccupied run 40% < 50% -> warn (not the >=50% fault tier).
    status = (np.arange(len(idx)) % 5 < 2).astype(float)
    frame = pd.DataFrame({Role.SUPPLY_FAN_STATUS: status}, index=idx)
    f = NightWeekendSetback().analyze("AHU-1", frame)
    assert f.severity == "warn"


# --------------------------------------------------------------------------- static fleet warn + ok


def _damper_frames(medians):
    idx = pd.date_range("2025-07-07", periods=24 * 5, freq="1h")  # spans occupied hours
    return {
        f"VAV-{i}": pd.DataFrame({Role.DAMPER: np.full(len(idx), m)}, index=idx)
        for i, m in enumerate(medians)
    }


def test_damper_census_fleet_ok_and_warn():
    ok = DamperCensus().analyze_fleet(_damper_frames([70.0] * 5))  # all mid-band
    assert ok.severity == "ok"
    # 5 low (30%) + 2 high (95%) + 3 mid: low=50%, high=20%, in-band=30% -> warn
    warn = DamperCensus().analyze_fleet(_damper_frames([30.0] * 5 + [95.0] * 2 + [70.0] * 3))
    assert warn.severity == "warn"


# --------------------------------------------------------------------------- overcooling warn


def test_overcooling_fault_capped_to_warn_without_damper():
    idx = pd.date_range("2025-07-07", periods=24 * 20, freq="1h")
    # space below cooling setpoint ~3/11 occupied hours, airflow always at min ->
    # overcool_at_minflow ~27% (fault tier). With no damper column the count is
    # unconfirmed, so the rule caps fault -> warn and caveats it.
    space = np.where((idx.hour >= 11) & (idx.hour <= 13), 71.0, 76.0)
    frame = pd.DataFrame(
        {
            Role.SPACE_TEMP: space,
            Role.COOL_SP: np.full(len(idx), 74.0),
            Role.AIRFLOW: np.full(len(idx), 100.0),
            Role.AIRFLOW_SP: np.full(len(idx), 100.0),
        },
        index=idx,
    )
    f = OvercoolingMinFlow().analyze("VAV-1", frame)
    assert f.severity == "warn"
    assert any("damper unavailable" in c for c in f.caveats)


# --------------------------------------------------------------------------- hw-plant deltaT warn


def test_hwplant_deltat_warn_tier():
    idx = pd.date_range("2025-01-06", periods=24 * 14, freq="1h")
    # boiler always running; ~27% of *occupied* hours at deltaT 15F (< 20F design) -> warn
    dt_low = (idx.hour >= 7) & (idx.hour < 10)  # 3 of the 11 occupied hours (7..17)
    hwr = np.where(dt_low, 165.0, 155.0)  # HWS 180 -> deltaT 15 (low) or 25 (ok)
    frame = pd.DataFrame(
        {
            Role.BOILER_STATUS: np.ones(len(idx)),
            Role.HW_SUPPLY_TEMP: np.full(len(idx), 180.0),
            Role.HW_RETURN_TEMP: hwr,
        },
        index=idx,
    )
    f = HWPlantDeltaT().analyze("BLR-1", frame)
    assert f.severity == "warn"
    assert 20.0 <= f.metrics["low_deltaT_pct"] < 50.0


# --------------------------------------------------------------------------- satcontrol / satreset


def test_satcontrol_fan_speed_signal_and_evidence():
    idx = pd.date_range("2025-07-07", periods=200, freq="1h")
    # fan SPEED (not status) is the running signal -> the speed branch of _running_mask
    frame = pd.DataFrame(
        {
            Role.SUPPLY_AIR_TEMP: np.full(len(idx), 60.0),
            Role.SUPPLY_AIR_TEMP_SP: np.full(len(idx), 55.0),  # 5F off -> fault
            Role.SUPPLY_FAN_SPEED: np.full(len(idx), 0.8),
        },
        index=idx,
    )
    rule = SupplyAirControl(tol_F=2.0)
    assert rule.analyze("AHU-1", frame).severity == "fault"
    # evidence on a frame with no fan signal -> _running_mask's all-True fallback
    ev_frame = frame.drop(columns=[Role.SUPPLY_FAN_SPEED])
    ev = rule.evidence("AHU-1", ev_frame)
    assert ev.renderer == "multitrend"


def test_satreset_evidence_with_and_without_oat():
    idx = pd.date_range("2025-07-07", periods=100, freq="1h")
    with_oat = pd.DataFrame(
        {Role.SUPPLY_AIR_TEMP: np.full(len(idx), 55.0), Role.OAT: np.linspace(50, 90, len(idx))},
        index=idx,
    )
    ev = SupplyAirReset().evidence("AHU-1", with_oat)
    assert ev.renderer == "diagnostic"
    no_oat = with_oat.drop(columns=[Role.OAT])
    assert SupplyAirReset().evidence("AHU-1", no_oat) is None
