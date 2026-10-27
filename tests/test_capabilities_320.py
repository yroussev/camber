"""0.111 (#320): the last acceptance-test gaps before the release.

1. ``check_citations`` returns the caller's whole ``verified_text`` (and sentences), never cut
   at the 2,000-character cap for values from the data; any value that is cut is said
   (``truncated`` and a notice).
2. A rule parameter of the wrong shape (null where it has a value by default, a list or a map
   where it takes one value, one value where it takes a list) is refused at launch, by
   ``camber run`` and in ``rule_run``'s ``params``, in the #315 wording.
3. ``report.rcx.references``: a warn level above the fault level and a value that is not an
   object are refused; a reference that cannot be compared is said in ``rule_run`` and
   ``rcx_report`` too.
4. The outdoor-air signal ``dcv_verification`` is judged on gates the check like a required
   input, although it is an optional role.
5. Only a fleet rule's ``<fleet>`` is the fleet: a unit stored as ``<fleet>`` is shown defanged,
   and ``finding_detail`` takes ``...@‹fleet›`` for the fleet only for a fleet rule.
"""

import json
import os
import sys
import types

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import (  # noqa: E402
    CallContext,
    Capability,
    CapabilityError,
    CapabilityResult,
    Registry,
    default_registry,
)
from camber.capabilities._sanitize import MAX_VALUE  # noqa: E402
from camber.config import _preflight_config, run_config  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.rules import base  # noqa: E402
from camber.store import ParquetStore  # noqa: E402
from camber.report.rcx import RcxOptions  # noqa: E402
from camber.rules.param_docs import _value_problem  # noqa: E402

FID = fx.FID

REG = default_registry()


def _ctx(root, allowed=("f",), **kw):
    return CallContext(store=str(root), allowed_facilities=frozenset(allowed), **kw)


# ------------------------------------------------------------- 1. check_citations' own text


def test_verified_text_is_the_whole_supported_draft(tmp_path):
    facts = [{"id": "F1@points-a1b2c3", "text": "AHU-2: 11 stored points on 1 equipment"}]
    sent = "AHU-2 has 11 stored points on 1 equipment [F1@points-a1b2c3]."
    text = " ".join([sent] * 60)
    assert len(text) > MAX_VALUE + 1000
    res = REG.call(
        "check_citations",
        {"text": text, "facts": facts, "detail": True, "limit": 100},
        _ctx(tmp_path, ()),
    )
    assert res.data["grounded"] is True and res.data["n_sentences"] == 60
    assert res.data["verified_text"] == text
    assert not res.truncated and res.banners == []


def test_a_long_sentence_comes_back_whole(tmp_path):
    facts = [{"id": "F1@points-a1b2c3", "text": "AHU-2: 11 stored points"}]
    long = "AHU-2 has 11 stored points " + "and more words " * 200 + "[F1@points-a1b2c3]."
    res = REG.call(
        "check_citations", {"text": long, "facts": facts, "detail": True}, _ctx(tmp_path, ())
    )
    assert res.data["sentences"][0]["text"] == long
    assert res.data["verified_text"] == long


def test_a_cut_value_from_the_data_is_said(tmp_path):
    reg = Registry()
    reg.register(
        Capability(
            name="probe",
            title="Probe",
            description="probe",
            input_schema={"type": "object"},
            output_schema={"type": "object"},
            handler=lambda ctx, a: CapabilityResult(
                data={"name": "B" * 3000, "ok": "short"},
                text="A probe.",
                facts=[],
            ),
        )
    )
    res = reg.call("probe", {}, _ctx(tmp_path, ()))
    assert len(res.data["name"]) == MAX_VALUE and res.data["ok"] == "short"
    assert res.truncated
    assert any("1 value(s) longer than 2,000 characters are cut" in b for b in res.banners)


# ------------------------------------------------------------- 2. parameter shapes


@pytest.fixture()
def store(tmp_path):
    return fx.make_store(tmp_path, meta=None)


