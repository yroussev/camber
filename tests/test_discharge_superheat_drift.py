"""Discharge-superheat drift detector (0.93, closes the #6 deferral)."""

import dataclasses
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from test_dx_refrigerant_rules import _unit  # noqa: E402

from camber import refrigerant as R  # noqa: E402
from camber.driftrun import build_drift_suite  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.resolve import discover_store  # noqa: E402
from camber.rules.base import Registry  # noqa: E402
from camber.rules.builtin import builtin_registry  # noqa: E402
from camber.rules.dx_discharge_superheat_rule import DischargeSuperheatDrift  # noqa: E402
from camber.store import ParquetStore  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402


def test_discharge_superheat_two_sided():
    base = _unit(seed=1)
    up = DischargeSuperheatDrift(BaselineStore()).analyze_periods(
        "HP1", base, _unit(seed=2, dsh_shift=15.0)
    )
    assert up.severity == "fault" and up.metrics["discharge_superheat_drift_direction"] == "up"
    assert "too little" in up.summary
    down = DischargeSuperheatDrift(BaselineStore()).analyze_periods(
        "HP1", base, _unit(seed=3, dsh_shift=-12.0)
    )
    assert down.severity == "fault" and "liquid" in down.summary
    ok = DischargeSuperheatDrift(BaselineStore()).analyze_periods("HP1", base, _unit(seed=4))
    assert ok.severity == "ok"
    assert "discharge_superheat_sustained_alarm" in ok.metrics


def test_discharge_superheat_declines_without_the_point():
    frame = _unit().drop(columns=[Role.DISCHARGE_SUPERHEAT_TEMP])
    f = DischargeSuperheatDrift().analyze_periods("HP1", frame, frame)
    assert f.severity == "info" and f.metrics["reason"] == "discharge_superheat_not_mapped"
    no_oat = _unit().drop(columns=[Role.OAT])
    assert (
        DischargeSuperheatDrift().analyze_periods("HP1", no_oat, no_oat).metrics["reason"]
        == "no_normalizer"
    )
    kind, load, metric = DischargeSuperheatDrift().drift_signature()
    assert kind == "discharge_superheat" and metric is Role.DISCHARGE_SUPERHEAT_TEMP
    assert not DischargeSuperheatDrift().drift_frame(_unit()).empty


def test_run_periods_through_the_registry_with_derived_discharge_superheat(tmp_path):
    """Pressures + a discharge-line temperature + a named refrigerant feed the #6 detector."""
    frame = _unit(n=480, seed=1)
    p_dis = 300.0 + 2.0 * (frame[Role.OAT] - 75.0)
    t_dew = R.saturation_temp(p_dis, "R-410A", point="dew")
    frame[Role.DISCHARGE_PRESSURE] = p_dis
    frame[Role.DISCHARGE_LINE_TEMP] = t_dew + frame[Role.DISCHARGE_SUPERHEAT_TEMP]
    frame = frame.drop(columns=[Role.DISCHARGE_SUPERHEAT_TEMP])
    frame.loc[frame.index[240:], Role.DISCHARGE_LINE_TEMP] += 15.0  # the current window runs hot
    store = ParquetStore(str(tmp_path / "s"))
    store.write_role_frame(frame, facility_id="f", equip="HP1", equip_class="HEAT_PUMP")
    ref = dataclasses.replace(discover_store(store, "f", "HEAT_PUMP")[0], refrigerant="R-410A")
    reg = Registry()
    reg.register(DischargeSuperheatDrift(BaselineStore()))
    idx = frame.index
    out = reg.run_periods(
        "discharge_superheat_drift",
        [ref],
        None,
        baseline=(idx[0], idx[239]),
        current=(idx[240], idx[-1]),
    )
    assert out and out[0].severity == "fault"
    assert out[0].metrics["discharge_superheat_drift_f"] == pytest.approx(15.0, abs=1.0)


def test_dx_family_carries_the_detector_and_the_registry_does_not():
    names = [r.name for r in build_drift_suite("dx", BaselineStore())]
    assert names[-1] == "discharge_superheat_drift"
    assert "discharge_superheat_drift" not in builtin_registry().names()  # needs a store
