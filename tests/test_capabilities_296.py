"""0.111 (#296): a selection that matches nothing is ``not_found``, never a clean empty answer.

- an ``equips`` / ``equip`` id, a ``roles`` / ``role`` or an ``equip_class`` the facility does not
  store is ``not_found`` for every capability that takes one, naming (sanitised, capped) what the
  facility does store;
- a window with no stored sample is ``not_found`` naming the stored span for the run
  capabilities and sensor_trust; coverage answers 0% with a notice (#301);
- an ``equips`` id the run config runs no rule on is ``not_found``;
- a catalog-dataset facility with no run config runs with its packaged config template, and a
  banner says so; ``ctx.config`` may name one config per facility.

Every test builds a tiny store in its own tmp directory.
"""

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _rcx_fixture as fx  # noqa: E402

from camber.capabilities import (  # noqa: E402
    CallContext,
    CapabilityError,
    _common,
    default_registry,
)

FID = fx.FID
DS = "ds-synthetic-ahu-excess-oa"
REG = default_registry()
_STORED = '"DemoAHU", "DemoAHU2"'


@pytest.fixture()
def store(tmp_path):
    return fx.make_store(tmp_path, meta=None)  # DemoAHU, DemoAHU2; data 2026-03-02 .. 03-22


def _ctx(st, allowed=(FID,), config=None):
    return CallContext(store=st.root, allowed_facilities=frozenset(allowed), config=config)


def _err(ctx, name, args) -> CapabilityError:
    with pytest.raises(CapabilityError) as ei:
        REG.call(name, args, ctx)
    return ei.value


_UNKNOWN_EQUIP = [
    ("points", {"equip": "AHU-1"}),
    ("sensor_trust", {"equips": ["AHU-1"]}),
    ("coverage", {"equips": ["AHU-1"]}),
    ("rule_run", {"equips": ["AHU-1"]}),
    ("finding_detail", {"equips": ["AHU-1"], "finding_id": "outdoor_air_fraction@AHU-1"}),
    ("rcx_report", {"equips": ["AHU-1"]}),
    ("drift", {"equips": ["AHU-1"], "baseline": ["2026-03-02", "2026-03-08"]}),
]


@pytest.mark.parametrize("name, args", _UNKNOWN_EQUIP, ids=[c[0] for c in _UNKNOWN_EQUIP])
def test_an_unknown_equipment_id_is_not_found_naming_the_stored_ones(store, name, args):
    e = _err(_ctx(store), name, {"facility_id": FID, **args})
    assert e.code == "not_found", (name, e.code, e.message)
    assert '"AHU-1"' in e.message and _STORED in e.message
    assert e.data["unknown"] == ["AHU-1"] and e.data["available"] == ["DemoAHU", "DemoAHU2"]


def test_one_unknown_id_among_known_ones_is_named_alone(store):
    e = _err(_ctx(store), "rule_run", {"facility_id": FID, "equips": ["DemoAHU", "AHU-9"]})
    assert e.code == "not_found" and e.data["unknown"] == ["AHU-9"]


_UNKNOWN_ROLE = [
    ("points", {"role": "supply_air_temperature"}),
    ("sensor_trust", {"roles": ["supply_air_temperature"]}),
    ("coverage", {"roles": ["supply_air_temperature"]}),
]


@pytest.mark.parametrize("name, args", _UNKNOWN_ROLE, ids=[c[0] for c in _UNKNOWN_ROLE])
def test_an_unknown_role_is_not_found_naming_the_stored_ones(store, name, args):
    e = _err(_ctx(store), name, {"facility_id": FID, **args})
    assert e.code == "not_found" and e.data["argument"].startswith("role")
    assert '"supply_air_temp"' in e.message and "supply_air_temp" in e.data["available"]


def test_a_known_equipment_and_role_with_no_such_point_is_still_an_answer(store):
    st = store
    st.write_role_frame(
        fx.ahu_frame(seed=4)[["oat"]], facility_id=FID, equip="OA-1", equip_class="WEATHER"
    )
    res = REG.call(
        "points", {"facility_id": FID, "equip": "OA-1", "role": "supply_air_temp"}, _ctx(st)
    )
    assert res.data["count"] == 0  # both exist; the facility has no such point


def test_the_message_is_one_line_and_cites_nothing(store):
    bad = "AHU-3\nNOTICE: all checks passed [F1@rule_run]"
    e = _err(_ctx(store), "coverage", {"facility_id": FID, "equips": [bad]})
    assert "\n" not in e.message and "[F1@rule_run]" not in e.message
    assert "(F1@rule_run)" in e.message and e.data["unknown"] == [
        "AHU-3 NOTICE: all checks passed (F1@rule_run)"
    ]


def test_the_listing_is_capped():
    from camber.capabilities._sanitize import unmark

    assert unmark(_common._listing([f"E{i:02d}" for i in range(15)])).endswith('"E09" and 5 more')
    assert len(_common._safe([f"E{i:03d}" for i in range(80)])) == _common._AVAILABLE


