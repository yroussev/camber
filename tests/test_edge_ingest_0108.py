"""Edge forwarder, spool, sinks, landing and ingest-protocol fixes of 0.108 (#242-#249), and the
rolling-window merge at landing.

Fakes and loopback only: no BACnet broadcast, no network, no cloud SDK.
"""

import hashlib
import json
import os
import sys
import urllib.error
import warnings
from types import SimpleNamespace as NS

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_edge_forwarder import _FakeSource, _forwarder, _mapping  # noqa: E402
from test_edge_row_digest import _put  # noqa: E402

from camber.cli import main  # noqa: E402
from camber.edge import collect_sink  # noqa: E402
from camber.edge.config import load_config  # noqa: E402
from camber.edge.forwarder import Forwarder  # noqa: E402
from camber.edge.quarantine import land, list_quarantine, quarantine_root, release  # noqa: E402
from camber.edge.sink import (  # noqa: E402
    AzureBlobSink,
    GcsSink,
    PermanentSinkError,
    PresignedHttpsSink,
    S3Sink,
    is_permanent_error,
    sink_metadata,
)
from camber.edge.spool import Spool  # noqa: E402
from camber.portfolio import Portfolio  # noqa: E402
from camber.store import ParquetStore  # noqa: E402

FID = "demo-fac-1"


def _write_cfg(tmp_path, obj, name="edge.json"):
    p = tmp_path / name
    p.write_text(json.dumps(obj))
    return str(p)


# --------------------------------------------------------------------------- #242 selftest


def _csv(tmp_path):
    idx = pd.date_range("2024-06-01", periods=24, freq="1h")
    df = pd.DataFrame({"AHU_1_SupplyAirTemp": np.linspace(50, 60, 24)}, index=idx)
    df.index.name = "timestamp"
    path = tmp_path / "data.csv"
    df.to_csv(path)
    return str(path)


def _journal(spool_dir):
    p = os.path.join(spool_dir, "journal.ndjson")
    return open(p, encoding="utf-8").read() if os.path.exists(p) else ""


def test_selftest_never_touches_the_configured_spool(tmp_path, capsys):
    spool_dir = str(tmp_path / "spool")
    cfg = _write_cfg(
        tmp_path,
        {
            "facility_id": FID,
            "source": {"kind": "csv_wide", "path": _csv(tmp_path)},
            "sink": {"kind": "collect"},
            "spool_dir": spool_dir,
        },
    )
    sp = Spool(spool_dir)
    sp.enqueue("facility_id=x/year=2024/month=1/part-a.parquet", b"backlog-1", content_type="x")
    sp.enqueue("facility_id=x/year=2024/month=2/part-b.parquet", b"backlog-2", content_type="x")
    before = (_journal(spool_dir), sorted(os.listdir(os.path.join(spool_dir, "pending"))))
    assert main(["edge", "selftest", cfg]) == 0
    out = capsys.readouterr().out
    assert "would send 1 object(s)" in out and "configured spool was not" in out
    assert (_journal(spool_dir), sorted(os.listdir(os.path.join(spool_dir, "pending")))) == before
    assert Spool(spool_dir).depth()[0] == 2


def test_selftest_does_not_create_a_missing_spool(tmp_path):
    spool_dir = str(tmp_path / "nowhere")
    cfg = _write_cfg(
        tmp_path,
        {
            "facility_id": FID,
            "source": {"kind": "csv_wide", "path": _csv(tmp_path)},
            "sink": {"kind": "collect"},
            "spool_dir": spool_dir,
        },
    )
    assert main(["edge", "selftest", cfg]) == 0
    assert not os.path.exists(spool_dir)


def test_selftest_refuses_a_retired_spool_cleanly(tmp_path, capsys):
    spool_dir = str(tmp_path / "spool")
    Spool(spool_dir).retire({"retired_at": "2026-01-01T00:00:00Z", "device_id": "pi-1"})
    cfg = _write_cfg(
        tmp_path,
        {
            "facility_id": FID,
            "source": {"kind": "csv_wide", "path": _csv(tmp_path)},
            "sink": {"kind": "collect"},
            "spool_dir": spool_dir,
        },
    )
    assert main(["edge", "selftest", cfg]) == 1
    assert "decommissioned" in capsys.readouterr().err


# --------------------------------------------------------------------------- #243 trend logs


class _RingApp:
    """A fake bacpypes3 app: a trend log of ``n`` records, ReadRange by position (index 1 = the
    oldest), ``record-count`` readable."""

    def __init__(self, n=5000, *, record_count=True):
        start = pd.Timestamp("2026-09-01")
        self.buf = [
            NS(timestamp=str(start + pd.Timedelta(minutes=15 * i)), logDatum=NS(realValue=float(i)))
            for i in range(n)
        ]
        self.record_count = record_count
        self.ranges = []
        self.addresses = []

    async def read_property(self, addr, oid, prop):
        self.addresses.append(addr)
        if prop == "record-count" and self.record_count:
            return len(self.buf)
        raise RuntimeError(f"unknown property {prop}")

    async def read_range(self, addr, oid, prop, range_params):
        self.ranges.append(range_params)
        self.addresses.append(addr)
        kind, ref, _d, _t, count = range_params
        assert kind == "p"
        if count > 0:
            return self.buf[ref - 1 : ref - 1 + count]
        return self.buf[max(0, ref + count) : ref]


