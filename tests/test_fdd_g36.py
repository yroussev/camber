"""Tests for the G36 §5.16.14 AHU fault-detection engine (clean-room)."""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.fdd_g36 import (  # noqa: E402
    OS_FAULTS,
    OS_FREECOOL,
    OS_HEATING,
    OS_MECH_ECON,
    OS_MECH_MINOA,
    OS_UNCLASSIFIED,
    OS_UNKNOWN,
    classify_os,
    run_g36_afdd,
)

# ---- operating-state classifier ----


def test_os_heating():
    assert classify_os(hc=40, cc=0) == OS_HEATING


def test_os_simultaneous_is_unknown():
    # both valves open -> OS#5 (the simultaneous heat/cool signature)
    assert classify_os(hc=30, cc=50) == OS_UNKNOWN


def test_os_free_cooling():
    assert classify_os(hc=0, cc=0) == OS_FREECOOL


def test_os_mech_econ_vs_minoa():
    assert classify_os(hc=0, cc=60, oa_damper=100) == OS_MECH_ECON
    assert classify_os(hc=0, cc=60, oa_damper=10) == OS_MECH_MINOA


def test_os_fault_map_matches_g36():
    # spot-check the OS->FC applicability (G36 5.16.14.9)
    assert set(OS_FAULTS[OS_HEATING]) == {1, 2, 3, 4, 5, 6, 7, 14}
    assert 8 in OS_FAULTS[OS_FREECOOL] and 9 in OS_FAULTS[OS_FREECOOL]
    assert 13 in OS_FAULTS[OS_MECH_ECON]
    assert 4 in OS_FAULTS[OS_UNKNOWN]  # instability checked in every state


# ---- fault conditions on synthetic AHUs ----


def _frame(n=200, **cols):
    cols.setdefault("FS", 100.0)  # a running AHU (the engine gates on the supply fan)
    idx = pd.date_range("2025-07-07", periods=n, freq="1h")
    return pd.DataFrame({c: np.full(n, v) for c, v in cols.items()}, index=idx)


def test_fc7_sat_too_low_in_full_heating():
    # heating valve full open but SAT well below setpoint -> FC7 fault
    df = _frame(HC=100, CC=0, SAT=80, SATSP=95, MAT=78, RAT=72, OAT=60)
    r = run_g36_afdd(df, "AHU_1")
    assert r.fault_pct[7] > 95  # FC7 trips
    assert r.os_distribution[OS_HEATING] == len(df)


def test_fc13_sat_too_high_in_full_cooling():
    # cooling valve full open, economizer damper open, SAT above setpoint -> FC13
    df = _frame(HC=0, CC=100, SAT=70, SATSP=55, MAT=78, RAT=74, OAT=72, OA_Damper=100)
    r = run_g36_afdd(df, "AHU_2")
    assert r.os_distribution[OS_MECH_ECON] == len(df)
    assert r.fault_pct[13] > 95


def test_fc15_heating_coil_leak():
    # OS free-cooling (both valves shut) but air RISES across the heating coil
    # (HCLT >> HCET) -> leaking/stuck heating valve, FC15
    df = _frame(HC=0, CC=0, SAT=70, MAT=71, RAT=73, OAT=68, HCET=70, HCLT=80)
    r = run_g36_afdd(df, "AHU_3")
    assert r.os_distribution[OS_FREECOOL] == len(df)
    assert r.fault_pct[15] > 95


def test_no_fault_when_healthy():
    # healthy full-heating AHU: SAT meets setpoint, MAT between OAT/RAT
    df = _frame(HC=100, CC=0, SAT=95, SATSP=95, MAT=68, RAT=72, OAT=55, CCET=68, CCLT=68)
    r = run_g36_afdd(df, "AHU_4")
    assert r.fault_pct[7] == 0.0  # SAT meets setpoint -> no FC7
    assert (r.fault_pct[5] or 0) == 0.0  # SAT above MAT -> no FC5


