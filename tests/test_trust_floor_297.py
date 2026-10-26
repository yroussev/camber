"""0.111 (#297): the trust floor must not silently withhold true plant faults through MCP.

A plant point trended only while its equipment runs (blank while off) is judged on its running
samples, a tower is gated on its condenser-water flow, a rule's trust gate reads the run signals
its own frame does not load, and a declined check is announced. The agreement test runs every
labelled synthetic catalog entry (generated locally, no network) through the CLI path and through
the capabilities at the default trust floor and expects the same findings, issues and scores.
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber import datasets as ds  # noqa: E402
from camber.capabilities import CallContext, default_registry  # noqa: E402
from camber.capabilities._common import trust_banner, trust_declines  # noqa: E402
from camber.config import run_config  # noqa: E402
from camber.datasets._ops import build_config, score_dataset  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.report.rcx import RcxOptions, build_rcx_report  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.sensorhealth import plant_gates, sensor_trust, untrusted_roles  # noqa: E402

IDX = pd.date_range("2025-07-07", periods=14 * 96, freq="15min")


def _running() -> pd.Series:
    """A plant that runs 08:00-17:00 every day (37.5 % of the samples)."""
    h = IDX.hour
    return pd.Series((h >= 8) & (h < 17), index=IDX)


def _cw_temp(run: pd.Series) -> pd.Series:
    rng = np.random.default_rng(297)
    v = 75.0 + 3.0 * np.sin(np.arange(len(IDX)) / 40.0) + rng.normal(0, 0.3, len(IDX))
    return pd.Series(np.where(run, v, np.nan), index=IDX)


# ----------------------------------------------------------------- run-conditional coverage


def test_a_point_logged_only_while_running_is_trusted():
    run = _running()
    t = sensor_trust(_cw_temp(run), Role.CW_SUPPLY_TEMP, run_gate=run, run_gate_source="test")
    assert t.coverage < 0.6  # the whole-span figure is unchanged
    assert t.run_coverage == 1.0
    assert t.trust >= 0.9 and t.verdict == "trusted"
    assert "low_coverage" not in t.flags and "logged_while_running" in t.flags


def test_a_point_missing_while_running_is_still_low_coverage():
    run = _running()
    s = _cw_temp(run)
    s[(IDX.day % 2 == 0)] = np.nan  # every other day lost, running or not
    t = sensor_trust(s, Role.CW_SUPPLY_TEMP, run_gate=run)
    assert t.run_coverage < 0.6 and "low_coverage" in t.flags and t.trust < 0.6


def test_ungated_points_keep_whole_span_coverage():
    t = sensor_trust(_cw_temp(_running()), Role.CW_SUPPLY_TEMP)
    assert t.run_coverage is None and "low_coverage" in t.flags and t.trust < 0.6


def test_a_tower_is_gated_on_its_condenser_water_flow():
    run = _running()
    frame = pd.DataFrame(
        {
            Role.CW_SUPPLY_TEMP: _cw_temp(run),
            Role.CW_FLOW: np.where(run, 600.0, 0.0),
            Role.TOWER_FAN_SPEED: np.where(run, 60.0, 0.0),
        },
        index=IDX,
    )
    pg = plant_gates(frame)
    assert pg[Role.CW_SUPPLY_TEMP][1] == "condenser-water flow"
    assert untrusted_roles(frame, [Role.CW_SUPPLY_TEMP]) == []
    # without a run signal the same point reads as missing most of the time
    assert untrusted_roles(frame.drop(columns=[Role.CW_FLOW]), [Role.CW_SUPPLY_TEMP]) == [
        Role.CW_SUPPLY_TEMP
    ]


def test_the_gate_frame_supplies_a_run_signal_the_rule_frame_lacks():
    run = _running()
    frame = pd.DataFrame({Role.CW_SUPPLY_TEMP: _cw_temp(run)}, index=IDX)
    gate = frame.join(pd.DataFrame({Role.COMPRESSOR_STATUS: run.astype(float)}, index=IDX))
    assert untrusted_roles(frame, [Role.CW_SUPPLY_TEMP]) == [Role.CW_SUPPLY_TEMP]
    assert untrusted_roles(frame, [Role.CW_SUPPLY_TEMP], gate_frame=gate) == []


# ----------------------------------------------------------------------------- the notice


def test_trust_declines_and_banner():
    ok = Finding(rule="r", equip="A", severity="fault", metrics={}, summary="")
    bad = Finding(
        rule="cooling_tower_approach",
        equip="T1",
        severity="info",
        metrics={"declined": True, "min_trust": 0.5, "untrusted_roles": ["cw_supply_temp"]},
        summary="T1: declined -- untrusted input(s): cw_supply_temp",
    )
    empty = Finding(rule="d", equip="B", severity="info", metrics={"declined": True}, summary="")
    declines, floor = trust_declines([ok, bad, empty])
    assert declines == [("cooling_tower_approach", "T1", ["cw_supply_temp"])] and floor == 0.5
    text = trust_banner(declines, floor)
    assert text.startswith("1 check(s) declined") and "cooling_tower_approach on 1" in text
    assert "cw_supply_temp" in text and "0.5" in text
    assert trust_banner([], 0.5) is None


# --------------------------------------------- CLI and capabilities agree on every labelled set

_IDS = [e.id for e in ds.catalog(kind="synthetic") if e.labeled_faults]
_FW = ("fault", "warn")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("trust297")
    store, cache = str(root / "store"), str(root / "cache")
    out = {}
    for did in _IDS:
        ds.ingest(did, store, data_dir=cache)
        path = str(root / f"{did}.json")
        build_config(ds.get(did), store, out=path)
        out[did] = path
    return store, out


def _ctx(store, did, cfg, **kw):
    fid = ds.get(did).ingest["facility"]
    return fid, CallContext(store=store, allowed_facilities=frozenset([fid]), config=cfg, **kw)


@pytest.mark.parametrize("did", _IDS)
def test_capabilities_agree_with_the_cli_at_the_default_floor(did, built):
    store, cfgs = built
    with open(cfgs[did], encoding="utf-8") as fh:
        cfg = json.load(fh)
    base = os.path.dirname(cfgs[did])
    res = run_config(cfg, base_dir=base, persist=False)  # the CLI: no trust gate
    drift_rules = {
        f.rule for fam in (res.drift.families if res.drift else []) for f in fam.findings
    }
    cli_rules = {(f.equip, f.rule, f.severity) for f in res.findings if f.severity in _FW}
    cli_drift = {x for x in cli_rules if x[1] in drift_rules}
    cli_rules -= cli_drift
    cli_issues = sorted(
        i["key"]
        for i in build_rcx_report(
            res, options=RcxOptions.from_config(cfg.get("report"), base_dir=base)
        ).to_dict()["issues"]
    )

    fid, ctx = _ctx(store, did, cfgs[did])
    assert ctx.min_trust == 0.5
    reg = default_registry()
    # every row, not the default page (#299)
    rr = reg.call("rule_run", {"facility_id": fid, "limit": 1000}, ctx)
    got = {
        (f["equip"], f["rule"], f["severity"]) for f in rr.data["findings"] if f["severity"] in _FW
    }
    assert got == cli_rules
    assert not any("declined because an input scored below" in b for b in rr.banners)
    rcx = reg.call("rcx_report", {"facility_id": fid, "top_n": 200}, ctx)
    assert sorted(i["key"] for i in rcx.data["issues"]) == cli_issues
    if cfg.get("drift"):
        dr = reg.call("drift", {"facility_id": fid, "limit": 1000}, ctx)
        got = {(f["equip"], f["rule"], f["severity"]) for f in dr.data["findings"]}
        assert {x for x in got if x[2] in _FW} == cli_drift

    # and `camber datasets score` scores the floor-gated run the same as its own
    from camber.capabilities import run_config_for

    entry = ds.get(did)
    gated = run_config_for(ctx, cfg, base_dir=base).findings
    want = score_dataset(entry, store, findings=res.findings)  # what `camber datasets score` runs
    have = score_dataset(entry, store, findings=gated)
    assert have["overall"] == want["overall"] and have["per_detector"] == want["per_detector"]


def test_a_floor_that_declines_says_so(built):
    store, cfgs = built
    did = "synthetic-chw-plant-multi"
    fid, ctx = _ctx(store, did, cfgs[did], min_trust=0.995)
    reg = default_registry()
    for name in ("rule_run", "rcx_report", "drift"):
        res = reg.call(name, {"facility_id": fid}, ctx)
        notice = [b for b in res.banners if "declined because an input scored below" in b]
        assert len(notice) == 1, (name, res.banners)
        n = int(notice[0].split(" ", 1)[0])
        assert n > 0 and "0.995" in notice[0] and "not a pass" in notice[0]
        assert "declined" in res.text and str(n) in res.facts[0]["text"]