def test_trend_log_read_takes_the_newest_records():
    from camber.ingest.bacnet import BacnetPoint, BacnetSource, BacnetTarget
    from camber.ingest.bacnet_client import Bacpypes3Client

    app = _RingApp()
    with Bacpypes3Client(app=app, address="192.0.2.10") as cl:
        src = BacnetSource(
            [BacnetPoint("AHU1_SAT", ("trendLog", 1))],
            BacnetTarget(address="192.0.2.10"),
            client=cl,
        )
        s = src.read_trend_log("AHU1_SAT")
    assert len(s) == 1000
    assert s.index.max() == pd.Timestamp(app.buf[-1].timestamp)
    assert s.index.min() == pd.Timestamp(app.buf[-1000].timestamp)
    assert app.ranges == [("p", 5000, None, None, -1000)]


def test_trend_log_count_is_configurable_and_small_buffers_read_whole():
    from camber.ingest.bacnet_client import BacnetClientConfig, Bacpypes3Client

    app = _RingApp(n=300)
    with Bacpypes3Client(app=app, address="192.0.2.10", trend_records=50) as cl:
        assert len(cl.read_trend_log(("trendLog", 1))) == 50
        assert len(cl.read_trend_log(("trendLog", 1), max_records=1000)) == 300
    assert BacnetClientConfig.from_mapping({"trend_records": 96}).trend_records == 96
    with pytest.raises(ValueError):
        Bacpypes3Client(app=_RingApp(), trend_records=0)


def test_trend_log_without_record_count_falls_back_with_a_warning():
    from camber.ingest.bacnet_client import Bacpypes3Client

    app = _RingApp(n=20, record_count=False)
    with Bacpypes3Client(app=app, address="192.0.2.10") as cl:
        with pytest.warns(UserWarning, match="record-count unreadable"):
            pairs = cl.read_trend_log(("trendLog", 1))
    assert len(pairs) == 20 and app.ranges == [("p", 1, None, None, 1000)]


# --------------------------------------------------------------------------- #244 Haystack


_DST_ROWS = [
    {"ts": f"t:2026-03-08T0{h}:00:00-0{o}:00 New_York", "val": f"n:{v} °F"}
    for h, o, v in [(0, 5, 70), (1, 5, 71), (3, 4, 72), (4, 4, 73)]
]


def test_his_read_across_a_dst_change_parses_to_the_wall_clock():
    from camber.ingest.haystack import parse_his_grid

    s = parse_his_grid({"rows": _DST_ROWS}, name="AHU1_SAT")
    assert s.index.tz is None
    assert list(s.index.astype(str)) == [
        "2026-03-08 00:00:00",
        "2026-03-08 01:00:00",
        "2026-03-08 03:00:00",
        "2026-03-08 04:00:00",
    ]
    assert s.attrs["unit"] == "°F"


def test_his_read_explicit_timezone_wins_and_strict_refuses_unzoned():
    from camber.ingest.haystack import haystack_tz, parse_his_grid
    from camber.tsparse import TimezoneWarning

    s = parse_his_grid({"rows": _DST_ROWS}, timezone="UTC")
    assert str(s.index[0]) == "2026-03-08 05:00:00"
    rows = [{"ts": "t:2026-01-01T00:00:00-05:00", "val": "n:1"}]
    with pytest.raises(ValueError):
        parse_his_grid({"rows": rows}, strict_timezone=True)
    with pytest.warns(TimezoneWarning):
        parse_his_grid({"rows": rows})
    assert haystack_tz("New_York") == "America/New_York"
    assert haystack_tz("UTC") == "UTC" and haystack_tz("Nowhere_Town") is None


def test_haystack_adapter_keeps_units_and_takes_timezone():
    from camber.ingest.haystack import HaystackAdapter

    grids = {
        "r1": {"rows": _DST_ROWS},
        "r2": {
            "rows": [
                {
                    "ts": {
                        "_kind": "dateTime",
                        "val": "2026-03-08T00:00:00-05:00",
                        "tz": "New_York",
                    },
                    "val": {"_kind": "number", "val": 21, "unit": "°C"},
                }
            ]
        },
    }
    a = HaystackAdapter(
        "https://hs.example",
        point_refs={"AHU1_SAT": "r1", "AHU2_SAT": "r2"},
        transport=lambda op, p: grids[p["id"]],
        timezone="America/New_York",
    )
    df = a.load_points(["AHU1_SAT", "AHU2_SAT"], resample="1h")
    assert df.index.tz is None and len(df) == 5
    assert a.units() == {"AHU1_SAT": "°F", "AHU2_SAT": "°C"}


# --------------------------------------------------------------------------- #245 sinks / spool


def _big_manifest():
    return {
        "facility_id": FID,
        "year": 2026,
        "month": 3,
        "window": ["2026-03-01", "2026-03-03"],
        "rows": 36000,
        "roles": ["damper", "zone_temp"],
        "equips": [f"VAV_{i:03d}_Ünïcode" for i in range(250)],
        "quality": {"n_points": 500, "mean_coverage": 1.0},
        "content_sha256": "a" * 64,
        "rows_sha256": "b" * 64,
        "schema_version": 1,
    }


def test_sink_metadata_is_a_compact_ascii_subset():
    meta = sink_metadata(_big_manifest())
    assert set(meta) == {"sha256", "rows", "rows_sha256", "schema_version", "year", "month"}
    assert meta["sha256"] == "a" * 64 and meta["rows"] == "36000" and meta["month"] == "3"
    assert sum(len(k) + len(v) for k, v in meta.items()) < 2048
    assert all(v.isascii() for v in meta.values())
    assert sink_metadata({"rows_sha256": None, "content_sha256": "é"}) == {"sha256": "\\u00e9"}