def _config(rule, params):
    return {
        "source": {"kind": "store", "store": "store", "facility_id": FID},
        "equipment": [{"class": "AHU"}],
        "rules": [{"name": rule, "params": params}],
    }


_WRONG_SHAPE = [
    ("night_weekend_setback", {"start_hour": None}, "null is not a whole number"),
    ("night_weekend_setback", {"start_hour": [7]}, r"\[7\] is a list, not a whole number"),
    ("night_weekend_setback", {"start_hour": {"v": 7}}, "is a map, not a whole number"),
    ("night_weekend_setback", {"occupied_days": 3}, r"3 is not a list \(each item a whole"),
    ("night_weekend_setback", {"min_unoccupied_run_pct": None}, "null is not a number"),
    ("outdoor_air_fraction", {"min_oa_pct": [20]}, r"\[20\] is a list, not a number"),
    ("outdoor_air_fraction", {"min_oa_pct_by_month": [20.0] * 12}, "is a list, not a map"),
    ("outdoor_air_fraction", {"fan_gate": None}, "null is not true or false"),
]


@pytest.mark.parametrize(
    "rule, params, message", _WRONG_SHAPE, ids=[m for _r, _p, m in _WRONG_SHAPE]
)
def test_a_wrong_shape_is_refused_at_launch_and_by_a_run(store, tmp_path, rule, params, message):
    for check in (_preflight_config, run_config):
        with pytest.raises(ValueError, match=message) as e:
            check(_config(rule, params), base_dir=str(tmp_path))
        assert f"see `camber rules params {rule}`" in str(e.value)


def test_a_wrong_shape_in_rule_run_params_is_refused(store):
    ctx = CallContext(store=store.root, allowed_facilities=frozenset({FID}))
    for params, message in (
        ({"night_weekend_setback": {"min_unoccupied_run_pct": None}}, "null is not a number"),
        ({"night_weekend_setback": {"occupied_days": 3}}, "3 is not a list"),
        ({"outdoor_air_fraction": {"min_oa_pct": {"v": 7}}}, "is a map, not a number"),
    ):
        with pytest.raises(CapabilityError) as e:
            REG.call("rule_run", {"facility_id": FID, "rules": list(params), "params": params}, ctx)
        assert e.value.code == "invalid_params" and message in e.value.message
        assert "NoneType" not in e.value.message and "iterable" not in e.value.message


def test_the_shapes_a_parameter_takes_pass():
    ok = [
        ("night_weekend_setback", "unoccupied_heat_sp_f", None),  # its default is null
        ("night_weekend_setback", "occupied_days", [0, 1, 2]),
        ("dcv_verification", "oa_floor_cfm", {"ROOM-1": 100.0}),  # one number or a map
        ("dcv_verification", "oa_floor_cfm", 100.0),
        ("reheat_capacity_shortfall", "heat_sp_f", {"VAV-1": 70.0}),
        ("overcooling_severity", "tiers", {"info": 1.0, "warn": 2.0, "fault": 3.0}),
        ("overcooling_severity", "shortfall_share_pct", None),
        ("outdoor_air_fraction", "min_oa_pct_by_month", {"6": 12.0}),
    ]
    for rule, key, value in ok:
        assert _value_problem(rule, key, value) is None, (rule, key, value)


# ------------------------------------------------------------- 3. references


def _refs(spec, unit="DemoAHU", role="supply_air_temp"):
    return fx.config(references={unit: {role: spec}})


@pytest.mark.parametrize(
    "spec, message",
    [
        ({"equip": "DemoAHU2", "bias_warn": 5, "bias_fault": 1}, r"bias_warn \(5\) must not be"),
        ({"equip": "DemoAHU2", "bias_warn": 6}, r"bias_warn \(6\) must not be above bias_fault"),
        ({"equip": "DemoAHU2", "drift_fault": 0.5}, "drift_warn .* must not be above drift_fault"),
    ],
    ids=["both", "warn-over-default-fault", "fault-under-default-warn"],
)
def test_an_inverted_threshold_is_refused(store, tmp_path, spec, message):
    with pytest.raises(ValueError, match=message):
        _preflight_config(_refs(spec), base_dir=str(tmp_path))


