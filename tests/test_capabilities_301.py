"""0.111 (#301): store and data errors are tool errors a model can act on, not internal errors.

- a date the calendar does not have is ``invalid_params``; a window outside the stored data is an
  empty, labelled result;
- a damaged or unreadable part file is ``unavailable`` (not retryable) for that facility only,
  and the ``facilities`` listing still shows the others;
- a read of one facility lists only that facility's files, so another facility's directory
  being replaced cannot fail it; a part file that vanishes mid-read is retried once, then
  ``unavailable`` (retryable).

Every test builds a tiny store in its own tmp directory.
"""

import glob
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import (  # noqa: E402
    ERROR_CODES,
    CallContext,
    Capability,
    CapabilityError,
    Registry,
    default_registry,
)
from camber.mcp import McpServer  # noqa: E402
from camber.mcp._server import TOOL_ERROR_CODES  # noqa: E402
from camber.store.parquet_store import ParquetStore, StoreReadError  # noqa: E402

FID, OTHER = fx.FID, "annex"
REG = default_registry()
MODERN_META = {
    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
    "io.modelcontextprotocol/clientCapabilities": {},
    "io.modelcontextprotocol/clientInfo": {"name": "test", "version": "1"},
}


@pytest.fixture()
def store(tmp_path):
    st = fx.make_store(tmp_path, meta=None)  # data 2026-03-02 .. 2026-03-22
    st.write_role_frame(fx.ahu_frame(seed=3), facility_id=OTHER, equip="AHU-9", equip_class="AHU")
    st.points()  # a current catalog cache, as an ingest leaves it
    return st


def _ctx(st, allowed=(FID, OTHER)):
    return CallContext(store=st.root, allowed_facilities=frozenset(allowed))


def _err(st, name, args, allowed=(FID, OTHER)) -> CapabilityError:
    with pytest.raises(CapabilityError) as ei:
        REG.call(name, args, _ctx(st, allowed))
    return ei.value


