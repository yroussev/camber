"""#322: the edges the 0.111 acceptance test left open.

1. A fence whose pid field is too long for ``int()`` (4,301 digits or more), or is not digits,
   is malformed: it blocks nothing and the next writer resets it.
2. A facility stuck in a killed writer's swap gap does not take the ``facilities`` listing down:
   the listing lists the healthy facilities and marks the stuck one ``unavailable``.
4. A Ctrl-C while ``camber.cli`` loads, or one an extension's import swallowed, ends the command
   quietly with exit status 130.
5. ``camber --version``.

Every test builds a tiny store in its own tmp directory.
"""

import argparse
import os
import shutil
import signal
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

import camber  # noqa: E402
import camber.__main__ as entry  # noqa: E402
import camber.cli as cli  # noqa: E402
from camber.capabilities import CallContext, CapabilityError, default_registry  # noqa: E402
from camber.mcp import _launch  # noqa: E402
from camber.store import _fence  # noqa: E402

FID = fx.FID
OTHER = "other-fac"
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


def _ctx(st, *fids):
    return CallContext(store=st.root, allowed_facilities=frozenset(fids or [FID]))


def _mark(st, text, fid=FID):
    os.makedirs(os.path.join(st.root, "_fence"), exist_ok=True)
    with open(os.path.join(st.root, "_fence", fid), "w", encoding="utf-8") as fh:
        fh.write(text)


