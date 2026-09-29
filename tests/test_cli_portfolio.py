"""The `camber portfolio` / `camber facility` commands, driven through ``main([...])``."""

import json
import os
import subprocess
import sys
import textwrap

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.cli import main  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.store import ParquetStore, make_facility_id  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _frame(n=48):
    idx = pd.date_range("2025-07-07", periods=n, freq="h")
    return pd.DataFrame({Role.SUPPLY_AIR_TEMP: np.linspace(55, 56, n)}, index=idx)


def _cli(capsys, *argv):
    rc = main(list(argv))
    out = capsys.readouterr()
    return rc, out.out, out.err


def test_cli_portfolio_and_facility_flow(tmp_path, capsys, monkeypatch):
    ws = str(tmp_path / "ws")
    monkeypatch.delenv("CAMBER_PORTFOLIO", raising=False)
    monkeypatch.chdir(tmp_path)
    rc, out, _ = _cli(capsys, "portfolio", "init", ws)
    assert rc == 0 and "created workspace" in out
    rc, out, _ = _cli(capsys, "portfolio", "init", ws)
    assert rc == 0 and "already a workspace" in out
    rc, _, err = _cli(capsys, "facility", "list")
    assert rc == 1 and "no portfolio workspace" in err
    rc, _, err = _cli(capsys, "facility", "list", "--workspace", str(tmp_path))
    assert rc == 1 and "is not a portfolio workspace" in err
    monkeypatch.setenv("CAMBER_PORTFOLIO", ws)
    rc, out, _ = _cli(
        capsys, "facility", "add", "Test Building", "--reason", "onboarding", "--owner", "ops",
        "--tag", "east",
    )  # fmt: skip
    fid = make_facility_id("Test Building")
    assert rc == 0 and f"added {fid}" in out and "provisioning" in out and "activate" in out
    rc, out, _ = _cli(capsys, "facility", "add", "Second", "--id", "sec-1", "--activate",
                      "--reason", "r")  # fmt: skip
    assert rc == 0 and "sec-1 (Second) -- active" in out
    rc, _, err = _cli(capsys, "facility", "suspend", fid, "--reason", "x")
    assert rc == 1 and "allowed from provisioning: activate" in err
    for verb, want in (("activate", "provisioning -> active"), ("suspend", "active -> suspended"),
                       ("resume", "suspended -> active")):  # fmt: skip
        rc, out, _ = _cli(capsys, "facility", verb, fid, "--reason", f"{verb} it")
        assert rc == 0 and want in out
    rc, out, _ = _cli(capsys, "facility", "rename", fid, "Test Bldg", "--reason", "rebrand")
    assert rc == 0 and "'Test Bldg'" in out
    rc, out, _ = _cli(capsys, "facility", "offboard", fid)  # a dry run by default
    assert rc == 0 and "dry run -- nothing changed" in out and "--apply" in out
    rc, _, err = _cli(capsys, "facility", "archive", fid)
    assert rc == 1 and "cannot archive a facility that is active" in err
    rc, out, _ = _cli(capsys, "facility", "list")
    assert rc == 0 and fid in out and "Test Bldg" in out and "2 facilities" in out
    rc, out, _ = _cli(capsys, "facility", "list", "--state", "active", "--json")
    assert rc == 0 and set(json.loads(out)) == {fid, "sec-1"}
    rc, out, _ = _cli(capsys, "facility", "show", fid)
    assert rc == 0 and "allowed actions  : suspend, offboard" in out and "keep_months=25" in out
    assert "reason: rebrand" in out and "'Test Building' -> 'Test Bldg'" in out
    rc, out, _ = _cli(capsys, "facility", "show", fid, "--json")
    assert rc == 0 and json.loads(out)["retention"]["audit"]["source"] == "default"
    rc, _, err = _cli(capsys, "facility", "show", "ghost")
    assert rc == 1 and "unknown facility 'ghost'" in err
    rc, out, _ = _cli(capsys, "portfolio", "status")
    assert rc == 0 and "facilities: 2 (active 2)" in out and "lock      : free" in out
    rc, out, _ = _cli(capsys, "portfolio", "status", "--json")
    assert rc == 0 and json.loads(out)["by_state"]["active"] == 2
    rc, out, _ = _cli(capsys, "portfolio", "audit", "--facility", fid)
    assert rc == 0 and "5 record(s)" in out and "reason: suspend it" in out
    assert "[active -> suspended]" in out
    rc, out, _ = _cli(capsys, "portfolio", "audit", "--json")
    rows = json.loads(out)
    assert rows[0]["action"] == "portfolio.init" and all(r["actor"] for r in rows)


def test_cli_adopt_and_status_lists_unregistered_and_tombstones(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("CAMBER_PORTFOLIO", raising=False)
    store = tmp_path / "lake"
    st = ParquetStore(str(store))
    st.write_role_frame(_frame(), facility_id="bare", equip="AHU_1")
    st.write_role_frame(_frame(), facility_id="gone", equip="AHU_1", name="Gone")
    st.drop_facility("gone", forget=True)
    rc, out, _ = _cli(capsys, "portfolio", "adopt", str(store), "--reason", "take over")
    assert rc == 0 and "nothing moved" in out
    rc, out, _ = _cli(capsys, "portfolio", "status", "--workspace", str(tmp_path))
    assert "unregistered store partitions (read as active): bare" in out
    assert "tombstoned: gone" in out
    rc, _, err = _cli(capsys, "facility", "add", "Gone", "--id", "gone", "--reason", "x",
                      "--workspace", str(tmp_path))  # fmt: skip
    assert rc == 1 and "tombstoned" in err


def test_cli_second_concurrent_change_is_refused_by_the_lock(tmp_path, capsys, monkeypatch):
    ws = str(tmp_path / "ws")
    assert main(["portfolio", "init", ws]) == 0
    monkeypatch.setenv("CAMBER_PORTFOLIO", ws)
    src = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {_REPO!r})
        from camber.portfolio._lock import portfolio_lock
        with portfolio_lock({ws!r}):
            print("held", flush=True)
            sys.stdin.read()
        """
    )
    child = subprocess.Popen(
        [sys.executable, "-c", src], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True
    )
    try:
        assert child.stdout.readline().strip() == "held"
        capsys.readouterr()
        rc, _, err = _cli(capsys, "facility", "add", "Y", "--reason", "r")
        assert rc == 1 and f"portfolio is locked by {child.pid}@" in err and " since " in err
        rc, out, _ = _cli(capsys, "portfolio", "status")
        assert rc == 0 and f"lock      : held by {child.pid}@" in out
    finally:
        child.stdin.close()
        child.wait(timeout=30)
    rc, _, _ = _cli(capsys, "facility", "add", "Y", "--reason", "r")
    assert rc == 0


def test_cli_status_flags_data_left_under_a_tombstoned_id(tmp_path, capsys):
    ws = str(tmp_path / "ws")
    main(["portfolio", "init", ws])
    st = ParquetStore(os.path.join(ws, "store"))
    st.write_role_frame(_frame(), facility_id="old", equip="AHU_1", name="Old")
    st._registry().remove("old", reason="retired")
    capsys.readouterr()
    rc, out, _ = _cli(capsys, "portfolio", "status", "--workspace", ws)
    assert rc == 0 and "data still stored under tombstoned ids" in out and "old" in out