def test_fault_only_evaluated_in_applicable_os():
    # in OS#1 (heating), cooling-side faults like FC13 are not applicable -> None
    df = _frame(HC=100, CC=0, SAT=95, SATSP=95, MAT=68, RAT=72, OAT=55)
    r = run_g36_afdd(df, "AHU_5")
    assert r.fault_pct[13] is None  # FC13 not evaluated in OS#1
    assert r.fault_n_applicable[13] == 0


# ---- opt-in single-signal comparability mode ----


def test_comparability_off_by_default():
    # default run: no single-signal output, and as_dict is unchanged (no extra keys)
    df = _frame(HC=100, CC=0, SAT=80, SATSP=95, MAT=78, RAT=72, OAT=60)
    r = run_g36_afdd(df, "AHU_1")
    assert r.fault_pct_singlesignal is None
    assert not any(k.endswith("_singlesignal") for k in r.as_dict())


def test_comparability_same_fires_different_denominator():
    # FC10 (OAT/MAT mismatch) is applicable only in OS#3 (mechanical + economizer).
    # Build cooling rows split between economizer (OS#3) and min-OA (OS#4); make the
    # MAT/OAT mismatch occur ONLY in the economizer rows. The fault FIRES in the same
    # rows under both denominators, but:
    #   - operating-state gating scores it over OS#3 rows only            -> 100%
    #   - single-signal gating scores it over all input-valid rows        ->  50%
    import numpy as np
    import pandas as pd

    n = 100
    idx = pd.date_range("2025-07-07", periods=n, freq="1h")
    econ = np.arange(n) < 50
    df = pd.DataFrame(
        {
            "HC": np.zeros(n),
            "CC": np.full(n, 60.0),  # cooling on
            "OA_Damper": np.where(econ, 100.0, 10.0),  # econ -> OS#3, else OS#4
            "MAT": np.where(econ, 80.0, 60.0),  # mismatch only in econ rows
            "OAT": np.full(n, 60.0),
            "FS": np.full(n, 60.0),  # fan running
        },
        index=idx,
    )

    r = run_g36_afdd(df, "AHU", comparability=True)
    # OS-gated: denominator = 50 econ rows, all fire -> 100%
    assert r.fault_pct[10] == 100.0
    assert r.fault_n_applicable[10] == 50
    # single-signal: denominator = all 100 input-valid rows, 50 fire -> 50%
    assert r.fault_pct_singlesignal[10] == 50.0
    # narrower (operating-state) denominator => >= single-signal magnitude
    assert r.fault_pct[10] >= r.fault_pct_singlesignal[10]
    # as_dict surfaces the comparability keys only in this mode
    assert r.as_dict()["FC10_pct_singlesignal"] == 50.0


def test_comparability_unrunnable_fc_singlesignal_is_none():
    # FC1 needs DSP/DSPSP/FS, absent here. Under OS gating FC1 is *applicable*
    # (it lists in every operating state) but cannot fire without inputs -> 0.0%.
    # Under single-signal gating there are zero input-valid rows -> None (truly
    # unrunnable). The two denominators legitimately disagree on "applicable".
    df = _frame(HC=0, CC=60, MAT=80, OAT=60, OA_Damper=100)
    r = run_g36_afdd(df, "AHU", comparability=True)
    assert r.fault_pct[1] == 0.0
    assert r.fault_pct_singlesignal[1] is None


def test_comparability_does_not_change_default_fault_pct():
    df = _frame(HC=0, CC=60, MAT=80, OAT=60, OA_Damper=100, SAT=70)
    base = run_g36_afdd(df, "AHU")
    comp = run_g36_afdd(df, "AHU", comparability=True)
    assert comp.fault_pct == base.fault_pct  # default output identical
    assert comp.fault_n_applicable == base.fault_n_applicable


# ---- real-data regressions (0.82.0) ----