def _fence_text(root, fid=FID):
    with open(os.path.join(root, "_fence", fid), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------------------- 1. long pid


@pytest.mark.parametrize("pid", ["9" * 4301, "9" * 100_000, "1" * 11, "+5", "05x", "12a"])
def test_a_fence_pid_too_long_or_not_digits_blocks_nothing_and_is_reset(store, pid):
    _mark(store, f"busy {pid} 1.000001 0123456789abcdef\n")
    assert REG.call("points", {"facility_id": FID}, _ctx(store)).data
    listing = REG.call("facilities", {}, _ctx(store)).data["facilities"]
    assert [r["facility_id"] for r in listing] == [FID] and "unavailable" not in listing[0]
    with store.lock():  # what `datasets ingest --force` takes first: it must not crash
        pass
    assert _fence_text(store.root).startswith("idle ")


def test_a_live_pid_of_up_to_ten_digits_is_still_read(store):
    # the largest pid_t has 10 digits: a live writer's fence must keep blocking readers
    live = os.getppid()
    assert _fence._busy(store.root, FID, ["busy", f"{live:010d}", "-", "t"]) is True
    assert _fence._busy(store.root, FID, ["busy", f"{live:011d}", "-", "t"]) is False


# --------------------------------------------------------------------------- 2. listing


def _kill_in_swap_gap(st, fid):
    """The state a writer SIGKILLed between ``<part> -> .old`` and ``.new -> <part>`` leaves."""
    part = os.path.join(st.root, f"facility_id={fid}")
    stem = os.path.join(st.root, f"_swap-facility_id={fid}")
    shutil.copytree(part, stem + ".new")
    open(stem + ".ready", "w").close()
    os.rename(part, stem + ".old")
    _mark(st, DEAD, fid)


def _add_other(st):
    """A second, unregistered facility."""
    st.write_role_frame(
        fx.ahu_frame(seed=3), facility_id=OTHER, equip="OtherAHU", equip_class="AHU"
    )
    st.points()


def test_the_listing_lists_the_healthy_facilities_and_marks_one_stuck_in_its_swap(store):
    _add_other(store)
    _kill_in_swap_gap(store, FID)
    ctx = _ctx(store, FID, OTHER)
    res = REG.call("facilities", {}, ctx)
    rows = {r["facility_id"]: r for r in res.data["facilities"]}
    assert set(rows) == {FID, OTHER} and res.data["count"] == 2
    stuck = rows[FID]
    assert stuck["unavailable"] == {"retryable": True, "reason": "being_replaced"}
    assert (stuck["start"], stuck["end"], stuck["n_points"], stuck["n_equipment"]) == (
        None,
        None,
        0,
        0,
    )
    assert "unavailable" not in rows[OTHER] and rows[OTHER]["n_points"] > 0
    assert any("being replaced" in b and "retry" in b for b in res.banners)
    assert f"{FID} is active, its stored data cannot be read right now" in res.text
    assert "0 points" not in res.text
    # the stuck facility's own calls keep their retryable error; the other's work
    with pytest.raises(CapabilityError) as ei:
        REG.call("points", {"facility_id": FID}, ctx)
    assert ei.value.code == "unavailable"
    assert ei.value.data == {"retryable": True, "facility_id": FID}
    assert REG.call("points", {"facility_id": OTHER}, ctx).data
    # the next writer finishes the swap, and the listing is whole again
    with store.lock():
        pass
    rows = REG.call("facilities", {}, ctx).data["facilities"]
    assert all("unavailable" not in r and r["n_points"] > 0 for r in rows)


def test_an_unregistered_facility_stuck_in_its_swap_is_still_listed(store):
    _add_other(store)
    _kill_in_swap_gap(store, OTHER)  # no directory and no registry entry: only the swap's files
    rows = REG.call("facilities", {}, _ctx(store, FID, OTHER)).data["facilities"]
    assert [r["facility_id"] for r in rows] == [FID, OTHER]
    assert rows[1]["unavailable"]["reason"] == "being_replaced"
    assert "unavailable" not in rows[0]


def test_a_live_writer_on_one_facility_is_waited_for_once(store, monkeypatch):
    _add_other(store)
    _mark(store, f"busy {os.getpid()} - 0123456789abcdef\n")  # this process "holds" it
    monkeypatch.setattr(_fence, "_held_elsewhere", lambda root, fid: True)
    rows = REG.call("facilities", {}, _ctx(store, FID, OTHER)).data["facilities"]
    assert rows[0]["unavailable"] == {"retryable": True, "reason": "being_replaced"}
    assert "unavailable" not in rows[1]


# --------------------------------------------------------------------------- 4. Ctrl-C


class _Interrupting(types.ModuleType):
    """A ``camber.cli`` whose import a Ctrl-C interrupts."""

    def __getattr__(self, name):
        raise KeyboardInterrupt


def test_ctrl_c_while_the_cli_loads_exits_130_quietly(monkeypatch, capsys):
    monkeypatch.setitem(sys.modules, "camber.cli", _Interrupting("camber.cli"))
    assert entry.main([]) == 130
    assert capsys.readouterr().err == ""


def test_the_console_script_runs_the_cli(monkeypatch):
    seen = []
    monkeypatch.setattr(cli, "main", lambda argv=None: seen.append(argv) or 7)
    assert entry.main(["x"]) == 7 and seen == [["x"]]


def _main_running(monkeypatch, func):
    def build():
        p = argparse.ArgumentParser(prog="camber")
        p.set_defaults(func=func)
        return p

    monkeypatch.setattr(cli, "_build_parser", build)
    return cli.main([])


def test_a_ctrl_c_an_import_printed_and_swallowed_still_exits_130_quietly(monkeypatch, capsys):
    hooks = (sys.excepthook, sys.unraisablehook)

    def swallowed(args):
        # what an extension module's init does with an interrupted import: print it, go on,
        # and fail later with another error
        sys.excepthook(KeyboardInterrupt, KeyboardInterrupt(), None)
        raise ImportError("PyCapsule_Import could not import module")

    assert _main_running(monkeypatch, swallowed) == 130
    assert capsys.readouterr().err == ""
    assert (sys.excepthook, sys.unraisablehook) == hooks


def test_other_errors_still_reach_the_hooks(monkeypatch):
    got = []
    monkeypatch.setattr(sys, "excepthook", lambda *a: got.append(a[0]))

    def prints(args):
        sys.excepthook(ValueError, ValueError("x"), None)
        return 0

    assert _main_running(monkeypatch, prints) == 0
    assert got == [ValueError]


@pytest.mark.skipif(not hasattr(signal, "SIGINT") or os.name == "nt", reason="POSIX signals")
def test_the_mcp_handler_notes_ctrl_c_for_the_cli_and_for_itself(monkeypatch):
    noted = []

    def cli_handler(signum, frame):
        noted.append(signum)
        raise KeyboardInterrupt

    old = signal.signal(signal.SIGINT, cli_handler)
    previous = None
    try:
        _launch._SIGNALLED.clear()
        previous = _launch._on_signals()
        with pytest.raises(KeyboardInterrupt):
            signal.raise_signal(signal.SIGINT)
        assert noted == [signal.SIGINT] and _launch._SIGNALLED == [signal.SIGINT]
        with pytest.raises(KeyboardInterrupt):
            _launch._raise_if_signalled()
    finally:
        _launch._restore_signals(previous)
        signal.signal(signal.SIGINT, old)
        _launch._SIGNALLED.clear()


def test_a_ctrl_c_swallowed_while_loading_starts_no_session(store, monkeypatch):
    def swallowed_ctrl_c():
        _launch._SIGNALLED.append(signal.SIGINT)  # the handler ran; its exception was lost
        return None

    monkeypatch.setattr(_launch, "isolate_matplotlib", swallowed_ctrl_c)
    monkeypatch.setattr(
        _launch, "serve_stdio", lambda *a, **k: pytest.fail("a session started after Ctrl-C")
    )
    assert _launch.launch(store.root, allow=[FID]) == 130


# --------------------------------------------------------------------------- 5. --version


def test_version(capsys):
    with pytest.raises(SystemExit) as ei:
        cli.main(["--version"])
    assert ei.value.code == 0
    assert capsys.readouterr().out.strip() == f"camber {camber.__version__}"
