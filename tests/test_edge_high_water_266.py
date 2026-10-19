"""0.109 (#266): the forwarder's persisted high-water mark (camber.edge.high_water)."""

import io
import json
import os

import numpy as np
import pandas as pd
import pyarrow.parquet as pq
import pytest

from camber.cli import main
from camber.edge import Forwarder, Spool, collect_sink
from camber.edge.high_water import HIGH_WATER_FILE, read_high_water
from camber.model.mapping import MappingProvider
from camber.model.roles import Role

FID = "demo-fac-1"
A, B = "AHU_1_SupplyAirTemp", "AHU_2_SupplyAirTemp"


def _mapping():
    return MappingProvider.from_dict({"patterns": [["SupplyAirTemp", Role.SUPPLY_AIR_TEMP.value]]})


class _GrowingSource:
    """A source whose window grows as the test appends hours (hourly, starting 2024-05-30)."""

    def __init__(self, hours=72, points=(A, B)):
        self.frame = pd.DataFrame(
            {p: np.arange(hours, dtype=float) + i for i, p in enumerate(points)},
            index=pd.date_range("2024-05-30", periods=hours, freq="1h"),
        )
        self.reads = 0

    def grow(self, hours):
        start = self.frame.index[-1] + pd.Timedelta("1h")
        idx = pd.date_range(start, periods=hours, freq="1h")
        more = pd.DataFrame({c: np.arange(hours, dtype=float) + 1000 for c in self.frame}, idx)
        self.frame = pd.concat([self.frame, more])

    def point_names(self):
        return list(self.frame.columns)

    def load_points(self, names, resample="1h"):
        self.reads += 1
        return self.frame[[n for n in names if n in self.frame]].copy()

    def units(self):
        return {}


class _FlakySink:
    """Fails every put while ``down``; otherwise records like ``collect_sink``."""

    def __init__(self):
        self.down = False
        self.log = []

    def put(self, key, data, *, content_type, metadata=None):
        if self.down:
            raise ConnectionError("link down")
        self.log.append({"key": key, "data": data})
        return {"ok": True}


def _fwd(spool_dir, source, sink, **kw):
    return Forwarder(
        source, sink, facility_id=FID, spool=Spool(str(spool_dir)), mapping=_mapping(), **kw
    )


def _months(log):
    return [e["key"].split("/")[1] + "/" + e["key"].split("/")[2] for e in log]


def _rows(entry):
    return pq.read_table(io.BytesIO(entry["data"])).to_pandas()


def _state(spool_dir):
    with open(os.path.join(spool_dir, HIGH_WATER_FILE), encoding="utf-8") as fh:
        return json.load(fh)


def test_a_second_poll_with_nothing_new_sends_nothing(tmp_path):
    src = _GrowingSource()
    sink, log = collect_sink()
    fwd = _fwd(tmp_path, src, sink)
    first = fwd.poll_once()
    assert first.forwarded == 2 and _months(log) == ["year=2024/month=5", "year=2024/month=6"]
    assert first.advanced == 2
    marks = _state(tmp_path)["points"]
    assert marks[A]["ts"].startswith("2024-06-01T23:00") and marks[A]["value"] == 71.0
    second = fwd.poll_once()
    assert second.spooled == 0 and second.forwarded == 0 and second.unchanged == 1
    assert len(log) == 2  # the closed month is not even read; the open one is unchanged


def test_new_rows_send_only_the_open_month_whole(tmp_path):
    src = _GrowingSource()
    sink, log = collect_sink()
    fwd = _fwd(tmp_path, src, sink)
    fwd.poll_once()
    src.grow(5)
    res = fwd.poll_once()
    assert res.forwarded == 1 and _months(log[2:]) == ["year=2024/month=6"]
    june = _rows(log[2])
    # the whole open month (a superset of the landed part, which the landing replaces), not
    # just the new hours, and nothing of May
    assert pd.to_datetime(june["ts"]).min() == pd.Timestamp("2024-06-01")
    assert len(june) == 2 * (24 + 5)
    assert _state(tmp_path)["points"][A]["ts"].startswith("2024-06-02T04:00")