@pytest.mark.parametrize("refs", [0, None, [], "x", 1.5], ids=repr)
def test_references_that_are_not_an_object_are_refused(store, tmp_path, refs):
    config = fx.config(references=refs)
    with pytest.raises(ValueError, match="report.rcx.references must map an equipment id"):
        _preflight_config(config, base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="report.rcx.references must map"):
        RcxOptions.from_config(config["report"], base_dir=str(tmp_path))


def test_empty_references_and_good_thresholds_pass(store, tmp_path):
    _preflight_config(fx.config(references={}), base_dir=str(tmp_path))
    spec = {"equip": "DemoAHU2", "bias_warn": 1.0, "bias_fault": 1.0, "drift_warn": 0.5}
    _preflight_config(_refs(spec), base_dir=str(tmp_path))


def test_a_reference_that_cannot_be_compared_is_said_by_rule_run_and_rcx_report(store, tmp_path):
    path = tmp_path / "cfg.json"
    path.write_text(json.dumps(_refs({"equip": "DemoAHU2", "role": "co2"})), encoding="utf-8")
    _preflight_config(json.loads(path.read_text()), base_dir=str(tmp_path))  # starts
    ctx = CallContext(store=store.root, allowed_facilities=frozenset({FID}), config=str(path))
    for cap in ("rule_run", "rcx_report"):
        res = REG.call(cap, {"facility_id": FID}, ctx)
        notice = [b for b in res.banners if "could not be compared" in b]
        assert len(notice) == 1, (cap, res.banners)
        assert notice[0].startswith("1 configured sensor reference(s) could not be compared")
        assert "DemoAHU supply_air_temp could not be loaded" in notice[0]
        assert "DemoAHU2" in notice[0] and "co2" in notice[0]
    other = REG.call("rule_run", {"facility_id": FID, "equips": ["DemoAHU2"]}, ctx)
    assert not [b for b in other.banners if "could not be compared" in b]


# ------------------------------------------------------------- 4. a needed optional input


def _gate(monkeypatch, rule, scores):
    health = {r: types.SimpleNamespace(trust=t) for r, t in scores.items()}
    monkeypatch.setattr(base, "_equip_health", lambda *a, **k: health)
    frame = pd.DataFrame({r: [1.0, 2.0] for r in scores})
    return base._untrusted(rule, types.SimpleNamespace(equip="AHU-1"), frame, None, "1h", 0.5)


def test_dcv_declines_on_the_untrusted_oa_signal_it_judges(monkeypatch):
    from camber.rules.ventilation_rule import DemandControlledVentilation

    rule = DemandControlledVentilation()
    bad = _gate(monkeypatch, rule, {Role.CO2: 0.9, Role.OA_AIRFLOW: 0.1, Role.OCCUPANCY: 0.9})
    assert bad == [(Role.OA_AIRFLOW, 0.1)]
    # the damper only fills the flow's gaps: its score alone does not decline the check
    scores = {Role.CO2: 0.9, Role.OA_AIRFLOW: 0.9, Role.OA_DAMPER: 0.1}
    assert _gate(monkeypatch, rule, scores) == []
    assert _gate(monkeypatch, rule, {Role.CO2: 0.9, Role.OA_DAMPER: 0.2}) == [(Role.OA_DAMPER, 0.2)]
    # a zone's CO2 alone (no OA signal) is gated on the CO2 only
    assert _gate(monkeypatch, rule, {Role.CO2: 0.9, Role.OCCUPANCY: 0.1}) == []
    full = DemandControlledVentilation(full_outdoor_air=True)
    assert _gate(monkeypatch, full, {Role.CO2: 0.9, Role.AIRFLOW: 0.1}) == [(Role.AIRFLOW, 0.1)]


