"""A run status resampled above the stored interval (0.104, #143).

A resampled 0/1 run status holds each bin's duty (the share of the bin it was on), at ingest, on
the folder path and at a run's read-time resample alike. The start-counting rules count the
fewest starts consistent with those duties (so a boiler firing a quarter of every hour is not read
as never firing), and on a trend coarser than ``max_interval_min`` they caveat the count as a
floor, declining a below-limit verdict when part-on samples show the interval hid starts.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.boilercycle import _fewest_starts, analyze_boiler_cycling  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.plant import analyze_hw_plant  # noqa: E402
from camber.resolve import clear_store_cache, discover_store, resolve  # noqa: E402
from camber.rules.boilercycle_rule import BoilerShortCycle  # noqa: E402
from camber.rules.chillerstaging_rule import ChillerStaging  # noqa: E402
from camber.rules.compressor_cycle_rule import CompressorShortCycle  # noqa: E402
from camber.schedules import fan_on_mask  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

FID = "ds-status"


def _q_idx(days=14):
    return pd.date_range("2025-01-06", periods=days * 96, freq="15min")  # Monday, winter


def _cycling(idx, *, hours=range(24)):
    """Fires the first quarter-hour of every hour in ``hours``: one start an hour."""
    in_hours = np.isin(idx.hour, list(hours))
    return np.where(in_hours & (idx.minute == 0), 1.0, 0.0)


def _plant_store(tmp_path, status, *, extra=None):
    idx = _q_idx()
    frame = pd.DataFrame(
        {Role.BOILER_STATUS: status, Role.HW_SUPPLY_TEMP: 160.0, **(extra or {})}, index=idx
    )
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(frame, facility_id=FID, equip="PLANT_1", equip_class="HW_PLANT")
    clear_store_cache()
    return st, frame


def _ref(st):
    return discover_store(st, FID, "HW_PLANT", marker_role=Role.HW_SUPPLY_TEMP)[0]


# --------------------------------------------------------------------------- read-time resample


def test_store_resample_keeps_duty_and_on_windows_stay_on(tmp_path):
    idx = _q_idx()
    warm = np.where((idx.hour == 5) & (idx.minute == 45), 1.0, 0.0)
    st, frame = _plant_store(tmp_path, _cycling(idx), extra={Role.WARMUP: warm})
    hourly = st.read_role_frame(facility_id=FID, equip="PLANT_1", resample="1h")
    duty = hourly[Role.BOILER_STATUS]
    # the duty is the share of the hour firing: a quarter, every hour
    assert duty.round(6).eq(0.25).all()
    # any-on is duty > 0: every hour the boiler fired in reads on, none is lost
    fired = frame[Role.BOILER_STATUS].resample("1h").max() > 0
    assert ((duty > 0) == fired).all()
    # the warm-up exclusion flag takes the max, as at ingest and in resolve
    assert hourly[Role.WARMUP].groupby(hourly.index.hour).max()[5] == 1.0
    # resolve's store path agrees with the store's own resample
    via = resolve(_ref(st), None, (Role.BOILER_STATUS, Role.WARMUP), resample="1h")
    pd.testing.assert_series_equal(
        via[Role.BOILER_STATUS], duty, check_names=False, check_freq=False
    )
    pd.testing.assert_series_equal(
        via[Role.WARMUP], hourly[Role.WARMUP], check_names=False, check_freq=False
    )


def test_no_resample_reads_the_stored_grid_unchanged(tmp_path):
    idx = _q_idx()
    st, frame = _plant_store(tmp_path, _cycling(idx))
    stored = st.read_role_frame(facility_id=FID, equip="PLANT_1")
    pd.testing.assert_series_equal(
        stored[Role.BOILER_STATUS], frame[Role.BOILER_STATUS], check_names=False, check_freq=False
    )
    f = BoilerShortCycle().analyze(
        "PLANT_1", resolve(_ref(st), None, (Role.BOILER_STATUS,), resample="15min")
    )
    assert f.severity == "fault" and f.metrics["n_starts"] == 14 * 24
    assert f.metrics["interval_min"] == 15.0 and f.metrics["n_partial"] == 0
    assert f.caveats == []


# --------------------------------------------------------------------------- the start count


def test_fewest_starts_is_the_plain_count_on_a_0_1_trend():
    rng = np.random.default_rng(143)
    for _ in range(100):
        a = rng.integers(0, 2, 60).astype(float)
        run = pd.Series(a > 0.5)
        plain = (run & ~run.shift(1, fill_value=False)).to_numpy()
        assert (_fewest_starts(a) == plain).all()


@pytest.mark.parametrize(
    "duty, starts",
    [
        ([0.25, 0.0, 0.25, 0.0], 2),  # two separate bursts
        ([0.0, 0.5, 1.0, 1.0, 0.5, 0.0], 1),  # one long run with part-on edges
        ([1.0, 0.5, 1.0], 2),  # a run, a stop and restart hidden inside the middle bin
        ([0.0, 0.3, 0.3, 0.3, 0.0], 2),  # on, off, on again across three part-on bins
        ([0.0, 0.0], 0),
    ],
)
def test_fewest_starts_on_duties(duty, starts):
    assert int(_fewest_starts(np.array(duty)).sum()) == starts


def test_runtime_is_the_mean_duty():
    idx = pd.date_range("2025-01-06", periods=48, freq="1h")
    df = pd.DataFrame({"BoilerStatus": 0.25}, index=idx)
    r = analyze_boiler_cycling(df, "B")
    assert r.runtime_pct == 25.0  # not 0 % from a 0.5 threshold
    assert r.n_partial == 48 and r.interval_min == 60.0


# --------------------------------------------------------------------------- the rules at 1 h


def test_short_cycling_boiler_at_one_hour_is_never_a_clean_ok(tmp_path):
    idx = _q_idx()
    st, _ = _plant_store(tmp_path, _cycling(idx))
    hourly = resolve(_ref(st), None, (Role.BOILER_STATUS,), resample="1h")
    f = BoilerShortCycle().analyze("PLANT_1", hourly)
    assert f.severity in ("warn", "fault")
    assert f.metrics["starts_per_day"] >= 6.0 and f.metrics["runtime_pct"] == 25.0
    assert f.metrics["interval_min"] == 60.0
    assert any("floor" in c for c in f.caveats)


def test_cycling_below_the_limit_at_one_hour_declines(tmp_path):
    idx = _q_idx()
    # starts twice an hour for four hours a day: 8 starts a day, hidden at 1 h
    hours = (idx.hour >= 6) & (idx.hour < 10)
    status = np.where(hours & (idx.minute % 30 == 0), 1.0, 0.0)
    st, _ = _plant_store(tmp_path, status)
    fine = BoilerShortCycle().analyze(
        "PLANT_1", resolve(_ref(st), None, (Role.BOILER_STATUS,), resample="15min")
    )
    assert fine.severity == "warn" and fine.metrics["n_starts"] == 14 * 8
    f = BoilerShortCycle().analyze(
        "PLANT_1", resolve(_ref(st), None, (Role.BOILER_STATUS,), resample="1h")
    )
    assert f.severity == "info"
    assert f.metrics["starts_per_day"] < 6.0 and f.metrics["n_partial"] > 0
    assert "cannot confirm" in f.summary and any("hid starts" in c for c in f.caveats)
    # a looser resolution limit takes the hourly count at its word
    loose = BoilerShortCycle(max_interval_min=60.0).analyze(
        "PLANT_1", resolve(_ref(st), None, (Role.BOILER_STATUS,), resample="1h")
    )
    assert loose.severity == "ok" and loose.caveats == []


def test_steady_boiler_at_one_hour_stays_ok_with_a_caveat():
    idx = pd.date_range("2025-01-06", periods=24 * 14, freq="1h")
    f = BoilerShortCycle().analyze("B", pd.DataFrame({Role.BOILER_STATUS: 1.0}, index=idx))
    assert f.severity == "ok" and f.metrics["n_partial"] == 0
    assert len(f.caveats) == 1 and "floor" in f.caveats[0]


def test_compressor_declines_the_same_way():
    idx = _q_idx()
    status = np.where((idx.hour == 9) & (idx.minute % 30 == 0), 1.0, 0.0)  # 2 starts in one hour
    frame = pd.DataFrame({Role.COMPRESSOR_STATUS: status}, index=idx)
    hourly = frame.resample("1h").mean()
    f = CompressorShortCycle(max_starts_per_day=2.0).analyze("RTU", frame)
    assert f.severity == "warn" and f.caveats == []
    g = CompressorShortCycle(max_starts_per_day=2.0).analyze("RTU", hourly)
    assert g.severity == "info" and "compressor" in g.caveats[0]


def test_chiller_staging_caveats_a_coarse_count():
    idx = pd.date_range("2025-07-07", periods=24 * 14, freq="1h")
    frame = pd.DataFrame({Role.POWER: 150.0}, index=idx)
    f = ChillerStaging().analyze("CH", frame)
    assert f.severity == "ok" and len(f.caveats) == 1 and "averaged power" in f.caveats[0]
    fine = frame.resample("15min").ffill()
    assert ChillerStaging().analyze("CH", fine).caveats == []


# --------------------------------------------------------------------------- run-time shares


def test_summer_firing_share_weights_each_hour_by_its_duty():
    idx = pd.date_range("2025-07-07", periods=24 * 14, freq="1h")  # weekday hours, OAT 85 F
    df = pd.DataFrame({"BoilerStatus": 0.25, "OAT": 85.0}, index=idx)
    r = analyze_hw_plant(df, "PLANT")
    assert r.summer_run_pct == 100.0  # a quarter of every hot hour firing is still firing
    assert r.boiler_running_pct == 25.0
    plain = analyze_hw_plant(df.assign(BoilerStatus=0.0), "PLANT")
    assert plain.summer_run_pct == 0.0


# --------------------------------------------------------------------------- the fan gate


def test_fan_gate_on_resampled_status_is_majority_on():
    idx = pd.date_range("2025-07-07 06:00", periods=8, freq="15min")
    # 06:00-07:00 on for 45 min, 07:00-08:00 on for 15 min
    status = pd.Series([1.0, 1.0, 1.0, 0.0, 1.0, 0.0, 0.0, 0.0], index=idx)
    hourly = pd.DataFrame({Role.SUPPLY_FAN_STATUS: status.resample("1h").mean()})
    m, src = fan_on_mask(hourly)
    assert src == "fan status"
    assert m.tolist() == [True, False]  # mostly on reads on; mostly off reads off
    fine, _ = fan_on_mask(pd.DataFrame({Role.SUPPLY_FAN_STATUS: status}))
    assert fine.sum() == 4  # unchanged on the stored grid