def test_a_revised_last_observation_is_sent(tmp_path):
    # the last hour was still in progress when it was polled: its value changes, nothing is added
    src = _GrowingSource()
    sink, log = collect_sink()
    fwd = _fwd(tmp_path, src, sink)
    fwd.poll_once()
    src.frame.iloc[-1, 0] = 99.5
    res = fwd.poll_once()
    assert res.forwarded == 1 and _months(log[2:]) == ["year=2024/month=6"]
    assert 99.5 in set(_rows(log[2])["value"])
    assert _state(tmp_path)["points"][A]["value"] == 99.5


def test_the_mark_survives_a_restart(tmp_path):
    src = _GrowingSource()
    sink, log = collect_sink()
    _fwd(tmp_path, src, sink).poll_once()
    again = _fwd(tmp_path, src, sink).poll_once()  # a new process on the same spool
    assert again.spooled == 0 and len(log) == 2
    src.grow(1)
    assert _fwd(tmp_path, src, sink).poll_once().forwarded == 1


def test_the_mark_advances_only_on_acknowledgement(tmp_path):
    src = _GrowingSource()
    sink = _FlakySink()
    sink.down = True
    fwd = _fwd(tmp_path, src, sink, clock=lambda: pd.Timestamp("2024-06-02", tz="UTC"))
    fwd.spool.drain = _no_sleep_drain(fwd.spool)
    res = fwd.poll_once()
    assert res.forwarded == 0 and res.advanced == 0
    state = _state(tmp_path)
    assert state["points"] == {} and len(state["inflight"]) == 2  # waiting for the acks
    # a restart while offline: still no mark, so the poll re-reads everything (and the spool
    # keeps one copy of each batch)
    fwd2 = _fwd(tmp_path, src, sink)
    fwd2.spool.drain = _no_sleep_drain(fwd2.spool)
    assert fwd2.poll_once().already_spooled == 2
    sink.down = False
    res = fwd2.poll_once()
    assert res.forwarded == 2 and res.advanced == 2
    state = _state(tmp_path)
    assert set(state["points"]) == {A, B} and state["inflight"] == {}


def _no_sleep_drain(spool):
    real = spool.drain

    def drain(sink, **kw):
        return real(sink, _sleep=lambda s: None, **kw)

    return drain


def test_an_evicted_part_never_advances_the_mark(tmp_path):
    src = _GrowingSource()
    sink, log = collect_sink()
    fwd = _fwd(tmp_path, src, sink)
    fwd.poll_once()
    hw = fwd._hw
    hw.note_sent("gone-key", {A: {"ts": pd.Timestamp("2030-01-01"), "value": 1.0}})
    assert hw.acknowledge([], still_queued=set()) == 0
    assert "gone-key" not in hw.inflight and hw.marks[A]["ts"] < pd.Timestamp("2030-01-01")


def test_an_unreadable_state_falls_back_to_a_full_read(tmp_path, caplog):
    src = _GrowingSource()
    sink, log = collect_sink()
    _fwd(tmp_path, src, sink).poll_once()
    with open(os.path.join(tmp_path, HIGH_WATER_FILE), "w", encoding="utf-8") as fh:
        fh.write('{"version": 1, "fingerp')  # torn
    assert read_high_water(str(tmp_path))["state"] == "unreadable"
    with caplog.at_level("WARNING", logger="camber.edge"):
        res = _fwd(tmp_path, src, sink).poll_once()
    assert "unreadable" in caplog.text
    assert res.spooled == 2  # the pre-0.109 behaviour: the whole window
    assert read_high_water(str(tmp_path))["state"] == "ok"  # rewritten


@pytest.mark.parametrize(
    "bad",
    [
        {"version": 2, "fingerprint": "x"},
        {"version": 1, "fingerprint": "x", "points": {A: {"ts": "not a time", "value": 1}}},
        {"version": 1, "fingerprint": "x", "points": {A: {"ts": "2024-06-01", "value": "1"}}},
        ["a list"],
    ],
)
def test_malformed_states_are_unreadable(tmp_path, bad):
    with open(os.path.join(tmp_path, HIGH_WATER_FILE), "w", encoding="utf-8") as fh:
        json.dump(bad, fh)
    assert read_high_water(str(tmp_path))["state"] == "unreadable"


