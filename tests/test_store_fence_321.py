"""#321: edges of the store's write fence and the CLI's start.

1. A fence naming a pid no process can have (larger than ``pid_t``) blocks no reader, and the
   next writer to take the store lock resets it instead of crashing.
2. A writer killed between a partition swap's two renames (the partition absent, its data in
   ``_swap-*``) leaves readers a retryable ``unavailable``, not "no data"; the next writer to
   take the store lock finishes the swap.
3. A first ingest writes the facility's registry entry inside its fence.
4. Ctrl-C while the CLI starts ends it quietly with exit status 130.

Every test builds a tiny store in its own tmp directory.
"""

import argparse
import os
import shutil
import signal
import subprocess
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402
from test_datasets_ingest import BASE, FakeOpener, _ahu_entry_dict, _zip_bytes  # noqa: E402

import camber.cli as cli  # noqa: E402
from camber.capabilities import CallContext, CapabilityError, default_registry  # noqa: E402
from camber.datasets import _ingest, _ops  # noqa: E402
from camber.datasets._catalog import DatasetEntry  # noqa: E402
from camber.store import _fence, _leftovers  # noqa: E402
from camber.store.facilities import FacilityRegistry  # noqa: E402
from camber.store.parquet_store import ParquetStore, StoreReadError  # noqa: E402

FID = fx.FID
REG = default_registry()
DEAD = "busy 999999999 - 0123456789abcdef\n"  # a pid no process has


@pytest.fixture(autouse=True)
def _short_wait(monkeypatch):
    monkeypatch.setattr(_fence, "_WAIT", 0.05)


@pytest.fixture()
def store(tmp_path):
    st = fx.make_store(tmp_path, meta=None)
    st.points()
    return st


def _ctx(st):
    return CallContext(store=st.root, allowed_facilities=frozenset([FID]))


def _mark(st, text):
    os.makedirs(os.path.join(st.root, "_fence"), exist_ok=True)
    with open(os.path.join(st.root, "_fence", FID), "w") as fh:
        fh.write(text)


def _fence_text(root, fid=FID):
    with open(os.path.join(root, "_fence", fid)) as fh:
        return fh.read()


# --------------------------------------------------------------------------- 1. oversized pid


@pytest.mark.parametrize("pid", ["2147483648", "99999999999999999999", "0"])
def test_a_fence_naming_an_impossible_pid_blocks_nothing_and_is_reset(store, pid):
    _mark(store, f"busy {pid} 1.000001 0123456789abcdef\n")
    assert REG.call("points", {"facility_id": FID}, _ctx(store)).data
    assert REG.call("facilities", {}, _ctx(store)).data
    assert len(ParquetStore(store.root).read_long(facility_id=FID)) > 0
    with store.lock():  # what `datasets ingest --force` takes first: it must not crash
        pass
    assert _fence_text(store.root).startswith("idle ")


def test_leftovers_with_an_impossible_pid_count_as_gone():
    assert _leftovers._process_alive(2**31) is False
    assert _leftovers._process_alive(10**20) is False
    assert _leftovers._process_alive(os.getpid()) is True


# --------------------------------------------------------------------------- 2. swap gap


def _kill_in_swap_gap(st):
    """The state a writer SIGKILLed between ``<part> -> .old`` and ``.new -> <part>`` leaves."""
    part = os.path.join(st.root, f"facility_id={FID}")
    stem = os.path.join(st.root, f"_swap-facility_id={FID}")
    shutil.copytree(part, stem + ".new")
    open(stem + ".ready", "w").close()
    os.rename(part, stem + ".old")
    _mark(st, DEAD)
    return part, stem


def test_a_writer_killed_in_the_swap_gap_leaves_readers_a_retryable_error(store):
    _kill_in_swap_gap(store)
    for name, args in (("points", {"facility_id": FID}), ("facilities", {})):
        with pytest.raises(CapabilityError) as ei:
            REG.call(name, args, _ctx(store))
        assert ei.value.code == "unavailable", (name, ei.value)
        assert ei.value.data == {"retryable": True, "facility_id": FID}
    with pytest.raises(StoreReadError) as ei:
        ParquetStore(store.root).read_long(facility_id=FID)
    assert ei.value.retryable


def test_the_next_store_lock_finishes_a_swap_its_writer_left_half_done(store):
    before = len(ParquetStore(store.root).read_long(facility_id=FID))
    part, stem = _kill_in_swap_gap(store)
    with store.lock():
        pass
    assert os.path.isdir(part)
    assert not [n for n in os.listdir(store.root) if n.startswith("_swap-")]
    assert _fence_text(store.root).startswith("idle ")
    assert len(ParquetStore(store.root).read_long(facility_id=FID)) == before
    assert REG.call("points", {"facility_id": FID}, _ctx(store)).data


