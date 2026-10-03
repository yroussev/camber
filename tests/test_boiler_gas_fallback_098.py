"""0.98 (#86 item 4a): the boiler firing rules read firing from the gas input when no boiler run
status is mapped (``roles_any_of``). Synthetic only."""

import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.charts.evidence import finding_evidence  # noqa: E402
from camber.model.entities import missing_inputs, runnable_rules  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules._boilerrun import GAS_RUN_CAVEAT, with_boiler_status  # noqa: E402
from camber.rules.boiler_rule import BoilerSummerLockout  # noqa: E402
from camber.rules.boilercycle_rule import BoilerShortCycle  # noqa: E402
from camber.rules.hwplant_deltat_rule import HWPlantDeltaT  # noqa: E402
from camber.rules.online import OnlineFDD  # noqa: E402
from camber.rules.triage import _rule_roles  # noqa: E402

RULES = (BoilerSummerLockout, BoilerShortCycle, HWPlantDeltaT)


def _plant(days=14, *, status=True, gas=True):
    """A boiler that fires 06:00-18:00 on weekdays in cold weather (OAT 30-50 F); 40 F loop dT."""
    idx = pd.date_range("2024-01-01", periods=24 * days, freq="h")
    firing = ((idx.hour >= 6) & (idx.hour < 18) & (idx.dayofweek < 5)).astype(float)
    cols = {
        Role.OAT: 40.0 + 10.0 * np.sin(np.arange(len(idx)) / 24.0),
        Role.HW_SUPPLY_TEMP: np.where(firing > 0, 170.0, 120.0),
        Role.HW_RETURN_TEMP: np.where(firing > 0, 130.0, 118.0),
    }
    if status:
        cols[Role.BOILER_STATUS] = firing
    if gas:
        cols[Role.GAS_INPUT_RATE] = firing * 400.0 + 2.0  # a pilot's worth when idle
    return pd.DataFrame(cols, index=idx)


@pytest.mark.parametrize("cls", RULES)
def test_rules_declare_the_any_of_group(cls):
    rule = cls()
    assert Role.BOILER_STATUS not in rule.roles_required
    assert rule.roles_any_of == ((Role.BOILER_STATUS, Role.GAS_INPUT_RATE),)
    req, groups = missing_inputs(
        rule, {Role.GAS_INPUT_RATE, Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP}
    )
    assert not req and not groups
    req, groups = missing_inputs(rule, {Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP})
    assert groups == [(Role.BOILER_STATUS, Role.GAS_INPUT_RATE)]
    (r,) = runnable_rules({Role.GAS_INPUT_RATE, Role.HW_SUPPLY_TEMP, Role.HW_RETURN_TEMP}, [rule])
    assert r.can_run


@pytest.mark.parametrize("cls", RULES)
def test_a_status_frame_is_unchanged_by_a_gas_column(cls):
    """With a run status the gas input is ignored: same finding, no run_source, no caveat."""
    rule = cls()
    with_gas = rule.analyze("B1", _plant(status=True, gas=True)).as_dict()
    without = rule.analyze("B1", _plant(status=True, gas=False)).as_dict()
    assert with_gas == without
    assert "run_source" not in with_gas["metrics"]
    assert GAS_RUN_CAVEAT not in with_gas["caveats"]


@pytest.mark.parametrize("cls", RULES)
def test_gas_only_frame_reads_firing_from_the_gas(cls):
    rule = cls()
    gas = rule.analyze("B1", _plant(status=False, gas=True))
    status = rule.analyze("B1", _plant(status=True, gas=False))
    assert gas.severity == status.severity == "ok"
    assert gas.metrics["run_source"] == "gas"
    assert GAS_RUN_CAVEAT in gas.caveats
    same = {k: v for k, v in gas.metrics.items() if k != "run_source"}
    assert same == status.metrics  # the gas gate recovers the same firing hours here


def test_short_cycle_counts_starts_from_the_gas():
    f = BoilerShortCycle().analyze("B1", _plant(days=14, status=False))
    assert f.metrics["n_starts"] == 10  # one start per weekday
    assert "gas input" in f.caveats[0]


def test_a_gas_gap_is_missing_not_a_stop():
    """Samples with no gas reading stay missing, so a trend gap never reads as a restart."""
    fr = _plant(days=14, status=False)
    fr.loc[fr.index[8:10], Role.GAS_INPUT_RATE] = np.nan  # a gap mid-firing on day 1
    out, src = with_boiler_status(fr)
    assert src == "gas"
    assert out[Role.BOILER_STATUS].iloc[8:10].isna().all()
    assert BoilerShortCycle().analyze("B1", fr).metrics["n_starts"] == 10


def test_helper_leaves_frames_it_cannot_help_alone():
    fr = _plant(status=True)
    assert with_boiler_status(fr) == (fr, None)
    bare = _plant(status=False, gas=False)
    out, src = with_boiler_status(bare)
    assert out is bare and src is None
    flat = _plant(status=False)
    flat[Role.GAS_INPUT_RATE] = 0.0  # never fires: no 95th percentile to read against
    out, src = with_boiler_status(flat)
    assert out is flat and src is None
    # an all-missing status column counts as no status
    empty = _plant(status=False)
    empty[Role.BOILER_STATUS] = np.nan
    assert with_boiler_status(empty)[1] == "gas"


def test_neither_input_still_declines():
    f = BoilerShortCycle().analyze("B1", _plant(status=False, gas=False))
    assert f.severity == "info" and "run_source" not in f.metrics


def test_online_monitor_honours_the_any_of_group():
    fr = _plant(days=3, status=False)
    mon = OnlineFDD(rules=[BoilerShortCycle()], window=72, eval_every=72, emit_ok=True)
    out = mon.extend("B1", fr)
    assert [t.rule for t in out] == ["boiler_short_cycle"]
    bare = OnlineFDD(rules=[BoilerShortCycle()], window=72, eval_every=72, emit_ok=True)
    assert bare.extend("B1", fr.drop(columns=[Role.GAS_INPUT_RATE])) == []


def test_evidence_and_triage_see_the_gas_input():
    rule = BoilerShortCycle()
    ev = finding_evidence(rule, "B1", _plant(status=False))
    assert ev is not None and ev.roles == [Role.GAS_INPUT_RATE]
    assert "gas_input_rate" in _rule_roles({rule.name: rule}, rule.name)
