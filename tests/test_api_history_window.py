"""0.103 (#122): the trend viewer's read side -- windowed, envelope-thinned history and the
time-axis label the page reads from ``/points``.

Pure facade and dispatch calls (no socket, no browser); the page's use of these is covered by the
HTML-content tests in ``tests/test_api_ui.py``.
"""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.api import ReadAPI, dispatch  # noqa: E402
from camber.api.read import _envelope_downsample, _time_axis  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore  # noqa: E402
from camber.store.facilities import FacilityRegistry  # noqa: E402

N = 10_000  # one week of one-minute samples


def _long(values, start="2024-01-01", freq="1min", equip="AHU_1", role="supply_air_temp"):
    idx = pd.date_range(start, periods=len(values), freq=freq)
    return pd.DataFrame({"ts": idx, "equip": equip, "role": role, "value": values})


def _store(tmp_path):
    """One AHU with N one-minute samples: a slow sine, one spike up and one spike down."""
    st = ParquetStore(str(tmp_path / "tsdb"))
    idx = pd.date_range("2024-01-01", periods=N, freq="1min")
    sat = 55 + 5 * np.sin(np.arange(N) / 500.0)
    sat[3_333] = 140.0  # a one-sample spike
    sat[7_777] = -20.0  # and a one-sample dip
    frame = pd.DataFrame({Role.SUPPLY_AIR_TEMP: sat, Role.HEAT_VALVE: np.zeros(N)}, index=idx)
    st.write_role_frame(frame, facility_id="S", equip="AHU_1", equip_class="AHU", name="Site S")
    return st


# --------------------------------------------------------------------------- the envelope


def test_envelope_keeps_extremes_and_respects_the_budget():
    rng = np.random.default_rng(1)
    v = rng.normal(60, 1, 50_000)
    v[12_345], v[40_001] = 999.0, -999.0
    out = _envelope_downsample(_long(v), 500)
    assert 0 < len(out) <= 500
    assert out["value"].max() == 999.0 and out["value"].min() == -999.0
    assert out["ts"].is_monotonic_increasing
    # the first and last samples bound the series: the window it covers is unchanged
    assert out["ts"].iloc[0] >= pd.Timestamp("2024-01-01")


def test_envelope_keeps_every_bucket_extreme():
    """Each bucket's min and max survive, not just the global ones: a spike in any bucket stays."""
    v = np.zeros(20_000)
    spikes = list(range(500, 20_000, 1_000))  # one spike every 1,000 samples
    v[spikes] = 10.0
    out = _envelope_downsample(_long(v), 200)  # 100 buckets of 200 samples
    assert len(out) <= 200
    kept = set(out["ts"])
    ts = pd.date_range("2024-01-01", periods=len(v), freq="1min")
    assert all(ts[i] in kept for i in spikes)


def test_envelope_flatline_stays_flat_and_short_series_untouched():
    flat = _envelope_downsample(_long(np.full(10_000, 72.0)), 100)
    assert len(flat) <= 100 and set(flat["value"]) == {72.0}
    short = _long(np.arange(50.0))
    assert _envelope_downsample(short, 100).equals(short)  # under budget: returned whole
    assert len(_envelope_downsample(short, 2)) <= 2  # the smallest budget


def test_envelope_budget_is_per_series_and_drops_nulls_only_when_thinning():
    a = _long(np.arange(5_000.0), role="supply_air_temp")
    b = _long(np.arange(5_000.0), role="heat_valve")
    b.loc[10, "value"] = np.nan
    out = _envelope_downsample(pd.concat([a, b], ignore_index=True), 300)
    counts = out.groupby("role").size()
    assert counts["supply_air_temp"] <= 300 and counts["heat_valve"] <= 300
    assert out["value"].notna().all()
    small = _long([1.0, np.nan, 3.0])
    assert _envelope_downsample(small, 10)["value"].isna().sum() == 1  # whole, nulls kept


# --------------------------------------------------------------------------- /history


def test_history_whole_span_thinned_keeps_spikes(tmp_path):
    api = ReadAPI(_store(tmp_path))
    h = api.history(facility_id="S", equip="AHU_1", role="supply_air_temp", max_points=400)
    assert h["count"] <= 400 and h["source_count"] == N and h["downsampled"] is True
    vals = [r["value"] for r in h["history"]]
    assert max(vals) == 140.0 and min(vals) == -20.0  # the spike and the dip both survive
    assert h["first"] == "2024-01-01T00:00:00"
    assert h["last"] == (pd.Timestamp("2024-01-01") + pd.Timedelta(minutes=N - 1)).isoformat()
    assert h["max_points"] == 400