def _damage(st, fid, how):
    part = sorted(
        glob.glob(os.path.join(st.root, f"facility_id={fid}", "**", "*.parquet"), recursive=True)
    )[0]
    raw = open(part, "rb").read()
    with open(part, "wb") as fh:
        fh.write({"truncated": raw[: len(raw) // 2], "empty": b"", "garbage": b"x" * 4096}[how])


# --------------------------------------------------------------------------- dates


@pytest.mark.parametrize("name", ["coverage", "sensor_trust", "rule_run", "rcx_report"])
@pytest.mark.parametrize("bad", ["2025-02-30", "2025-13-45", "2025-01-01T24:00", "2025-00-10"])
def test_an_impossible_date_is_invalid_params(store, name, bad):
    for key in ("start", "end"):
        e = _err(store, name, {"facility_id": FID, key: bad})
        assert e.code == "invalid_params", (name, key, bad, e.code, e.message)


def test_coverage_with_a_window_after_the_data_is_an_empty_labelled_result(store):
    for args, win in (
        ({"start": "2030-01-01"}, {"start": "2030-01-01T00:00:00", "end": None}),
        ({"end": "2020-01-01"}, {"start": None, "end": "2020-01-01T00:00:00"}),
    ):
        res = REG.call("coverage", {"facility_id": FID, **args}, _ctx(store))
        assert res.data["coverage"] == 0.0 and res.data["n_points"] > 0
        assert res.data["window"] == win and res.data["gaps"] == []
        assert any("No valid stored sample" in b for b in res.banners)
    # a window inside the data has no such banner
    res = REG.call("coverage", {"facility_id": FID, "start": "2026-03-03"}, _ctx(store))
    assert not any("No valid stored sample" in b for b in res.banners)


def test_sensor_trust_with_a_window_after_the_data_says_so(store):
    res = REG.call("sensor_trust", {"facility_id": FID, "start": "2030-01-01"}, _ctx(store))
    assert res.data["n_points"] == 0
    assert any("falls in the requested window" in b for b in res.banners)


# --------------------------------------------------------------------------- damaged files


@pytest.mark.parametrize("how", ["truncated", "empty", "garbage"])
def test_a_damaged_part_file_is_unavailable_for_that_facility_only(store, how):
    _damage(store, FID, how)
    for name in ("coverage", "sensor_trust", "rule_run", "rcx_report"):
        e = _err(store, name, {"facility_id": FID})
        assert e.code == "unavailable", (name, e.code, e.message)
        assert e.data == {"retryable": False, "facility_id": FID}
        assert "damaged or unreadable" in e.message and os.sep + "facility_id" not in e.message
    res = REG.call("coverage", {"facility_id": OTHER}, _ctx(store))  # the other one still reads
    assert res.data["n_points"] > 0
    listing = REG.call("facilities", {}, _ctx(store)).data["facilities"]
    by_id = {r["facility_id"]: r for r in listing}
    assert set(by_id) == {FID, OTHER}
    assert by_id[FID]["unavailable"] == {"retryable": False} and by_id[FID]["start"] is None
    assert "unavailable" not in by_id[OTHER] and by_id[OTHER]["start"]


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0, reason="root reads anything")
def test_an_unreadable_facility_directory_is_unavailable(store):
    fdir = os.path.join(store.root, f"facility_id={FID}")
    os.chmod(fdir, 0)
    try:
        e = _err(store, "coverage", {"facility_id": FID})
        assert e.code == "unavailable" and e.data["facility_id"] == FID
        # a read of another facility never lists this directory (#301)
        assert REG.call("coverage", {"facility_id": OTHER}, _ctx(store)).data["n_points"] > 0
        assert REG.call("points", {"facility_id": OTHER}, _ctx(store)).data["points"]
    finally:
        os.chmod(fdir, 0o755)


def test_the_error_names_no_facility_outside_the_allowlist(store):
    _damage(store, FID, "garbage")
    store._invalidate_catalog()  # the next points() scans every facility, the damaged one too
    e = _err(store, "points", {"facility_id": OTHER}, allowed=(OTHER,))
    assert e.code == "unavailable"
    assert FID not in e.message and "facility_id" not in (e.data or {})


# --------------------------------------------------------------------------- concurrent ingest


def test_a_part_file_that_vanishes_mid_read_is_retried_once(store, monkeypatch):
    real, calls = ParquetStore._read_long, []

    def flaky(self, *a, **kw):
        calls.append(1)
        if len(calls) == 1:
            raise FileNotFoundError(2, "Failed to open local file")
        return real(self, *a, **kw)

    monkeypatch.setattr(ParquetStore, "_read_long", flaky)
    assert not ParquetStore(store.root).read_long(facility_id=FID, columns=["ts"]).empty
    assert len(calls) == 2


def test_a_part_file_still_missing_after_the_retry_is_retryable_unavailable(store, monkeypatch):
    def gone(self, *a, **kw):
        raise FileNotFoundError(2, "Cannot list directory")

    monkeypatch.setattr(ParquetStore, "_read_long", gone)
    with pytest.raises(StoreReadError) as ei:
        ParquetStore(store.root).read_long(facility_id=FID, columns=["ts"])
    assert ei.value.retryable and ei.value.facility_id == FID
    assert isinstance(ei.value, OSError)
    e = _err(store, "coverage", {"facility_id": FID})
    assert e.code == "unavailable" and e.data == {"retryable": True, "facility_id": FID}
    assert "retry the call" in e.message


def test_a_read_lists_only_its_own_facility(store, monkeypatch):
    """A full-store discovery is what a concurrent replacement of another facility broke (#301):
    a read scoped to one facility never runs it, and the catalog scan runs it per facility."""
    import pyarrow.dataset as ds

    real = ds.dataset
    seen = []

    def spy(source, *a, **kw):
        seen.append(os.path.basename(os.path.normpath(str(source))))
        return real(source, *a, **kw)

    monkeypatch.setattr(ds, "dataset", spy)
    st = ParquetStore(store.root)
    st._invalidate_catalog()
    REG.call("coverage", {"facility_id": OTHER}, _ctx(store))
    REG.call("sensor_trust", {"facility_id": OTHER}, _ctx(store))
    assert seen and all(s.startswith("facility_id=") for s in seen), seen
    assert os.path.basename(store.root) not in seen


def test_a_numeric_facility_id_reads_as_a_string(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    st.write_role_frame(fx.ahu_frame(), facility_id="123", equip="A1", equip_class="AHU")
    st._invalidate_catalog()
    assert {k.facility_id for k in st.points()} == {"123"}
    assert not st.read_role_frame(facility_id="123", equip="A1").empty
    assert not st.read_long(facility_id="123", columns=["ts"]).empty


def test_a_facility_removed_mid_call_reads_as_empty(store):
    st = ParquetStore(store.root)
    os.rename(
        os.path.join(store.root, f"facility_id={FID}"), os.path.join(store.root, "_gone-demo")
    )
    out = st.read_long(facility_id=FID, columns=["ts"])
    assert out.empty


# --------------------------------------------------------------------------- the error path


def test_unavailable_is_a_documented_tool_error_code():
    assert "unavailable" in ERROR_CODES and "unavailable" in TOOL_ERROR_CODES


def test_an_os_error_in_a_handler_is_unavailable_not_internal(tmp_path):
    def handler(ctx, args):
        raise PermissionError(13, "Permission denied", str(tmp_path / "x"))

    reg = Registry()
    reg.register(
        Capability("io", "IO", "io", {"type": "object"}, {"type": "object"}, handler=handler)
    )
    ctx = CallContext(store=str(tmp_path), allowed_facilities=frozenset())
    with pytest.raises(CapabilityError) as ei:
        reg.call("io", {}, ctx)
    assert ei.value.code == "unavailable" and ei.value.data == {"retryable": False}
    assert str(tmp_path) not in ei.value.message


def test_the_mcp_server_answers_unavailable_as_an_is_error_tool_result(store):
    _damage(store, FID, "truncated")
    server = McpServer(REG, _ctx(store), server_version="TEST")
    for name in ("coverage", "facilities"):
        resp = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": name,
                    "arguments": {"facility_id": FID} if name != "facilities" else {},
                    "_meta": MODERN_META,
                },
            }
        )
        assert "error" not in resp, resp
        if name == "coverage":
            assert resp["result"]["isError"] is True
            assert resp["result"]["_meta"]["camber/error"] == {
                "code": "unavailable",
                "data": {"retryable": False, "facility_id": FID},
            }
            assert resp["result"]["content"][0]["text"].startswith("unavailable: ")
        else:
            assert not resp["result"].get("isError")
