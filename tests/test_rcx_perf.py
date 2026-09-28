"""#35: the RCx week scoring and the store's one-equipment read stay fast, and give the same answer.

The week scoring used to reindex every series onto every candidate week's grid (one 23-month sensor
logged in sub-second bursts took minutes); the store opened every part file of a facility for each
equipment it read (a template run over thousands of equipment took over an hour). Both are now
computed once per series / file. These tests pin the speed with generous bounds and pin the answers
against the reference per-window computation, which the fast path must reproduce exactly.
"""

import os
import pickle
import sys
import time

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.report import rcx  # noqa: E402
from camber.report.rcx import _P3_ROLES, select_week  # noqa: E402
from camber.rules.triage import Issue  # noqa: E402
from camber.schedules import effective_occupied_mask, fan_on_mask  # noqa: E402
from camber.store import ParquetStore  # noqa: E402
from camber.store import parquet_store as ps  # noqa: E402

# --------------------------------------------------------------------------- week scoring


def _burst_sensor(months: float, tz="America/Chicago"):
    """An hourly trend logged as three samples 50 ms apart: the modal gap (the week grid's step)
    is 50 ms, so the reference path builds a 12-million-point grid per candidate week."""
    rng = np.random.default_rng(0)
    base = pd.date_range("2022-11-01", periods=int(months * 30.4 * 24), freq="h", tz=tz)
    idx = base.repeat(3) + pd.to_timedelta(np.tile([0, 50, 100], len(base)), unit="ms")
    df = pd.DataFrame(
        {
            Role.OAT: 50 + rng.normal(0, 5, len(idx)),
            Role.SUPPLY_AIR_TEMP: 55 + rng.normal(0, 1, len(idx)),
        },
        index=idx,
    )
    df[Role.SUPPLY_FAN_STATUS] = ((idx.hour >= 6) & (idx.hour < 19)).astype(float)
    return df