def test_history_window_at_full_resolution(tmp_path):
    api = ReadAPI(_store(tmp_path))
    h = api.history(
        facility_id="S",
        equip="AHU_1",
        role="supply_air_temp",
        start="2024-01-02T00:00:00",
        end="2024-01-02T05:59:59",
        max_points=20_000,
    )
    assert h["count"] == h["source_count"] == 360 and h["downsampled"] is False
    assert h["first"] == "2024-01-02T00:00:00" and h["last"] == "2024-01-02T05:59:00"
    assert all("2024-01-02T00" <= r["ts"] < "2024-01-02T06" for r in h["history"])


def test_history_without_new_params_is_unchanged(tmp_path):
    api = ReadAPI(_store(tmp_path))
    h = api.history(facility_id="S", equip="AHU_1", role="supply_air_temp", limit=5)
    assert h["count"] == 5 and h["source_count"] == 5 and h["downsampled"] is False
    assert h["max_points"] is None
    empty = api.history(facility_id="S", equip="AHU_1", role="supply_air_temp", start="2030-01-01")
    assert empty["count"] == 0 and empty["first"] is None and empty["last"] is None


def test_history_offset_timestamps_name_the_wall_clock(tmp_path):
    """The store is naive wall-clock time: an offset on start/end is dropped, not converted."""
    api = ReadAPI(_store(tmp_path))
    h = api.history(
        facility_id="S",
        role="heat_valve",
        start="2024-01-02T00:00:00Z",
        end="2024-01-02T00:09:00+05:00",
    )
    assert h["count"] == 10


def test_dispatch_passes_window_and_max_points(tmp_path):
    api = ReadAPI(_store(tmp_path))
    q = {
        "facility_id": ["S"],
        "equip": ["AHU_1"],
        "role": ["supply_air_temp"],
        "start": ["2024-01-03"],
        "end": ["2024-01-05"],
        "max_points": ["100"],
    }
    status, body = dispatch(api, "GET", "/history", q)
    assert status == 200 and body["count"] <= 100 and body["downsampled"] is True
    assert body["first"].startswith("2024-01-03") and body["last"] <= "2024-01-05T00:00:00"


@pytest.mark.parametrize(
    "bad",
    [
        {"max_points": ["many"]},
        {"max_points": ["1"]},
        {"limit": ["0"]},
        {"start": ["not a date"]},
        {"end": ["2024-13-45"]},
    ],
)
def test_dispatch_rejects_malformed_params_with_400(tmp_path, bad):
    api = ReadAPI(_store(tmp_path))
    status, body = dispatch(api, "GET", "/history", {"facility_id": ["S"], **bad})
    assert status == 400 and body["error"] == "bad request"
    assert next(iter(bad)) in body["detail"]


# --------------------------------------------------------------------------- the time-axis label


def test_time_axis_never_claims_utc_without_a_zone():
    assert _time_axis(None) == {
        "timezone": None,
        "local_label": "local time (no time zone recorded)",
        "utc_label": None,
    }
    assert _time_axis("America/Chicago") == {
        "timezone": "America/Chicago",
        "local_label": "local time (America/Chicago)",
        "utc_label": "time (UTC)",
    }


def test_points_reply_carries_the_time_axis(tmp_path):
    st = _store(tmp_path)
    frame = pd.DataFrame(
        {Role.HEAT_VALVE: range(3)}, index=pd.date_range("2024-01-01", periods=3, freq="1h")
    )
    for fid in ("T", "D", "B"):
        st.write_role_frame(frame, facility_id=fid, equip="AHU_1", equip_class="AHU", name=fid)
    reg = FacilityRegistry(st.root)
    reg.register("T", timezone="America/Chicago")  # a registry zone
    reg.register("D", dataset={"dataset_id": "bts", "local_timezone": "Australia/Sydney"})
    reg.register("B", timezone="not a zone")  # not a zone: treated as none, never guessed
    api = ReadAPI(st)
    axis = {f: api.points(facility_id=f)["time_axis"] for f in ("S", "T", "D", "B")}
    assert axis["S"]["utc_label"] is None and "no time zone" in axis["S"]["local_label"]
    assert axis["B"] == axis["S"]
    assert axis["T"]["local_label"] == "local time (America/Chicago)"
    assert axis["T"]["utc_label"] == "time (UTC)"
    assert axis["D"]["timezone"] == "Australia/Sydney"
    assert "time_axis" not in api.points()  # no facility named: no label to give
    status, body = dispatch(api, "GET", "/points", {"facility_id": ["T"]})
    assert status == 200 and body["time_axis"]["timezone"] == "America/Chicago"