def test_the_other_rules_gate_the_input_they_cannot_judge_without(monkeypatch):
    from camber.rules.builtin import builtin_registry

    reg = builtin_registry()
    oaf = reg.get("outdoor_air_fraction")
    temps = {Role.MIXED_AIR_TEMP: 0.9, Role.RETURN_AIR_TEMP: 0.9}
    assert _gate(monkeypatch, oaf, {**temps, Role.OAT: 0.1}) == [(Role.OAT, 0.1)]
    setback = reg.get("night_weekend_setback")
    assert _gate(monkeypatch, setback, {Role.SUPPLY_FAN_STATUS: 0.2}) == [
        (Role.SUPPLY_FAN_STATUS, 0.2)
    ]
    assert (
        _gate(monkeypatch, setback, {Role.SUPPLY_FAN_STATUS: 0.9, Role.SUPPLY_FAN_SPEED: 0.1}) == []
    )
    for name in ("condenser_water_reset", "cooling_tower_approach"):
        rule = reg.get(name)
        cws = {Role.CW_SUPPLY_TEMP: 0.9}
        assert _gate(monkeypatch, rule, {**cws, Role.WETBULB_TEMP: 0.1}) == [
            (Role.WETBULB_TEMP, 0.1)
        ]
        derived = {**cws, Role.OAT: 0.9, Role.OUTDOOR_RH: 0.1}
        assert _gate(monkeypatch, rule, derived) == [(Role.OUTDOOR_RH, 0.1)]
        # a wet-bulb point is read first: the OAT and RH are not used then
        assert _gate(monkeypatch, rule, {**cws, Role.WETBULB_TEMP: 0.9, Role.OAT: 0.1}) == []


# ------------------------------------------------------------- 5. a unit named <fleet>

GUILLEMETS = "\u2039fleet\u203a"


@pytest.fixture()
def fleet_named(tmp_path):
    st = ParquetStore(str(tmp_path / "store"))
    idx = pd.date_range("2025-01-06", periods=24 * 14, freq="h")
    occ = ((idx.hour >= 7) & (idx.hour < 18) & (idx.dayofweek < 5)).astype(float)
    rng = np.random.default_rng(1)
    for equip, fan in (("<fleet>", np.ones(len(idx))), (GUILLEMETS, occ)):
        frame = pd.DataFrame(
            {
                Role.OAT: 70 + rng.normal(0, 2, len(idx)),
                Role.SUPPLY_AIR_TEMP: 55 + rng.normal(0, 0.5, len(idx)),
                Role.SUPPLY_FAN_STATUS: fan,
            },
            index=idx,
        )
        st.write_role_frame(frame, facility_id="fl", equip=equip, equip_class="AHU")
    st.register_facility("fl", name="fleet names")
    return CallContext(store=st.root, allowed_facilities=frozenset({"fl"}))


def test_a_unit_named_fleet_is_not_the_fleet(fleet_named):
    ctx = fleet_named
    args = {"facility_id": "fl", "rules": ["night_weekend_setback"]}
    rr = REG.call("rule_run", {**args, "min_severity": "ok"}, ctx)
    by_id = {f["finding_id"]: f for f in rr.data["findings"]}
    assert set(by_id) == {"night_weekend_setback@<fleet>", f"night_weekend_setback@{GUILLEMETS}"}
    texts = " ".join(f["text"] for f in rr.facts)
    assert '"<fleet>"' not in texts and '"<fleet>"' not in rr.text
    for want in by_id:
        fd = REG.call("finding_detail", {**args, "finding_id": want}, ctx)
        assert fd.data["finding"]["finding_id"] == want
        assert fd.data["finding"]["severity"] == by_id[want]["severity"]
    assert (
        by_id["night_weekend_setback@<fleet>"]["severity"]
        != by_id[f"night_weekend_setback@{GUILLEMETS}"]["severity"]
    )