def _issues(frame, n=4, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(n):
        m = pd.Series(rng.random(len(frame)) < 0.05 * (i + 1), index=frame.index)
        iss = Issue(key=f"k{i}", root=None, members=[], mask=m, rank=i + 1)
        if i % 2:
            iss.conditional_on = ["x"]
        out.append(iss)
    return out


def test_select_week_on_a_long_sub_second_trend_is_fast():
    fr = {"S1": _burst_sensor(23)}
    iss = _issues(fr["S1"])
    t0 = time.perf_counter()
    for mode in ("evidence", "oat_range", "typical"):
        w = select_week(fr, issues=iss, mode=mode)
        assert not w.declined and len(w.candidates) >= 99
    # ~0.2 s here; the reference path took ~220 s on this input (and 474 s on the real sensor)
    assert time.perf_counter() - t0 < 30


def _frames(seed: int):
    """Mixed inputs: time zones across DST, a jittered second unit, a partial third unit."""
    rng = np.random.default_rng(seed)
    tz = [None, "America/New_York", "Europe/London"][seed % 3]
    freq = ["1h", "15min", "7min", "13min"][seed % 4]
    start = pd.Timestamp("2024-02-20") + pd.Timedelta(minutes=int(rng.integers(0, 60 * 24 * 300)))
    n = int(pd.Timedelta(days=int(rng.integers(20, 80))) / pd.Timedelta(freq))
    idx = pd.date_range(start, periods=n, freq=freq, tz=tz)
    out = {}
    for e in range(3):
        cols = {r: 50 + rng.normal(0, 5, n) for r in _P3_ROLES}
        cols[Role.SUPPLY_FAN_STATUS] = ((idx.hour >= 6) & (idx.hour < 19)).astype(float)
        df = pd.DataFrame(cols, index=idx)
        for r in _P3_ROLES:
            df.loc[rng.random(n) < 0.2, r] = np.nan
        df.iloc[n // 3 : n // 3 + n // 8] = np.nan
        if e == 1:
            j = pd.to_timedelta(rng.integers(0, 3, n) * 30, unit="s")
            df.index = df.index + j
            df = df[~df.index.duplicated()]
        if e == 2:
            df = df[[Role.OAT, Role.DUCT_STATIC]].iloc[n // 4 :]
        out[f"AHU{e}"] = df
    return out


@pytest.mark.parametrize("seed", range(8))
def test_week_stats_match_the_per_window_reference(seed):
    frames = _frames(seed)
    rng = np.random.default_rng(seed)
    gates, occs = {}, {}
    for i, (e, f) in enumerate(frames.items()):
        gates[e] = (
            fan_on_mask(f)[0] if i == 0 else pd.Series(rng.random(len(f)) < 0.6, index=f.index)
        )
        if i == 2:
            gates[e] = None
        occs[e] = effective_occupied_mask(f.index, occ=None)
    lo = min(f.index.min() for f in frames.values())
    hi = max(f.index.max() for f in frames.values())
    starts = _starts(lo, hi)
    step = rcx._grid_step(frames)
    fast = rcx._week_stats(frames, starts, step, _P3_ROLES, gates, occs)
    ref = rcx._week_stats_loop(frames, starts, step, _P3_ROLES, gates, occs)
    assert fast == ref
    assert any(n_all for _ok, n_all, _d in fast)


def _starts(lo, hi):
    s = (lo - pd.Timedelta(days=int(lo.dayofweek))).normalize()
    out = []
    while s <= hi:
        out.append(s)
        s = s + pd.Timedelta(days=7)
    return out


@pytest.mark.parametrize("seed", range(4))
def test_select_week_candidates_are_unchanged_by_the_fast_path(seed, monkeypatch):
    frames = _frames(seed)
    iss = _issues(next(iter(frames.values())))
    got = {m: select_week(frames, issues=iss, mode=m).as_dict() for m in ("evidence", "typical")}
    monkeypatch.setattr(rcx, "_week_stats", rcx._week_stats_loop)
    monkeypatch.setattr(rcx, "_window_positions", lambda *a, **k: None)

    def slow_evidence(issues, starts, span):
        out = []
        for i in issues or ():
            m = rcx._on(i.mask)
            tot = float(m.sum())
            if tot > 0:
                inws = [float(m[(m.index >= st) & (m.index < st + span)].sum()) for st in starts]
                out.append((i, tot, inws))
        return out

    monkeypatch.setattr(rcx, "_evidence_counts", slow_evidence)
    want = {m: select_week(frames, issues=iss, mode=m).as_dict() for m in ("evidence", "typical")}
    assert pickle.dumps(got) == pickle.dumps(want)


def test_mismatched_clocks_fall_back_to_the_reference_path(monkeypatch):
    frames = {"A": _frames(1)["AHU0"]}  # tz-aware
    f = frames["A"]
    gates = {"A": pd.Series(True, index=f.index.tz_localize(None))}  # a naive mask
    occs = {"A": effective_occupied_mask(f.index, occ=None)}
    calls = []
    ref = rcx._week_stats_loop
    monkeypatch.setattr(rcx, "_week_stats_loop", lambda *a: calls.append(1) or ref(*a))
    starts = _starts(f.index.min(), f.index.max())
    step = rcx._grid_step(frames)
    got = rcx._week_stats(frames, starts, step, _P3_ROLES, gates, occs)
    assert calls and got == ref(frames, starts, step, _P3_ROLES, gates, occs)


# --------------------------------------------------------------------------- store reads


def _many_equipment_store(root, n=120):
    st = ParquetStore(str(root))
    idx = pd.date_range("2025-01-06", periods=7 * 96, freq="15min")
    rng = np.random.default_rng(0)
    for e in range(n):
        df = pd.DataFrame(
            {Role.OAT: rng.normal(50, 5, len(idx)), Role.SPACE_TEMP: rng.normal(72, 1, len(idx))},
            index=idx,
        )
        st.write_role_frame(df, facility_id="fac-1", equip=f"VAV_{e:03d}", equip_class="VAV")
    # one file holding several equipment (row groups whose equip min != max)
    long = pd.concat(
        [ps.role_frame_to_long(df.iloc[:40], equip=f"MIX_{j}", equip_class="VAV") for j in (0, 1)]
    )
    st.write_long(long, facility_id="fac-1")
    st.write_role_frame(df.iloc[:10], facility_id="fac-2", equip="VAV_000", equip_class="VAV")
    return st


def test_one_equipment_read_opens_only_its_files(tmp_path):
    st = _many_equipment_store(tmp_path / "store")
    one = st._facility_dataset("fac-1", ["VAV_007"])
    assert len(one.files) == 1 and "fac-1" in one.files[0]
    assert len(st._facility_dataset("fac-1", ["MIX_1"]).files) == 1
    assert st._facility_dataset("fac-1", ["nope"]).files == []


def test_pruned_reads_are_identical_to_full_reads(tmp_path, monkeypatch):
    st = _many_equipment_store(tmp_path / "store", n=12)
    asks = [
        ("fac-1", "VAV_003", {}),
        ("fac-1", "MIX_0", {}),
        ("fac-1", "VAV_011", {"start": "2025-01-08", "end": "2025-01-09", "resample": "1h"}),
        ("fac-1", "nope", {}),
        ("fac-2", "VAV_000", {"roles": [Role.OAT]}),
    ]
    got = [st.read_role_frame(facility_id=f, equip=e, **kw) for f, e, kw in asks]
    got_long = st.read_long(facility_id="fac-1", equips=["VAV_001", "MIX_1"])
    monkeypatch.setattr(ParquetStore, "_facility_dataset", lambda *a, **k: None)
    want = [st.read_role_frame(facility_id=f, equip=e, **kw) for f, e, kw in asks]
    want_long = st.read_long(facility_id="fac-1", equips=["VAV_001", "MIX_1"])
    assert pickle.dumps(got) == pickle.dumps(want)
    assert pickle.dumps(got_long) == pickle.dumps(want_long)


def test_the_fragment_index_follows_writes(tmp_path):
    st = _many_equipment_store(tmp_path / "store", n=3)
    assert st.read_role_frame(facility_id="fac-1", equip="NEW").empty
    idx = pd.date_range("2025-01-06", periods=4, freq="h")
    st.write_role_frame(
        pd.DataFrame({Role.OAT: [1.0, 2, 3, 4]}, index=idx), facility_id="fac-1", equip="NEW"
    )
    assert st.read_role_frame(facility_id="fac-1", equip="NEW")[Role.OAT].tolist() == [1, 2, 3, 4]


def test_reading_every_equipment_of_a_large_facility_is_fast(tmp_path):
    st = _many_equipment_store(tmp_path / "store", n=150)
    equips = sorted(st.equipment(facility_id="fac-1")["fac-1"])
    t0 = time.perf_counter()
    frames = [st.read_role_frame(facility_id="fac-1", equip=e) for e in equips]
    # ~1 s here; opening every part file per read made this grow with the square of the count
    assert time.perf_counter() - t0 < 60
    assert all(not f.empty for f in frames)