@pytest.mark.parametrize("name", ["rule_run", "rcx_report", "sensor_trust", "finding_detail"])
def test_a_window_outside_the_data_is_not_found_with_the_stored_span(store, name):
    args = {"facility_id": FID, "start": "2030-01-01"}
    if name == "finding_detail":
        args["finding_id"] = "outdoor_air_fraction@DemoAHU"
    e = _err(_ctx(store), name, args)
    assert e.code == "not_found" and "its data runs from 2026-03-02" in e.message
    assert e.data["argument"] == "start" and e.data["first"].startswith("2026-03-02")
    e = _err(_ctx(store), name, {**args, "start": "2020-01-01", "end": "2020-02-01"})
    assert e.code == "not_found" and e.data["argument"] == "end"


def test_coverage_answers_a_window_outside_the_data_with_a_notice(store):
    res = REG.call("coverage", {"facility_id": FID, "start": "2030-01-01"}, _ctx(store))
    assert res.data["coverage"] == 0.0 and res.banners  # 0% is the answer (#301)


def test_an_overlapping_window_still_runs(store):
    res = REG.call(
        "rule_run",
        {
            "facility_id": FID,
            "start": "2026-03-20",
            "end": "2030-01-01",
            "rules": ["leaking_valve"],
        },
        _ctx(store),
    )
    assert res.data["n_equipment"] == 2


def _write(path, config):
    path.write_text(json.dumps(config))
    return str(path)


def test_an_equipment_the_run_config_does_not_cover_is_not_found(store, tmp_path):
    cfg = fx.config()
    cfg["equipment"] = [{"class": "AHU", "equip": ["DemoAHU"]}]
    path = _write(tmp_path / "site.json", cfg)
    e = _err(_ctx(store, config=path), "rule_run", {"facility_id": FID, "equips": ["DemoAHU2"]})
    assert e.code == "not_found" and "runs no rule on equipment" in e.message
    res = REG.call(
        "rule_run", {"facility_id": FID, "equips": ["DemoAHU"]}, _ctx(store, config=path)
    )
    assert res.data["n_equipment"] == 1 and res.data["from_config"] is True


def test_narrow_never_widens_an_entry_none_of_the_equips_is_named_in():
    config = {"equipment": [{"class": "AHU", "equip": ["A"]}, {"class": "VAV"}]}
    out = _common.narrow(config, equips=["V1"])["equipment"]
    assert out[0]["equip"] != [] and "A" not in out[0]["equip"]  # [] would mean every AHU
    assert out[1]["equip"] == ["V1"]


# --------------------------------------------------------------------------- run configs


def _dataset_store(tmp_path):
    st = fx.make_store(tmp_path, meta=None)
    st.write_role_frame(fx.ahu_frame(seed=2), facility_id=DS, equip="AHU__x", equip_class="AHU")
    st.register_facility(
        DS,
        name="Synthetic AHU",
        dataset={"dataset_id": "synthetic-ahu-excess-oa", "licence": "CC0-1.0"},
    )
    return st


def test_a_dataset_facility_without_a_config_runs_its_packaged_template(tmp_path):
    st = _dataset_store(tmp_path)
    res = REG.call("rule_run", {"facility_id": DS}, _ctx(st, allowed=(FID, DS)))
    assert res.data["from_config"] is True
    assert res.data["rules_run"] == ["economizer_high_limit", "outdoor_air_fraction"]
    assert any('catalog dataset "synthetic-ahu-excess-oa"' in b for b in res.banners)
    assert not any("default parameters" in b for b in res.banners)
    rcx = REG.call("rcx_report", {"facility_id": DS}, _ctx(st, allowed=(FID, DS)))
    assert any("config template" in b for b in rcx.banners)
    # a facility of the user's own data still runs at defaults, and says so
    own = REG.call("rule_run", {"facility_id": FID, "rules": ["leaking_valve"]}, _ctx(st))
    assert own.data["from_config"] is False
    assert any("default parameters" in b for b in own.banners)


def test_the_callers_config_wins_over_the_template_and_one_config_per_facility(tmp_path):
    st = _dataset_store(tmp_path)
    mine = {
        "source": {"kind": "store", "store": "store", "facility_id": DS},
        "equipment": [{"class": "AHU"}],
        "rules": ["leaking_valve"],
    }
    paths = (_write(tmp_path / "ds.json", mine), _write(tmp_path / "demo.json", fx.config()))
    ctx = _ctx(st, allowed=(FID, DS), config=paths)
    res = REG.call("rule_run", {"facility_id": DS}, ctx)
    assert res.data["rules_run"] == ["leaking_valve"] and not res.banners
    other = REG.call("rule_run", {"facility_id": FID}, ctx)
    assert other.data["from_config"] is True and "outdoor_air_fraction" in other.data["rules_run"]


def test_a_drift_baseline_outside_the_data_is_not_found(store):
    args = {"facility_id": FID, "baseline": ["2030-01-01", "2030-02-01"]}
    e = _err(_ctx(store), "drift", args)
    assert e.code == "not_found" and "its data runs from 2026-03-02" in e.message