def test_a_swap_gap_without_the_ready_marker_rolls_back(store):
    part = os.path.join(store.root, f"facility_id={FID}")
    stem = os.path.join(store.root, f"_swap-facility_id={FID}")
    os.rename(part, stem + ".old")  # not reachable by commit, but recovery must still be safe
    _mark(store, DEAD)
    with pytest.raises(CapabilityError) as ei:
        REG.call("points", {"facility_id": FID}, _ctx(store))
    assert ei.value.code == "unavailable"
    assert _fence.recover(store.root) == [FID]
    assert os.path.isdir(part) and not os.path.lexists(stem + ".old")
    assert REG.call("points", {"facility_id": FID}, _ctx(store)).data


def test_a_dead_marker_without_swap_leftovers_still_blocks_nothing(store):
    _mark(store, DEAD)
    assert REG.call("points", {"facility_id": FID}, _ctx(store)).data


# --------------------------------------------------------------------------- 3. first ingest


@pytest.fixture
def cat(tmp_path, monkeypatch):
    zb = _zip_bytes()
    entry = DatasetEntry.from_dict(_ahu_entry_dict(zb))
    opener = FakeOpener({BASE + "test.zip": zb})
    cache = str(tmp_path / "cache")
    monkeypatch.setenv("CAMBER_DATA_DIR", cache)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    _ops.fetch_dataset(entry, data_dir=cache, opener=opener)
    return entry, cache, tmp_path


def test_a_first_ingest_registers_its_facility_inside_the_fence(cat, monkeypatch):
    entry, cache, tmp = cat
    store = str(tmp / "plain")
    real = FacilityRegistry.register
    seen: list = []

    def register(self, fid, *a, **kw):
        seen.append((fid, _fence_text(store, fid).split()[0]))
        return real(self, fid, *a, **kw)

    monkeypatch.setattr(FacilityRegistry, "register", register)
    res = _ingest.ingest_dataset(entry, store, data_dir=cache)
    assert seen and all(state == "busy" for _fid, state in seen), seen
    for fid in res.facilities:
        with open(os.path.join(store, "_fence", fid)) as fh:
            assert fh.read().startswith("idle ")


# --------------------------------------------------------------------------- 4. Ctrl-C at start


def _main_running(monkeypatch, func):
    """``cli.main`` with a parser whose only command is ``func``."""

    def build():
        p = argparse.ArgumentParser(prog="camber")
        p.set_defaults(func=func)
        return p

    monkeypatch.setattr(cli, "_build_parser", build)
    return cli.main([])


def test_ctrl_c_inside_the_cli_exits_130_quietly(monkeypatch, capsys):
    def interrupted(args):
        raise KeyboardInterrupt

    assert _main_running(monkeypatch, interrupted) == 130
    out = capsys.readouterr()
    assert out.err == "" and out.out == ""
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_an_import_error_raised_by_ctrl_c_also_exits_130(monkeypatch, capsys):
    def broken_import(args):
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            raise ImportError("Error importing numpy: you should not try ...") from None

    assert _main_running(monkeypatch, broken_import) == 130
    assert capsys.readouterr().err == ""


def test_other_errors_are_not_mistaken_for_ctrl_c(monkeypatch):
    def boom(args):
        raise RuntimeError("not an interrupt")

    with pytest.raises(RuntimeError):
        _main_running(monkeypatch, boom)
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


@pytest.mark.skipif(not hasattr(signal, "SIGINT") or os.name == "nt", reason="POSIX signals")
def test_a_real_sigint_while_the_cli_runs_exits_130_without_a_traceback():
    # the subcommand signals its own process, as a Ctrl-C would, while main() is running
    code = (
        "import os, signal, sys, camber.cli as cli\n"
        "import argparse, time\n"
        "def build():\n"
        "    p = argparse.ArgumentParser()\n"
        "    p.set_defaults(func=lambda a: (os.kill(os.getpid(), signal.SIGINT), time.sleep(30)))\n"
        "    return p\n"
        "cli._build_parser = build\n"
        "raise SystemExit(cli.main([]))\n"
    )
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env = {**os.environ, "PYTHONPATH": root + os.pathsep + os.environ.get("PYTHONPATH", "")}
    r = subprocess.run(
        [sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=120, cwd=root
    )
    assert r.returncode == 130, r.stderr
    assert "Traceback" not in r.stderr and r.stderr == ""