def test_sdk_sinks_send_only_the_compact_subset():
    calls = {}

    class S3:
        def put_object(self, **kw):
            calls["s3"] = kw["Metadata"]

    class Blob:
        def __init__(self):
            self.metadata = None

        def upload_from_string(self, data, content_type=None):
            calls["gcs"] = self.metadata

    blob = Blob()
    gcs = NS(bucket=lambda b: NS(blob=lambda k: blob))

    class Container:
        def upload_blob(self, name, data, overwrite=False, metadata=None):
            calls["azure"] = metadata

    m = _big_manifest()
    S3Sink(bucket="b", client=S3()).put("k", b"d", metadata=m)
    GcsSink(bucket="b", client=gcs).put("k", b"d", metadata=m)
    AzureBlobSink(container="c", client=Container()).put("k", b"d", metadata=m)
    assert calls["s3"] == calls["gcs"] == calls["azure"] == sink_metadata(m)


def test_a_large_site_forwards_through_an_s3_metadata_limit(tmp_path):
    idx = pd.date_range("2026-03-01", periods=24, freq="1h")
    frame = pd.DataFrame(
        {f"VAV_{i:03d}_{p}": np.random.rand(24) for i in range(250) for p in ("ZoneTemp", "Dmp")},
        index=idx,
    )

    class Src:
        def point_names(self):
            return list(frame.columns)

        def load_points(self, names, resample="1h"):
            return frame[names]

    class LimitedS3:
        def put_object(self, Bucket, Key, Body, ContentType, Metadata):
            if sum(len(k) + len(v) for k, v in Metadata.items()) > 2048:
                raise RuntimeError("MetadataTooLarge")

    sp = Spool(str(tmp_path / "spool"))
    fw = Forwarder(Src(), S3Sink(bucket="b", client=LimitedS3()), facility_id=FID, spool=sp)
    assert fw.poll_once().forwarded == 1 and sp.depth()[0] == 0


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _LoopOpener:
    """A loopback opener: ``status`` (400) for ``month=1/`` keys, 200 otherwise (or raise
    ``fail`` for every key)."""

    def __init__(self, fail=None, status=400):
        self.fail = fail
        self.status = status
        self.urls = []

    def open(self, req, timeout=None):
        self.urls.append(req.full_url)
        if self.fail is not None:
            raise self.fail
        if "month=1/" in req.full_url:
            raise urllib.error.HTTPError(req.full_url, self.status, "Refused", {}, None)
        return _Resp()


def _presigned(opener):
    return PresignedHttpsSink(
        url_template="https://landing.example/{key}", host="landing.example", _opener=opener
    )


def _seed(sp):
    sp.enqueue("facility_id=s/year=2026/month=1/part-a.parquet", b"rejected", content_type="x")
    for m in range(2, 6):
        sp.enqueue(f"facility_id=s/year=2026/month={m}/part-b.parquet", b"good", content_type="x")


def test_a_rejected_batch_never_blocks_the_ones_behind_it(tmp_path):
    sp = Spool(str(tmp_path / "spool"), dead_letter_after=3)
    _seed(sp)
    sink = _presigned(_LoopOpener())
    r = sp.drain(sink, _sleep=lambda s: None)
    assert r.forwarded == 4 and r.rejected == 1 and r.remaining == 1 and not r.dead
    (head,) = sp.pending()
    assert head.rejects == 1 and "400" in head.last_error
    for _ in range(2):
        r = sp.drain(sink, _sleep=lambda s: None)
    assert r.dead == ["facility_id=s/year=2026/month=1/part-a.parquet"]
    assert sp.depth()[0] == 0
    (dead,) = sp.dead_letters()
    assert dead["rejects"] == 3 and "400" in dead["error"]
    assert dead["sha256"] == hashlib.sha256(b"rejected").hexdigest()
    ops = [json.loads(line)["op"] for line in _journal(sp.root).splitlines()]
    assert ops.count("dead") == 1
    # compaction keeps the dead letter out of the queue and the record in place
    sp.compact()
    assert sp.depth()[0] == 0 and len(sp.dead_letters()) == 1
    # requeue sends it again with a fresh count
    (again,) = sp.requeue_dead()
    assert again.key == dead["key"] and again.rejects == 0 and sp.dead_letters() == []
    assert sp.pending()[0].seq > dead["seq"]


def test_a_transient_failure_still_stops_the_cycle(tmp_path):
    sp = Spool(str(tmp_path / "spool"))
    _seed(sp)
    r = sp.drain(_presigned(_LoopOpener(fail=urllib.error.URLError("down"))), _sleep=lambda s: 0)
    assert r.forwarded == 0 and r.failed == 1 and r.rejected == 0 and r.remaining == 5
    assert sp.pending()[0].rejects == 0 and sp.pending()[0].attempts == 1


def test_permanent_error_classification():
    assert is_permanent_error(urllib.error.HTTPError("u", 400, "Bad", {}, None))
    assert not is_permanent_error(urllib.error.HTTPError("u", 429, "Slow", {}, None))
    assert not is_permanent_error(urllib.error.HTTPError("u", 503, "Busy", {}, None))
    assert not is_permanent_error(urllib.error.URLError("no route"))
    assert not is_permanent_error(OSError("link down"))
    assert is_permanent_error(ValueError("resolved egress host is not the allowlisted host"))
    assert is_permanent_error(PermanentSinkError("refused"))

    class ClientError(Exception):  # botocore's shape
        response = {
            "Error": {"Code": "MetadataTooLarge"},
            "ResponseMetadata": {"HTTPStatusCode": 400},
        }

    assert is_permanent_error(ClientError())