def test_nan_valves_are_unclassified_not_free_cooling():
    # real case: logger-dropout rows (valves NaN) were classified OS#2 free cooling, diluting FC8
    # from 25.7 % to 15.0 % on a real AHU. A NaN valve is an interval nobody observed.
    from camber.fdd_g36 import OS_UNCLASSIFIED

    good = _frame(n=100, HC=0, CC=0, SAT=66, MAT=55, RAT=72, OAT=50)  # SAT >> MAT: FC8 trips
    blind = _frame(n=100, HC=np.nan, CC=np.nan, SAT=np.nan, MAT=np.nan, RAT=np.nan, OAT=np.nan)
    blind.index = blind.index + pd.Timedelta(days=30)
    r = run_g36_afdd(pd.concat([good, blind]), "AHU")
    assert r.os_distribution[OS_FREECOOL] == 100
    assert r.n_unclassified == 100 and r.as_dict()["n_unclassified"] == 100
    assert r.fault_n_applicable[8] == 100
    assert r.fault_pct[8] == run_g36_afdd(good, "AHU").fault_pct[8] > 95
    assert classify_os(hc=float("nan"), cc=0) == OS_UNCLASSIFIED
    assert classify_os(hc=None, cc=None) == OS_UNCLASSIFIED


def test_unsorted_and_duplicate_timestamps_do_not_crash():
    # rolling("60min") raised "index must be monotonic" on an unsorted export
    df = _frame(n=48, HC=100, CC=0, SAT=80, SATSP=95, MAT=78, RAT=72, OAT=60)
    messy = pd.concat([df.iloc[::-1], df.iloc[:3]])  # reversed + duplicated rows
    r = run_g36_afdd(messy, "AHU")
    assert r.n_intervals == 48
    assert r.fault_pct == run_g36_afdd(df, "AHU").fault_pct
    import pytest

    as_text = run_g36_afdd(messy.set_axis(messy.index.astype(str)), "AHU")
    assert as_text.fault_pct == r.fault_pct
    with pytest.raises(TypeError, match="DatetimeIndex"):
        run_g36_afdd(df.reset_index(drop=True), "AHU")


# ---- 0.91 (#60): unit-running gate, cooling-only AHUs, G36 ModeDelay / AlarmDelay ----


def _day_frame(days=3, freq="5min", **cols):
    """A 5-minute AHU frame whose fan runs 06:00-18:00; the columns may be arrays or scalars."""
    idx = pd.date_range("2026-07-06", periods=int(days * 24 * 60 / int(freq[:-3])), freq=freq)
    on = (idx.hour >= 6) & (idx.hour < 18)
    data = {c: (v(idx, on) if callable(v) else v) for c, v in cols.items()}
    return pd.DataFrame(data, index=idx), on


def test_fan_off_intervals_are_not_evaluated():
    # fan off: duct air warms to 80F with both valves shut -> used to read as OS#2 and trip FC8/FC9
    df, on = _day_frame(
        HC=0.0,
        CC=lambda i, on: np.where(on, 40.0, 0.0),
        SAT=lambda i, on: np.where(on, 55.0, 80.0),
        MAT=lambda i, on: np.where(on, 70.0, 72.0),
        RAT=74.0,
        OAT=lambda i, on: np.where(on, 80.0, 70.0),
        SATSP=55.0,
        FS=lambda i, on: np.where(on, 60.0, 0.0),
    )
    r = run_g36_afdd(df, "AHU", keep_masks=True)
    assert r.fan_gate == "fan speed proxy"
    assert r.n_fan_off == int((~on).sum())
    assert r.os_distribution[OS_FREECOOL] == 0  # no fan-off row is classed as free cooling
    assert r.fault_pct[8] is None and r.fault_pct[9] is None  # OS#2 never occurred with fan on
    assert not r.masks.loc[~on, [f"FC{fc}" for fc in range(1, 16)]].any().any()
    # fan status outranks speed, which outranks airflow
    df["FAN_STATUS"] = on.astype(float)
    assert run_g36_afdd(df, "AHU").fan_gate == "fan status"
    only_flow = df.drop(columns=["FAN_STATUS", "FS"]).assign(AIRFLOW=np.where(on, 9000.0, 0.0))
    assert run_g36_afdd(only_flow, "AHU").fan_gate == "airflow proxy"


