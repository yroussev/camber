"""0.98 (#85 item 3): reheat valve position vs demand, and fan heat on fan-powered boxes.

``reheat_penalty`` and ``overcooling_min_flow`` read the measured position
(``HEAT_VALVE_POSITION``) when it is mapped beside the demand (``HEAT_VALVE``), caveat a demand
that calls for full heat while the position reads shut, and ``reheat_penalty`` can allow for the
box fan's own lift (``fan_heat_f``: a number of °F or ``"auto"``).
"""

import json
import os
import sys

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.interop.haystack_semantic import role_from_tags  # noqa: E402
from camber.interop.semantic223 import ROLE_TO_223  # noqa: E402
from camber.model.roles import HAYSTACK_HINT, Role  # noqa: E402
from camber.rules.builtin import make_rule  # noqa: E402
from camber.rules.overcooling_rule import OvercoolingMinFlow  # noqa: E402
from camber.rules.reheat_rule import ReheatPenalty  # noqa: E402
from camber.sensorhealth import PHYSICAL_BOUNDS  # noqa: E402
from camber.units import PERCENT_ROLES  # noqa: E402

_STUCK = "stuck or failed valve"


def _box(open_rise=6.0, closed_rise=6.0, *, position=None, n_days=14, entering=True):
    """An hourly box: on occupied hours (weekdays 07-18) the demand is 100 % before noon, 0 % after.

    Discharge = entering primary air (55 F) + ``open_rise`` when the demand is 100 %, else
    + ``closed_rise``. ``position``: None (demand only), ``"same"`` (it follows the demand) or
    ``"shut"`` (always 0 %).
    """
    idx = pd.date_range("2025-01-06", periods=n_days * 24, freq="1h")  # a Monday
    occ = (idx.dayofweek < 5) & (idx.hour >= 7) & (idx.hour < 18)
    demand = np.where(occ & (idx.hour < 12), 100.0, 0.0)
    frame = pd.DataFrame(
        {
            Role.HEAT_VALVE: demand,
            Role.SUPPLY_AIR_TEMP: 55.0 + np.where(demand > 0, open_rise, closed_rise),
        },
        index=idx,
    )
    if entering:
        frame[Role.MIXED_AIR_TEMP] = 55.0
    if position == "same":
        frame[Role.HEAT_VALVE_POSITION] = demand
    elif position == "shut":
        frame[Role.HEAT_VALVE_POSITION] = 0.0
    return frame


# --------------------------------------------------------------------------- the role


def test_the_position_role_is_registered_everywhere_a_valve_is():
    r = Role.HEAT_VALVE_POSITION
    assert r.value == "heat_valve_position"
    assert r in PERCENT_ROLES and PHYSICAL_BOUNDS[r] == PHYSICAL_BOUNDS[Role.HEAT_VALVE]
    assert ROLE_TO_223[r] == ROLE_TO_223[Role.HEAT_VALVE]
    # Haystack: the command keeps its tags, the measured position gets the sensor ones
    assert role_from_tags(HAYSTACK_HINT[r]) is r
    assert role_from_tags("heating valve cmd") is Role.HEAT_VALVE


def test_lbnl_fpu_maps_the_position_beside_the_demand():
    from importlib.resources import files

    m = json.loads(files("camber.datasets").joinpath("mappings/lbnl_fpu.json").read_text("utf-8"))
    assert m["aliases"]["RH_VLV_DM_S"] == "heat_valve"  # the drift and capacity rules' signal
    assert m["aliases"]["RH_VLV_S"] == "heat_valve_position"


# --------------------------------------------------------------------------- reheat_penalty


def test_a_valve_stuck_shut_is_not_a_reheat_penalty_when_the_position_is_mapped():
    by_demand = ReheatPenalty().analyze("B", _box())
    assert by_demand.severity == "fault" and by_demand.metrics["valve_signal"] == "demand"
    assert by_demand.metrics["valve_divergence_share"] is None

    f = ReheatPenalty().analyze("B", _box(position="shut"))
    assert f.metrics["valve_signal"] == "position"
    assert f.metrics["valve_open_pct"] == 0.0 and f.severity == "ok"
    assert f.metrics["valve_divergence_share"] == 1.0
    assert any(_STUCK in c for c in f.caveats)
    assert "(measured position)" in f.summary


def test_a_position_that_follows_the_demand_changes_nothing():
    a = ReheatPenalty().analyze("B", _box(open_rise=20.0))
    b = ReheatPenalty().analyze("B", _box(open_rise=20.0, position="same"))
    assert b.metrics["valve_signal"] == "position" and b.metrics["valve_divergence_share"] == 0.0
    assert a.severity == b.severity
    assert a.metrics["valve_open_pct"] == b.metrics["valve_open_pct"]
    assert not any(_STUCK in c for c in b.caveats)


def test_the_divergence_needs_enough_full_demand_samples():
    fr = _box(position="shut", n_days=1)  # five full-demand hours: too few to judge
    f = ReheatPenalty().analyze("B", fr)
    assert f.metrics["valve_divergence_share"] is None
    assert not any(_STUCK in c for c in f.caveats)


def test_an_empty_position_column_falls_back_to_the_demand():
    fr = _box()
    fr[Role.HEAT_VALVE_POSITION] = np.nan
    f = ReheatPenalty().analyze("B", fr)
    assert f.metrics["valve_signal"] == "demand" and f.severity == "fault"