def test_edge_status_shows_stuck_and_dead_batches(tmp_path, capsys):
    spool_dir = str(tmp_path / "spool")
    cfg = _write_cfg(
        tmp_path,
        {
            "facility_id": FID,
            "sink": {"kind": "collect"},
            "spool_dir": spool_dir,
            "dead_letter_after": 1,
        },
    )
    sp = Spool(spool_dir, dead_letter_after=1)
    _seed(sp)
    sp.drain(_presigned(_LoopOpener(fail=urllib.error.URLError("down"))), _sleep=lambda s: 0)
    assert main(["edge", "status", cfg]) == 0
    out = capsys.readouterr().out
    assert "STUCK: 1 batch(es)" in out and "URLError" in out
    sp.drain(_presigned(_LoopOpener()), _sleep=lambda s: 0)
    assert main(["edge", "status", cfg]) == 0
    out = capsys.readouterr().out
    assert "DEAD: 1 batch(es)" in out and "month=1/part-a.parquet" in out
    assert main(["edge", "status", cfg, "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    assert doc["pending"] == 0 and len(doc["dead"]) == 1
    # requeue: a dry run first, then --apply
    assert main(["edge", "requeue", cfg]) == 0 and Spool(spool_dir).depth()[0] == 0
    assert main(["edge", "requeue", cfg, "--apply"]) == 0
    assert Spool(spool_dir).depth()[0] == 1 and Spool(spool_dir).dead_letters() == []
    assert main(["edge", "requeue", cfg, "--key", "nope", "--apply"]) == 1


# --------------------------------------------------------------------------- #246 dedupe


class _Down:
    def put(self, *a, **k):
        raise OSError("link down")


def test_repeated_polls_while_offline_keep_one_copy_per_batch(tmp_path):
    sp = Spool(str(tmp_path / "spool"))
    fw = Forwarder(
        _FakeSource(start="2024-01-01", n=24 * 90),
        _Down(),
        facility_id=FID,
        spool=sp,
        mapping=_mapping(),
    )
    first = fw.poll_once()
    n, nbytes = sp.depth()
    assert first.spooled == n == 3 and first.already_spooled == 0
    for _ in range(3):
        again = fw.poll_once()
        assert again.already_spooled == 3 and again.spooled == 0
    assert sp.depth() == (n, nbytes)


def test_enqueue_skips_the_same_bytes_but_keeps_new_content(tmp_path):
    sp = Spool(str(tmp_path / "spool"))
    a = sp.enqueue("k", b"one", content_type="x")
    assert sp.enqueue("k", b"one", content_type="x").seq == a.seq
    assert sp.enqueue("k", b"two", content_type="x").seq != a.seq  # a different body: queued
    assert sp.depth()[0] == 2


def test_zero_or_negative_spool_cap_is_refused(tmp_path):
    with pytest.raises(ValueError, match="max_bytes"):
        Spool(str(tmp_path / "s"), max_bytes=0)


# --------------------------------------------------------------------------- #247 quarantine


@pytest.fixture
def ws(tmp_path):
    pf = Portfolio.init(str(tmp_path / "ws"))
    pf.add_facility("Demo", facility_id=FID, reason="t", activate=True)
    return pf


def _audit(pf):
    with open(os.path.join(pf.root, "_audit.ndjson"), encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def test_a_quarantine_key_collision_never_overwrites_and_never_aborts(ws, tmp_path):
    ws.add_facility("Other", facility_id="other-fac", reason="t", activate=True)
    ws.transition(FID, "suspend", reason="t")
    inbox = str(tmp_path / "inbox")
    key = f"facility_id={FID}/year=2026/month=3/batch.parquet"
    _put(inbox, key, b"first-upload")
    land(ws, inbox, apply=True, reason="one")
    rec_path = os.path.join(quarantine_root(ws), *key.split("/")) + ".quarantine.json"
    rec = json.load(open(rec_path))
    _put(inbox, key, b"second-upload-different")
    # another facility's valid part behind it in the same run
    sink, log = collect_sink()
    _forwarder(tmp_path / "spool", sink).poll_once()
    good_key = log[0]["key"].replace("demo-fac-1", "other-fac")
    _put(inbox, good_key, log[0]["data"])
    rep = land(ws, inbox, apply=True, reason="two")
    rows = {r["key"]: r for r in rep["objects"]}
    assert rows[key]["category"] == "quarantine_collision" and rows[key]["to"] == "inbox"
    assert rows[good_key]["to"] == "store"
    assert json.load(open(rec_path)) == rec  # the record is untouched
    held = open(os.path.join(quarantine_root(ws), *key.split("/")), "rb").read()
    assert held == b"first-upload"
    assert open(os.path.join(inbox, *key.split("/")), "rb").read() == b"second-upload-different"
    phases = [r["details"].get("phase") for r in _audit(ws) if r["action"] == "edge.land"]
    assert phases.count("begin") == phases.count("done")


def test_one_failing_object_is_reported_and_the_run_goes_on(ws, tmp_path, monkeypatch):
    from camber.edge import quarantine as qmod

    ws.add_facility("Other", facility_id="other-fac", reason="t", activate=True)
    ws.transition(FID, "suspend", reason="t")
    inbox = str(tmp_path / "inbox")
    bad = f"facility_id={FID}/year=2026/month=3/batch.parquet"
    _put(inbox, bad, b"x")
    sink, log = collect_sink()
    _forwarder(tmp_path / "spool", sink).poll_once()
    good = log[0]["key"].replace("demo-fac-1", "other-fac")
    _put(inbox, good, log[0]["data"])
    real = qmod._quarantine_one

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(qmod, "_quarantine_one", boom)
    rep = land(ws, inbox, apply=True, reason="t")
    assert rep["errors"] == [{"key": bad, "error": "OSError: disk full"}]
    assert {r["key"]: r.get("result") for r in rep["objects"]}[good] == "moved"
    done = [r for r in _audit(ws) if r["details"].get("phase") == "done"]
    assert any(d["details"].get("errors") for d in done)
    monkeypatch.setattr(qmod, "_quarantine_one", real)
    assert land(ws, inbox, apply=True, reason="again")["errors"] == []


def test_release_rechecks_the_held_objects_hash(ws, tmp_path):
    ws.transition(FID, "suspend", reason="t")
    inbox = str(tmp_path / "inbox")
    sink, log = collect_sink()
    _forwarder(tmp_path / "spool", sink).poll_once()
    key, data = log[0]["key"], log[0]["data"]
    _put(inbox, key, data)
    land(ws, inbox, apply=True, reason="t")
    ws.transition(FID, "resume", reason="t")
    held = os.path.join(quarantine_root(ws), *key.split("/"))
    with open(held, "ab") as fh:  # the held bytes changed after quarantine
        fh.write(b"tampered")
    os.remove(held + ".quarantine.json")  # and its record is gone
    rep = release(ws, facility_id=FID)
    assert rep["planned"] == [] and "part-<sha16>" in rep["refused"][0]["why"]


def test_release_refuses_an_object_that_differs_from_its_record(ws, tmp_path):
    ws.transition(FID, "suspend", reason="t")
    inbox = str(tmp_path / "inbox")
    key = f"facility_id={FID}/year=2026/month=3/batch.parquet"
    _put(inbox, key, b"one")
    land(ws, inbox, apply=True, reason="t")
    ws.transition(FID, "resume", reason="t")
    with open(os.path.join(quarantine_root(ws), *key.split("/")), "wb") as fh:
        fh.write(b"two")
    rep = release(ws, facility_id=FID)
    assert rep["planned"] == [] and "quarantine record" in rep["refused"][0]["why"]


# --------------------------------------------------------------------------- the merge rule


class _Ring(_FakeSource):
    """A trend-log ring buffer: the last ``n`` hours up to ``end``; each value depends only on
    its hour, so two polls agree wherever they overlap."""

    def __init__(self, end, n, *, edit=None):
        self.idx = pd.date_range(end=end, periods=n, freq="1h")
        self.reads = 0
        self.edit = edit

    def load_points(self, names, resample="1h"):
        hours = ((self.idx - pd.Timestamp("2024-06-01")) / pd.Timedelta("1h")).to_numpy()
        f = pd.DataFrame({n: hours + 50.0 + k for k, n in enumerate(names)}, index=self.idx)
        if self.edit is not None:
            f.loc[pd.Timestamp(self.edit), names[0]] += 9.0
        return f


def _ring_poll(tmp_path, tag, end, n, **kw):
    sink, log = collect_sink()
    fwd = _forwarder(tmp_path / f"spool-{tag}", sink)
    fwd.source = _Ring(end, n, **kw)
    fwd.poll_once()
    (obj,) = log
    return obj["key"], obj["data"]


def _parts(pf, key):
    d = os.path.join(pf.store_root, *key.split("/")[:-1])
    return sorted(n for n in os.listdir(d) if n.endswith(".parquet") and not n.startswith("_"))


def test_a_ring_buffer_across_three_polls_merges_into_one_part(ws, tmp_path):
    inbox = str(tmp_path / "inbox")
    ends = ("2024-06-02 23:00", "2024-06-03 23:00", "2024-06-04 23:00")  # 48 h windows, 24 h on
    for i, end in enumerate(ends):
        key, data = _ring_poll(tmp_path, str(i), end, 48)
        _put(inbox, key, data)
        rep = land(ws, inbox, apply=True, reason=f"poll {i}")
        (row,) = rep["objects"]
        assert row["to"] == "store"
        if i:
            assert row["result"] == "merged" and rep["merges"] == 1
            assert (
                row["merged"]["new_rows"] == 48 * 3 and row["merged"]["kept_old_rows"] == 24 * i * 3
            )
            assert row["merged"]["revised"] == 0
        (part,) = _parts(ws, key)  # one part in the partition
        long = ParquetStore(ws.store_root).read_long()
        assert not long.duplicated(["ts", "equip", "role"]).any()
        assert len(long) == (48 + 24 * i) * 3  # nothing of the oldest hours lost
        assert long["ts"].min() == pd.Timestamp("2024-06-01 00:00")
    # the stored part is canonical and named by its own bytes
    p = os.path.join(ws.store_root, *key.split("/")[:-1], part)
    assert part == f"part-{hashlib.sha256(open(p, 'rb').read()).hexdigest()[:16]}.parquet"
    done = [r for r in _audit(ws) if r["action"] == "edge.land" and r["details"]["phase"] == "done"]
    merged = done[-1]["details"]["merged"]
    assert list(merged.values())[0]["stored_as"].endswith(part)
    assert list_quarantine(ws) == []


def test_a_disagreement_in_the_overlap_is_a_conflict(ws, tmp_path):
    inbox = str(tmp_path / "inbox")
    k1, d1 = _ring_poll(tmp_path, "a", "2024-06-02 23:00", 48)
    _put(inbox, k1, d1)
    land(ws, inbox, apply=True, reason="first")
    k2, d2 = _ring_poll(tmp_path, "b", "2024-06-03 23:00", 48, edit="2024-06-02 10:00")
    _put(inbox, k2, d2)
    (row,) = land(ws, inbox, apply=True, reason="second")["objects"]
    assert row["category"] == "conflict" and row["to"] == "quarantine"
    assert _parts(ws, k1) == [k1.split("/")[-1]]


def test_a_held_facility_keeps_the_merged_away_part_in_quarantine(ws, tmp_path):
    ws.hold(FID, reason="litigation")
    inbox = str(tmp_path / "inbox")
    k1, d1 = _ring_poll(tmp_path, "a", "2024-06-02 23:00", 48)
    _put(inbox, k1, d1)
    land(ws, inbox, apply=True, reason="first")
    k2, d2 = _ring_poll(tmp_path, "b", "2024-06-03 23:00", 48)
    _put(inbox, k2, d2)
    (row,) = land(ws, inbox, apply=True, reason="second")["objects"]
    assert row["result"] == "merged" and row["replaced"] == [k1.split("/")[-1]]
    (q,) = list_quarantine(ws)
    assert q["key"] == k1 and q["category"] == "duplicate" and "merged" in q["detail"]
    assert q["sha256"] == hashlib.sha256(d1).hexdigest()


def test_a_dry_run_plans_the_merge_and_writes_nothing(ws, tmp_path):
    inbox = str(tmp_path / "inbox")
    k1, d1 = _ring_poll(tmp_path, "a", "2024-06-02 23:00", 48)
    _put(inbox, k1, d1)
    land(ws, inbox, apply=True, reason="first")
    k2, d2 = _ring_poll(tmp_path, "b", "2024-06-03 23:00", 48)
    _put(inbox, k2, d2)
    rep = land(ws, inbox)
    assert rep["merges"] == 1 and rep["objects"][0]["merged"]["kept_old_rows"] == 72
    assert _parts(ws, k1) == [k1.split("/")[-1]]


# --------------------------------------------------------------------------- #248 discovery


class _Disc:
    def who_is(self, lo, hi, address=None):
        return [(1001, "192.0.2.11", 5), (1002, "192.0.2.12", 5)]

    def read_object_list(self, a, inst):
        return [("device", inst), ("trendLog", 1)]

    def read_object_metadata(self, a, oid, props):
        name = "ZN-T TREND" if oid[0] == "trendLog" else f"VAV-{a[-2:]}"
        return {"object-name": name, "units": "degreesFahrenheit"}


def test_same_named_objects_on_two_devices_stay_two_points_read_at_their_devices():
    from camber.ingest.bacnet import BacnetSource, BacnetTarget
    from camber.ingest.bacnet_discovery import discover, discovery_to_points

    with pytest.warns(UserWarning, match="share a name"):
        pts = discovery_to_points(discover(_Disc()))
    assert [p.name for p in pts] == ["VAV-11_ZN-T TREND", "VAV-12_ZN-T TREND"]
    assert [(p.device_instance, p.address) for p in pts] == [
        (1001, "192.0.2.11"),
        (1002, "192.0.2.12"),
    ]

    class Client:
        calls = []

        def read_trend_log(self, oid, address=None):
            self.calls.append(address)
            return [("2026-01-01T00:00:00", 1.0)]

    cl = Client()
    src = BacnetSource(pts, BacnetTarget(address="192.0.2.11"), client=cl)
    assert src.point_names() == ["VAV-11_ZN-T TREND", "VAV-12_ZN-T TREND"]
    src.load_points(src.point_names())
    assert cl.calls == ["192.0.2.11", "192.0.2.12"]


def test_unique_names_keep_their_name_and_no_warning():
    from camber.ingest.bacnet_discovery import (
        DiscoveredDevice,
        DiscoveredObject,
        discovery_to_points,
    )

    dev = DiscoveredDevice(instance=7, address="192.0.2.7", object_name="AHU-1")
    dev.objects = [
        DiscoveredObject(7, "192.0.2.7", ("trendLog", 1), "SAT", is_trend=True),
        DiscoveredObject(7, "192.0.2.7", ("trendLog", 2), "SAT", is_trend=True),
        DiscoveredObject(7, "192.0.2.7", ("trendLog", 3), "RAT", is_trend=True),
    ]
    with pytest.warns(UserWarning):
        pts = discovery_to_points([dev])
    assert [p.name for p in pts] == ["AHU-1_SAT_trendLog1", "AHU-1_SAT_trendLog2", "RAT"]
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert [
            p.name for p in discovery_to_points([NS(**{**vars(dev), "objects": dev.objects[2:]})])
        ] == ["RAT"]


def test_bacnet_source_refuses_duplicate_point_names():
    from camber.ingest.bacnet import BacnetPoint, BacnetSource, BacnetTarget

    with pytest.raises(ValueError, match="unique"):
        BacnetSource(
            [BacnetPoint("X", ("trendLog", 1)), BacnetPoint("X", ("trendLog", 1))],
            BacnetTarget(address="192.0.2.1"),
            client=object(),
        )


# --------------------------------------------------------------------------- #249 config


@pytest.mark.parametrize(
    "cfg",
    [
        {
            "sink": {
                "kind": "presigned",
                "host": "a.blob.core.windows.net",
                "_url": "https://a.blob.core.windows.net/l/{key}?sv=2022&se=2027&sig=AbC%3D",
            }
        },
        {"source": {"kind": "sql", "dsn": "postgresql://reader:pw@historian.local/trends"}},
        {"source": {"kind": "mqtt", "broker": "mqtts://user:pw@broker.local:8883"}},
        {"_relay": "http://broker.local/put?signature=abc"},
        {"_gcs": "https://storage.googleapis.com/b/o?X-Goog-Signature=abc"},
        {"_az": "DefaultEndpointsProtocol=https;AccountName=a;AccountKey=xyz"},
    ],
)
def test_secret_shapes_are_refused_whatever_the_scheme(tmp_path, cfg):
    path = _write_cfg(tmp_path, {"facility_id": FID, **cfg})
    with pytest.raises(ValueError, match="secret"):
        load_config(path, environ={})


def test_a_user_without_a_password_is_not_a_secret(tmp_path):
    path = _write_cfg(
        tmp_path,
        {"facility_id": FID, "source": {"kind": "sql", "dsn": "postgresql://reader@db.local/t"}},
    )
    assert load_config(path, environ={}).source["dsn"].startswith("postgresql://reader@")


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ({"quality": "false"}, "quality"),
        ({"interval": -5}, "interval"),
        ({"interval": 0}, "interval"),
        ({"spool_max_bytes": 0}, "spool_max_bytes"),
        ({"spool_max_bytes": "lots"}, "spool_max_bytes"),
        ({"wire_format": "csv"}, "wire_format"),
        ({"part_layout": "new"}, "part_layout"),
        ({"resample": "fortnightly-ish"}, "resample"),
        ({"timezone": "Mars/Base"}, "timezone"),
        ({"dead_letter_after": 0}, "dead_letter_after"),
        ({"sink": {"kind": "ftp"}}, "sink kind"),
    ],
)
def test_values_and_keys_are_validated(tmp_path, extra, match):
    path = _write_cfg(tmp_path, {"facility_id": FID, **extra})
    with pytest.raises(ValueError, match=match):
        load_config(path, environ={})


def test_env_values_are_validated_too(tmp_path):
    path = _write_cfg(tmp_path, {"facility_id": FID})
    with pytest.raises(ValueError, match="interval"):
        load_config(path, environ={"CAMBER_EDGE_INTERVAL": "-1"})


def test_comments_and_a_tiny_cap_warn_rather_than_fail(tmp_path):
    path = _write_cfg(
        tmp_path,
        {
            "facility_id": FID,
            "_comment": "x",
            "note": "y",
            "spool_max_bytes": 4096,
            "source": {"kind": "sql", "note": "injected"},
        },
    )
    with pytest.warns(UserWarning, match="spool_max_bytes"):
        cfg = load_config(path, environ={})
    assert cfg.spool_max_bytes == 4096


def test_timezone_reaches_file_sources(tmp_path):
    from camber.edge.config import build_forwarder

    csv = tmp_path / "d.csv"
    csv.write_text("timestamp,AHU_1_SupplyAirTemp\n2024-06-01T12:00:00Z,55\n")
    path = _write_cfg(
        tmp_path,
        {
            "facility_id": FID,
            "timezone": "America/Chicago",
            "sink": {"kind": "collect"},
            "source": {"kind": "csv_wide", "path": str(csv)},
            "spool_dir": str(tmp_path / "spool"),
        },
    )
    fwd = build_forwarder(load_config(path, environ={}))
    assert fwd.source.timezone == "America/Chicago"
    frame = fwd.source.load_points(fwd.source.point_names(), resample=None)
    assert str(frame.index[0]) == "2024-06-01 07:00:00"  # 12:00 UTC on a Chicago wall clock
    over = _write_cfg(
        tmp_path,
        {
            "facility_id": FID,
            "timezone": "America/Chicago",
            "sink": {"kind": "collect"},
            "source": {"kind": "csv_wide", "path": str(csv), "timezone": "UTC"},
            "spool_dir": str(tmp_path / "spool"),
        },
        name="over.json",
    )
    assert build_forwarder(load_config(over, environ={})).source.timezone == "UTC"


class _RingInProgress(_Ring):
    def load_points(self, names, resample="1h"):
        f = super().load_points(names, resample)
        f.iloc[-1, :] += 0.25  # the hour still in progress at this poll
        return f


def test_a_merge_may_revise_the_last_stored_observation(ws, tmp_path):
    inbox = str(tmp_path / "inbox")
    sink, log = collect_sink()
    fwd = _forwarder(tmp_path / "spool-a", sink)
    fwd.source = _RingInProgress("2024-06-02 23:00", 48)
    fwd.poll_once()
    _put(inbox, log[0]["key"], log[0]["data"])
    land(ws, inbox, apply=True, reason="first")
    k2, d2 = _ring_poll(tmp_path, "b", "2024-06-03 23:00", 48)
    _put(inbox, k2, d2)
    (row,) = land(ws, inbox, apply=True, reason="second")["objects"]
    assert row["result"] == "merged" and row["merged"]["revised"] == 3
    long = ParquetStore(ws.store_root).read_long()
    assert len(long) == 72 * 3 and not long.duplicated(["ts", "equip", "role"]).any()
    last = long[long["ts"] == pd.Timestamp("2024-06-02 23:00")]
    hours = (pd.Timestamp("2024-06-02 23:00") - pd.Timestamp("2024-06-01")) / pd.Timedelta("1h")
    assert sorted(last["value"]) == [hours + 50.0, hours + 51.0, hours + 52.0]  # the new values


def test_decommission_refuses_while_a_batch_is_dead_lettered(tmp_path):
    from camber.edge.decommission import decommission

    sp = Spool(str(tmp_path / "spool"), dead_letter_after=1)
    _seed(sp)
    res = decommission(
        sp,
        _presigned(_LoopOpener()),
        facility_id="s",
        apply=True,
        yes=True,
        reason="sold",
        wait=0,
        _sleep=lambda s: None,
    )
    assert res.refused and not res.retired and sp.retirement() is None
    assert [u["key"] for u in res.unacknowledged if u.get("dead")] == [
        "facility_id=s/year=2026/month=1/part-a.parquet"
    ]


# --------------------------------------------------------------------------- 0.108 policy


def _auth_opener(status):
    return _LoopOpener(fail=urllib.error.HTTPError("u", status, "Auth", {}, None))


@pytest.mark.parametrize("status", [401, 403])
def test_an_auth_refusal_is_transient_and_never_dead_letters(tmp_path, status, caplog):
    assert not is_permanent_error(urllib.error.HTTPError("u", status, "Auth", {}, None))
    clock = iter(f"2026-10-18T0{i}:00:00+00:00" for i in range(10))
    sp = Spool(str(tmp_path / "spool"), dead_letter_after=1)
    _seed(sp)
    sp._clock = lambda: pd.Timestamp(next(clock)).to_pydatetime()
    with caplog.at_level("WARNING", logger="camber.edge"):
        for _ in range(4):
            r = sp.drain(_presigned(_auth_opener(status)), _sleep=lambda s: None)
            # the cycle stops at the head batch, nothing is rejected or dead-lettered
            assert r.forwarded == 0 and r.rejected == 0 and not r.dead and r.auth_failed
    assert "AUTH FAILING" in caplog.text
    assert sp.depth()[0] == 5 and sp.dead_letters() == []
    assert sp.pending()[0].rejects == 0 and sp.pending()[0].attempts == 4
    auth = sp.auth_failing()
    assert auth["since"].startswith("2026-10-18T00") and auth["failures"] == 4
    assert str(status) in auth["error"]
    # once the credentials work again, the record is cleared and everything drains
    r = sp.drain(_presigned(_LoopOpener(status=400)), _sleep=lambda s: None)
    assert r.forwarded == 4 and sp.auth_failing() is None


def test_edge_status_shows_auth_failing_since(tmp_path, capsys):
    spool_dir = str(tmp_path / "spool")
    cfg = _write_cfg(
        tmp_path, {"facility_id": FID, "sink": {"kind": "collect"}, "spool_dir": spool_dir}
    )
    sp = Spool(spool_dir)
    _seed(sp)
    sp.drain(_presigned(_auth_opener(401)), _sleep=lambda s: None)
    since = sp.auth_failing()["since"]
    assert main(["edge", "status", cfg]) == 0
    out = capsys.readouterr().out
    assert f"AUTH FAILING since {since}" in out and "401" in out
    assert main(["edge", "status", cfg, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["auth_failing"]["since"] == since


@pytest.mark.parametrize(
    ("extra", "match"),
    [
        ({"spool_dri": "/x"}, "did you mean 'spool_dir'"),
        ({"wire_fromat": "ndjson"}, "did you mean 'wire_format'"),
        ({"sink": {"kind": "s3", "bucket": "b", "prefx": "p"}}, "prefx"),
        ({"source": {"kind": "csv_wide", "path": "x.csv", "tz": "UTC"}}, "'tz'"),
    ],
)
def test_unknown_keys_warn_in_0108(tmp_path, caplog, extra, match):
    path = _write_cfg(tmp_path, {"facility_id": FID, **extra})
    with caplog.at_level("WARNING", logger="camber.edge"):
        with pytest.warns(FutureWarning, match="0.109 will refuse"):
            cfg = load_config(path, environ={})
    assert match in caplog.text and any(match in w for w in cfg.warnings)
    assert cfg.spool_dir == "./camber-edge-spool"  # the typo is ignored, the default applies


def test_unknown_keys_show_in_status_and_selftest_and_the_forwarder_runs(tmp_path, capsys):
    spool_dir = str(tmp_path / "spool")
    cfg = _write_cfg(
        tmp_path,
        {
            "facility_id": FID,
            "source": {"kind": "csv_wide", "path": _csv(tmp_path)},
            "sink": {"kind": "collect"},
            "spool_dir": spool_dir,
            "intervall": 60,
        },
    )
    with pytest.warns(FutureWarning):
        assert main(["edge", "selftest", cfg]) == 0
    out = capsys.readouterr().out
    assert "CONFIG WARNING" in out and "did you mean 'interval'" in out
    assert "would send 1 object(s)" in out
    with pytest.warns(FutureWarning):
        assert main(["edge", "send-once", cfg]) == 0
    capsys.readouterr()
    with pytest.warns(FutureWarning):
        assert main(["edge", "status", cfg]) == 0
    assert "CONFIG WARNING" in capsys.readouterr().out
    with pytest.warns(FutureWarning):
        assert main(["edge", "status", cfg, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["config_warnings"]