def test_no_fan_signal_declines_unless_the_caller_opts_out():
    df = _frame(HC=0, CC=0, SAT=66, MAT=55, RAT=72, OAT=50).drop(columns=["FS"])
    r = run_g36_afdd(df, "AHU")
    assert r.declined and "supply-fan" in r.declined
    assert all(v is None for v in r.fault_pct.values())
    assert any("not evaluated" in c for c in r.caveats)
    assert r.as_dict()["declined"] == r.declined
    forced = run_g36_afdd(df, "AHU", fan_gate="none")
    assert forced.declined is None and forced.fault_pct[8] > 95
    assert forced.fan_gate.startswith("ungated") and any("fan gate" in c for c in forced.caveats)
    import pytest

    with pytest.raises(ValueError, match="fan_gate"):
        run_g36_afdd(df, "AHU", fan_gate="off")


def test_cooling_only_ahu_runs_with_heating_valve_zero():
    # no HC column: an AHU without a heating coil, not an unrunnable frame
    df = _frame(CC=100, SAT=70, SATSP=55, MAT=78, RAT=74, OAT=72, OA_Damper=100)
    r = run_g36_afdd(df, "AHU")
    assert r is not None and r.declined is None
    assert r.fault_pct[13] > 95  # FC13 still scored
    assert r.fault_pct[7] is None and r.fault_pct[15] is None
    assert r.omitted == {7: "no heating coil", 15: "no heating coil"}
    assert any("without a heating coil" in c for c in r.caveats)
    # heating-only: the cooling-coil tests are omitted instead
    h = run_g36_afdd(_frame(HC=100, SAT=80, SATSP=95, MAT=78, RAT=72, OAT=60), "AHU")
    assert h.fault_pct[7] > 95 and h.fault_pct[13] is None and 14 in h.omitted
    # neither valve: nothing to classify the operating state from
    none = run_g36_afdd(_frame(SAT=70, MAT=72), "AHU")
    assert none.declined and "valve" in none.declined


def test_mode_delay_suppresses_the_morning_static_ramp():
    # the fan starts at full speed and static takes 40 min to reach setpoint every morning
    def mins(i, on):
        return np.where(on, (i.hour - 6) * 60 + i.minute, 0)

    df, on = _day_frame(
        days=5,
        HC=0.0,
        CC=20.0,
        SAT=55.0,
        SATSP=55.0,
        MAT=65.0,
        RAT=72.0,
        OAT=75.0,
        FS=lambda i, on: np.where(on, np.where(mins(i, on) < 40, 100.0, 60.0), 0.0),
        DSP=lambda i, on: np.where(on, np.minimum(1.5, 1.5 * mins(i, on) / 40.0), 0.0),
        DSPSP=1.5,
    )
    raw = run_g36_afdd(df, "AHU", mode_delay_min=0, alarm_delay_min=0)
    assert raw.fault_pct[1] > 4  # the ramp alone trips FC1 without the G36 delays
    r = run_g36_afdd(df, "AHU")
    assert r.fault_pct[1] == 0.0
    assert r.n_suspended == 5 * 6  # 30 min of 5-minute rows after each of the 5 starts
    assert r.delays == {"mode_delay_min": 30.0, "alarm_delay_min": 30.0, "avg_window_min": 5.0}


def test_mode_delay_follows_a_zone_group_mode_change():
    df, on = _day_frame(
        days=1,
        HC=0.0,
        CC=20.0,
        SAT=55.0,
        MAT=65.0,
        FS=60.0,  # fan runs all day: no start inside the data
        MODE=lambda i, on: np.where(i.hour >= 7, 1.0, 0.0),  # unoccupied -> occupied at 07:00
    )
    r = run_g36_afdd(df, "AHU", keep_masks=True)
    s = r.masks["suspended"]
    assert r.n_suspended == 6 and s[s].index.min() == pd.Timestamp("2026-07-06 07:00")
    assert not run_g36_afdd(df.drop(columns=["MODE"]), "AHU").n_suspended  # data-start: unknown