def test_a_declined_valve_also_carries_the_divergence_caveat():
    # the controller asks for full heat all day; the position opens in the morning only, and the
    # discharge never rises: the position check declines, and the shut afternoons diverge
    fr = _box(open_rise=0.0, closed_rise=0.0, position="same")
    fr[Role.HEAT_VALVE] = np.where(
        (fr.index.dayofweek < 5) & (fr.index.hour >= 7) & (fr.index.hour < 18), 100.0, 0.0
    )
    f = ReheatPenalty().analyze("B", fr)
    assert f.metrics["declined"] and f.metrics["valve_signal"] == "position"
    assert f.metrics["valve_divergence_share"] >= 0.25
    assert any(_STUCK in c for c in f.caveats)


# --------------------------------------------------------------------------- fan heat


def test_default_fan_heat_leaves_the_bounds_as_they_were():
    f = ReheatPenalty().analyze("B", _box(open_rise=7.0))
    assert f.metrics["fan_heat_f"] is None
    assert f.metrics["valve_dat_consistency"] == "consistent" and f.severity == "fault"


def test_a_configured_fan_heat_raises_the_no_rise_bound():
    f = ReheatPenalty(fan_heat_f=3.0).analyze("B", _box(open_rise=7.0))
    assert f.metrics["fan_heat_f"] == 3.0
    assert f.metrics["declined"] and f.severity == "info"
    assert "< 8°F allowing 3°F of fan heat" in f.summary


def test_a_configured_fan_heat_raises_the_big_rise_bound():
    fr = _box(open_rise=25.0, closed_rise=11.0)
    assert ReheatPenalty().analyze("B", fr).metrics["valve_dat_consistency"] == "closed_with_rise"
    f = ReheatPenalty(fan_heat_f=3.0).analyze("B", fr)
    assert f.metrics["valve_dat_consistency"] == "consistent"


def test_auto_fan_heat_is_the_closed_valve_lift_and_declines_a_valve_that_adds_nothing():
    # a series box: its fan lifts the discharge 6 F with the valve shut; at full demand only 9 F
    fr = _box(open_rise=9.0, closed_rise=6.0)
    assert ReheatPenalty().analyze("B", fr).metrics["valve_dat_consistency"] == "consistent"
    f = ReheatPenalty(fan_heat_f="auto").analyze("B", fr)
    assert f.metrics["fan_heat_f"] == 6.0 and f.metrics["declined"]


def test_auto_fan_heat_is_clipped_and_needs_the_entering_air_and_closed_samples():
    assert (
        ReheatPenalty(fan_heat_f="auto")
        .analyze("B", _box(open_rise=30.0, closed_rise=14.0))
        .metrics["fan_heat_f"]
        == 8.0
    )
    no_entering = _box(open_rise=30.0, closed_rise=4.0, entering=False)
    assert ReheatPenalty(fan_heat_f="auto").analyze("B", no_entering).metrics["fan_heat_f"] is None
    always_open = _box(open_rise=20.0)
    always_open[Role.HEAT_VALVE] = 100.0
    assert ReheatPenalty(fan_heat_f="auto").analyze("B", always_open).metrics["fan_heat_f"] is None


def test_auto_fan_heat_uses_fan_on_samples_when_the_fan_status_is_mapped():
    fr = _box(open_rise=30.0, closed_rise=6.0)
    fr[Role.SUPPLY_FAN_STATUS] = 0.0  # the fan never runs: no fan-on closed-valve samples
    assert ReheatPenalty(fan_heat_f="auto").analyze("B", fr).metrics["fan_heat_f"] is None
    fr[Role.SUPPLY_FAN_STATUS] = 1.0
    assert ReheatPenalty(fan_heat_f="auto").analyze("B", fr).metrics["fan_heat_f"] == 6.0


@pytest.mark.parametrize("bad", [-1.0, "fixed", True, [3.0]])
def test_fan_heat_is_validated(bad):
    with pytest.raises(ValueError, match="fan_heat_f"):
        ReheatPenalty(fan_heat_f=bad)


def test_fan_heat_is_config_tunable():
    assert make_rule("reheat_penalty", fan_heat_f="auto").fan_heat_f == "auto"
    assert make_rule("reheat_penalty", fan_heat_f=4).fan_heat_f == 4.0


# --------------------------------------------------------------------------- overcooling_min_flow


def _overcooled(position=None):
    fr = _box(position=position)
    fr[Role.SPACE_TEMP] = 70.0
    fr[Role.COOL_SP] = 74.0
    fr[Role.AIRFLOW] = 200.0
    fr[Role.AIRFLOW_SP] = 200.0
    fr[Role.DAMPER] = 20.0
    return fr


def test_overcooling_reads_the_position_for_its_reheat_overlap():
    by_demand = OvercoolingMinFlow().analyze("B", _overcooled())
    assert by_demand.severity == "fault" and by_demand.metrics["valve_signal"] == "demand"
    f = OvercoolingMinFlow().analyze("B", _overcooled(position="shut"))
    assert f.metrics["valve_signal"] == "position"
    assert f.metrics["overcool_with_reheat_pct"] == 0.0 and f.severity == "ok"
    assert f.metrics["valve_divergence_share"] == 1.0
    assert any(_STUCK in c for c in f.caveats)


def test_overcooling_with_only_a_position_uses_it():
    fr = _overcooled(position="same").drop(columns=[Role.HEAT_VALVE])
    f = OvercoolingMinFlow().analyze("B", fr)
    assert f.metrics["valve_signal"] == "position" and f.severity == "fault"
    assert f.metrics["valve_divergence_share"] is None