def test_a_config_change_resets_the_mark(tmp_path, caplog):
    src = _GrowingSource()
    sink, log = collect_sink()
    _fwd(tmp_path, src, sink).poll_once()
    with caplog.at_level("WARNING", logger="camber.edge"):
        res = _fwd(tmp_path, src, sink, high_water_id="another-source").poll_once()
    assert "source or config changed" in caplog.text and res.spooled == 2
    assert _fwd(tmp_path, src, sink, high_water_id="another-source").poll_once().spooled == 0


def test_an_explicit_since_reads_the_window_as_given(tmp_path):
    src = _GrowingSource()
    sink, log = collect_sink()
    fwd = _fwd(tmp_path, src, sink)
    fwd.poll_once()
    res = fwd.poll_once(since="2024-05-30")  # a backfill
    assert res.spooled == 2 and res.unchanged == 0
    assert len(log) == 4  # both months again, as before 0.109
    assert fwd.poll_once().spooled == 0  # and the next poll is back to the marks


def test_high_water_off_keeps_the_pre_0109_behaviour(tmp_path):
    src = _GrowingSource()
    sink, log = collect_sink()
    fwd = _fwd(tmp_path, src, sink, high_water=False)
    fwd.poll_once()
    fwd.poll_once()
    assert len(log) == 4 and not os.path.exists(os.path.join(tmp_path, HIGH_WATER_FILE))


def test_a_point_that_stopped_does_not_resend_its_month(tmp_path):
    # B stops reporting in May; A goes on into June. June's polls never re-send May for B.
    src = _GrowingSource(hours=72)
    src.frame.loc[src.frame.index >= "2024-06-01", B] = np.nan
    sink, log = collect_sink()
    fwd = _fwd(tmp_path, src, sink)
    fwd.poll_once()
    assert _state(tmp_path)["points"][B]["ts"].startswith("2024-05-31T23:00")
    src.grow(3)
    src.frame.loc[src.frame.index >= "2024-06-01", B] = np.nan
    res = fwd.poll_once()
    assert _months(log[2:]) == ["year=2024/month=6"] and res.unchanged == 1
    assert set(_rows(log[2])["equip"]) == {"AHU_1"}


def test_a_source_that_takes_since_reads_from_the_earliest_floor(tmp_path):
    class _SinceSource(_GrowingSource):
        def load_points(self, names, resample="1h", since=None):
            self.since = since
            out = super().load_points(names, resample)
            return out if since is None else out[out.index >= since]

    src = _SinceSource()
    sink, log = collect_sink()
    fwd = _fwd(tmp_path, src, sink)
    fwd.poll_once()
    assert src.since is None
    src.grow(1)
    fwd.poll_once()
    assert src.since == pd.Timestamp("2024-06-01")


def test_edge_status_shows_the_high_water_mark(tmp_path, capsys):
    data = tmp_path / "data.csv"
    df = pd.DataFrame(
        {A: np.arange(48, dtype=float)},
        index=pd.date_range("2024-06-01", periods=48, freq="1h", name="timestamp"),
    )
    df.to_csv(data)
    spool_dir = str(tmp_path / "spool")
    cfg = {
        "facility_id": FID,
        "source": {"kind": "csv_wide", "path": str(data)},
        "sink": {"kind": "collect"},
        "spool_dir": spool_dir,
    }
    path = tmp_path / "edge.json"
    path.write_text(json.dumps(cfg))
    assert main(["edge", "status", str(path)]) == 0
    assert "high-water: none yet" in capsys.readouterr().out
    assert main(["edge", "send-once", str(path)]) == 0
    capsys.readouterr()
    assert main(["edge", "status", str(path)]) == 0
    out = capsys.readouterr().out
    assert "high-water: 1 point(s) acknowledged up to 2024-06-02T23:00:00" in out
    assert main(["edge", "status", str(path), "--json"]) == 0
    hw = json.loads(capsys.readouterr().out)["high_water"]
    assert hw["state"] == "ok" and hw["points"] == 1 and hw["stale"] is False
    assert hw["marks"][A] == {"ts": "2024-06-02T23:00:00", "value": 47.0}
    # a second send-once has nothing new; a config change shows the mark as stale
    assert main(["edge", "send-once", str(path)]) == 0
    assert "parts=0" in capsys.readouterr().out
    path.write_text(json.dumps({**cfg, "resample": "15min"}))
    assert main(["edge", "status", str(path)]) == 0
    assert "STALE" in capsys.readouterr().out