def test_alarm_delay_drops_a_single_excursion_and_keeps_a_persistent_fault():
    # OS#2 -> OS#4 at row 12, with one 5-minute SAT excursion right at the change
    idx = pd.date_range("2026-06-01 08:00", periods=24, freq="5min")
    base = dict(HC=0.0, CC=0.0, SAT=55.0, MAT=70.0, RAT=72.0, OAT=60.0, SATSP=55.0, FS=60.0)
    df = pd.DataFrame(base, index=idx).assign(OA_Damper=30.0)
    df.loc[idx[12] :, "CC"] = 100.0
    df.loc[idx[12], "SAT"] = 62.0
    assert run_g36_afdd(df, "AHU", alarm_delay_min=0).fault_pct[13] > 0  # raw equation trips
    assert run_g36_afdd(df, "AHU").fault_pct[13] == 0.0
    # the same excursion held for 45 min is a fault, counted from its first interval
    df.loc[idx[12] : idx[20], "SAT"] = 62.0
    r = run_g36_afdd(df, "AHU", keep_masks=True)
    assert r.fault_hours[13] == 0.75 and r.masks["FC13"].sum() == 9
    # a missing sample breaks the episode: 20 + 25 minutes around a gap are not 50 of fault
    df.loc[idx[12] : idx[21], "SAT"] = 62.0
    assert run_g36_afdd(df, "AHU").fault_hours[13] == round(10 * 5 / 60, 2)
    assert run_g36_afdd(df.drop(index=idx[16]), "AHU").fault_pct[13] == 0.0


def test_rolling_average_smooths_a_one_minute_spike():
    idx = pd.date_range("2026-06-01 08:00", periods=120, freq="1min")
    df = pd.DataFrame(
        dict(HC=0.0, CC=100.0, SAT=55.0, SATSP=55.0, MAT=70.0, FS=60.0, OA_Damper=30.0), index=idx
    )
    df.loc[idx[60], "SAT"] = 62.0  # one 1-minute spike 7F over the setpoint
    kw = dict(alarm_delay_min=0, mode_delay_min=0)
    assert run_g36_afdd(df, "AHU", avg_window_min=0, **kw).fault_pct[13] > 0
    assert run_g36_afdd(df, "AHU", **kw).fault_pct[13] == 0.0  # 5-min mean 56.4F <= SP + 2F


def test_fc14_fan_heat_term_is_configurable():
    from camber.fdd_g36 import G36Thresholds

    # MAT 70 / SAT 66 as coil entering/leaving: a 4F drop is inside the printed +dT_sf form ...
    df = _frame(HC=0, CC=0, MAT=70, SAT=66, CCET=70, CCLT=66)
    assert run_g36_afdd(df, "AHU").fault_pct[14] == 0.0
    # ... but with SAT downstream of the fan the coil drop is 6F, past hypot(5, 2)
    thr = G36Thresholds(fc14_fan_heat=-2.0)
    assert run_g36_afdd(df, "AHU", thr=thr).fault_pct[14] > 95


# ---- 0.98 (#94): free cooling needs the economizer open beyond its minimum ----


def test_os_free_cooling_needs_the_damper_beyond_its_minimum():
    # both coils idle: OS#2 only with the OA damper open past minimum + tolerance (5 points)
    assert classify_os(hc=0, cc=0, oa_damper=60, oa_damper_min=10) == OS_FREECOOL
    assert classify_os(hc=0, cc=0, oa_damper=15, oa_damper_min=10) == OS_UNKNOWN
    assert classify_os(hc=0, cc=0, oa_damper=0) == OS_UNKNOWN  # closed: recirculating
    assert classify_os(hc=0, cc=0, oa_damper=float("nan")) == OS_UNCLASSIFIED
    assert classify_os(hc=0, cc=0) == OS_FREECOOL  # no damper point: the valves-only reading
    assert set(OS_FAULTS[OS_UNKNOWN]) == {1, 2, 3, 4}  # no free-cooling test at minimum OA


def _unocc_recirc_frame():
    """lbnl-sdahu's fault-free pattern: occupied hours cool on mechanical cooling at a 10 %
    minimum or economize at 60 %; unoccupied hours run the fan with the OA damper shut and both
    valves idle on a hot night (OAT 80 F, far above the 55 F setpoint)."""
    idx = pd.date_range("2026-07-06", periods=24 * 7, freq="1h")
    occ = (idx.hour >= 7) & (idx.hour < 19)
    cool = occ & (idx.hour >= 12)
    return pd.DataFrame(
        {
            "FS": 100.0,
            "HC": 0.0,
            "CC": np.where(cool, 60.0, 0.0),
            "OA_Damper": np.where(occ, np.where(cool, 10.0, 60.0), 0.0),
            "OAT": np.where(occ & ~cool, 50.0, 80.0),
            "SATSP": 55.0,
            "SAT": 55.0,
            "MAT": np.where(occ & ~cool, 53.0, 72.0),
            "RAT": 74.0,
        },
        index=idx,
    ), occ


def test_unoccupied_recirculation_is_not_free_cooling():
    df, occ = _unocc_recirc_frame()
    r = run_g36_afdd(df, "AHU", keep_masks=True)
    assert r.oa_damper_min == 10.0 and r.oa_damper_min_source.startswith("learned")
    # the 12 unoccupied hours a day are OS#5: FC9 (OAT too high for free cooling) cannot fire
    assert r.n_idle_at_min_oa == int((~occ).sum())
    assert r.os_distribution[OS_FREECOOL] == int((occ & (df.index.hour < 12)).sum())
    assert r.os_distribution[OS_UNKNOWN] == int((~occ).sum())
    assert r.fault_pct[9] == 0.0
    assert not r.masks.loc[~occ, "FC9_app"].any()
    # the pre-0.98 valves-only reading tripped FC9 on every unoccupied hour
    legacy = run_g36_afdd(df.drop(columns="OA_Damper"), "AHU")
    assert legacy.fault_pct[9] > 50
    assert any("no OA damper point" in c for c in legacy.caveats)


def test_oa_damper_minimum_from_the_caller_and_the_closed_fallback():
    df, occ = _unocc_recirc_frame()
    # a caller minimum of 60 % puts the economizer hours at the minimum too: no OS#2 at all
    r = run_g36_afdd(df, "AHU", oa_damper_min=60.0)
    assert r.oa_damper_min_source == "caller" and r.os_distribution[OS_FREECOOL] == 0
    # no mechanical-cooling hours to learn from: the minimum is taken as closed (0 %)
    no_cool = df.assign(CC=0.0)
    r0 = run_g36_afdd(no_cool, "AHU")
    assert r0.oa_damper_min == 0.0 and r0.oa_damper_min_source.startswith("assumed 0 %")
    assert any("not learnable" in c for c in r0.caveats)
    assert r0.os_distribution[OS_UNKNOWN] == int((~occ).sum())  # the shut damper is still OS#5
    # a missing damper reading with both coils idle is unclassified, not free cooling
    gap = df.copy()
    gap.loc[gap.index[(~occ)][:5], "OA_Damper"] = np.nan
    assert run_g36_afdd(gap, "AHU").n_unclassified == 5


def test_occupied_mask_excludes_unoccupied_hours():
    df, occ = _unocc_recirc_frame()
    r = run_g36_afdd(df, "AHU", occupied=pd.Series(occ, index=df.index))
    assert r.n_unoccupied == int((~occ).sum()) and r.fault_n_applicable[9] > 0
    assert run_g36_afdd(df, "AHU", occupied=occ).n_unoccupied == r.n_unoccupied
    import pytest

    with pytest.raises(ValueError, match="occupied"):
        run_g36_afdd(df, "AHU", occupied=occ[:10])
