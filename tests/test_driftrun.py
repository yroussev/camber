"""Tests for camber.driftrun — the drift family runner behind the config/CLI surface.

The load-bearing behaviour here is not "does it detect drift" (the per-rule and physics-sim tests
cover that) but the two honesty guards: a suite must be able to carry two same-named coil rules, and
an equipment that was never actually tested must never be reported as steady.
"""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.ahusim import build_ahu_suite, simulate_case  # noqa: E402
from camber.condensersim import build_condenser_suite  # noqa: E402
from camber.driftrun import (  # noqa: E402
    DRIFT_FAMILIES,
    build_drift_suite,
    family_names,
    run_drift,
)
from camber.driftsim import build_chiller_suite  # noqa: E402
from camber.evaporatorsim import build_evaporator_suite  # noqa: E402
from camber.model.mapping import MappingProvider  # noqa: E402
from camber.model.roles import Role  # noqa: E402
from camber.pumpsim import build_pump_suite  # noqa: E402
from camber.store.modelstore import BaselineStore  # noqa: E402
from camber.vavsim import build_vav_suite  # noqa: E402

_BASE = ("2025-05-01", "2025-05-30")
_CUR = ("2025-06-01", "2025-07-01")


# --------------------------------------------------------------------------- suite membership


@pytest.mark.parametrize(
    "family,legacy",
    [
        ("ahu", build_ahu_suite),
        ("chiller", build_chiller_suite),
        ("condenser", build_condenser_suite),
        ("evaporator", build_evaporator_suite),
        ("pump", build_pump_suite),
        ("vav", build_vav_suite),
    ],
)
def test_suite_matches_the_legacy_sim_builder(family, legacy):
    """build_drift_suite is the single source of truth: the *sim builders must agree with it.

    Same classes, same order — the physics-validation harnesses score the roll-up against those
    orderings, so a divergence here would silently change the accuracy gates.
    """
    new = [
        type(r).__name__
        for r in build_drift_suite(family, BaselineStore(), site="SIM", run_id="SIM")
    ]
    old = [type(r).__name__ for r in legacy(BaselineStore())]
    assert new == old


def test_unknown_family_raises_and_names_the_known_ones():
    with pytest.raises(KeyError) as ei:
        build_drift_suite("boiler", BaselineStore())
    for name in family_names():
        assert name in str(ei.value)
    assert sorted(DRIFT_FAMILIES) == family_names()


def test_freeze_if_missing_reaches_every_rule_in_the_suite():
    for family in family_names():
        suite = build_drift_suite(family, BaselineStore(), freeze_if_missing=False)
        assert {r.freeze_if_missing for r in suite} == {False}, family


def test_two_coils_produce_two_same_named_rules():
    """The AHU family registers cooling and heating CoilValveDrift under one name.

    A shared Registry would drop one (it keys on rule.name); the runner therefore gives each rule
    its own scratch Registry. This pins the collision that makes that necessary.
    """
    suite = build_drift_suite("ahu", BaselineStore(), coils=("cooling", "heating"))
    coil_rules = [r for r in suite if r.name == "coil_valve_drift"]
    assert len(coil_rules) == 2
    assert {r.coil for r in coil_rules} == {"cooling", "heating"}


def test_sustained_alarm_is_opt_in_for_the_chiller_family():
    names = lambda **kw: [r.name for r in build_drift_suite("chiller", BaselineStore(), **kw)]  # noqa: E731
    assert "chiller_approach_drift_sustained" not in names()
    assert names(sustained_alarm=True)[-1] == "chiller_approach_drift_sustained"


# --------------------------------------------------------------------------- running a family


def _write_point(folder, equip, measure, series):
    ts = series.index.strftime("%d-%b-%y %I:%M:%S %p") + " PDT"
    pd.DataFrame({"Timestamp": ts, "Value": series.values}).to_csv(
        os.path.join(folder, f"{equip}_{measure}.csv"), index=False
    )


def _token(role) -> str:
    value = role.value if isinstance(role, Role) else str(role)
    return "".join(p.capitalize() for p in value.split("_"))


def _make_site(tmp_path):
    """An AHU with an injected filter fault, a healthy one, and one carrying only a marker point."""
    trends = os.path.join(str(tmp_path), "trends")
    os.makedirs(trends, exist_ok=True)
    aliases = {}
    for equip, case in (
        ("AHU_1", simulate_case("filter_loading", 4, seed=3)),
        ("AHU_2", simulate_case(None, 0, seed=9)),
    ):
        frame = pd.concat([case.baseline, case.current])
        for col in frame.columns:
            aliases[_token(col)] = col.value if isinstance(col, Role) else str(col)
            _write_point(trends, equip, _token(col), frame[col])
    idx = pd.concat(
        [simulate_case(None, 0, seed=1).baseline, simulate_case(None, 0, seed=1).current]
    ).index
    _write_point(trends, "AHU_3", "Airflow", pd.Series(9000.0, index=idx))
    aliases["Airflow"] = "airflow"
    return trends, MappingProvider.from_dict({"aliases": aliases})


def _refs(trends):
    from camber.resolve import discover

    return {"AHU": discover([trends], "AHU", marker_measure="Airflow")}


def _run(tmp_path, store, **kw):
    trends, mapping = _make_site(tmp_path)
    return run_drift(
        _refs(trends),
        mapping,
        store=store,
        families=[{"class": "AHU", "family": "ahu"}],
        baseline=_BASE,
        current=_CUR,
        site="T",
        run_id="R",
        **kw,
    )


def test_drift_is_localized_and_the_healthy_unit_stays_steady(tmp_path):
    store = BaselineStore()
    _run(tmp_path, store, freeze_if_missing=True)  # establish the references
    res = _run(tmp_path, store)

    fam = res.families[0]
    by_equip = {d.equip: d for d in fam.diagnoses}
    assert by_equip["AHU_1"].severity in ("warn", "fault")
    assert by_equip["AHU_1"].locus == "air-path"
    assert by_equip["AHU_2"].severity == "ok"
    assert by_equip["AHU_2"].locus == "steady"


def test_equipment_with_no_resolvable_role_is_never_diagnosed(tmp_path):
    """AHU_3 carries only a marker point — untested is not steady."""
    store = BaselineStore()
    _run(tmp_path, store, freeze_if_missing=True)
    fam = _run(tmp_path, store).families[0]

    assert "AHU_3" not in {d.equip for d in fam.diagnoses}
    row = next(r for r in fam.unevaluated if r["equip"] == "AHU_3")
    assert row["reason"] == "no required role resolved"
    assert "power" in row["roles_required"]
    # the absence is a Finding, so it reaches findings.json and the audit report's caveats
    f = next(f for f in fam.findings if f.equip == "AHU_3")
    assert f.severity == "info" and f.metrics["declined"] is True
    assert any("AHU_3" in c for c in f.caveats)


def test_all_detectors_declining_is_not_a_verdict(tmp_path):
    """With nothing frozen, every detector declines — that must not roll up to ok/steady."""
    res = _run(tmp_path, BaselineStore())  # freeze_if_missing defaults to False
    fam = res.families[0]

    assert fam.diagnoses == []
    reasons = {r["equip"]: r["reason"] for r in fam.unevaluated}
    assert reasons["AHU_1"] == "every detector declined"
    assert reasons["AHU_2"] == "every detector declined"
    row = next(r for r in fam.unevaluated if r["equip"] == "AHU_1")
    assert "fan_efficiency_drift" in row["declined"]


def test_run_drift_never_writes_the_store(tmp_path):
    """freeze_if_missing defaults to False and nothing is saved: scoring must not mint one."""
    path = os.path.join(str(tmp_path), "baselines.json")
    store = BaselineStore(path)
    _run(tmp_path, store)
    assert store.records() == []
    assert not os.path.exists(path)


def test_empty_current_window_declines_rather_than_raising(tmp_path):
    store = BaselineStore()
    _run(tmp_path, store, freeze_if_missing=True)
    trends, mapping = _make_site(tmp_path)
    res = run_drift(
        _refs(trends),
        mapping,
        store=store,
        families=[{"class": "AHU", "family": "ahu"}],
        baseline=_BASE,
        current=("2030-01-01", "2030-02-01"),
        site="T",
        run_id="R",
    )
    fam = res.families[0]
    assert fam.diagnoses == []
    reasons = {r["equip"]: r["reason"] for r in fam.unevaluated}
    assert reasons["AHU_1"] == reasons["AHU_2"] == "every detector declined"
    assert any("empty_periods" in (f.metrics or {}) for f in fam.findings)


def test_as_dict_carries_the_threshold_confidence_block(tmp_path):
    store = BaselineStore()
    _run(tmp_path, store, freeze_if_missing=True)
    payload = _run(tmp_path, store).as_dict()

    tc = payload["threshold_confidence"]
    assert tc["magnitude_threshold_confidence"] == "screening-grade"
    assert tc["temporal_threshold_confidence"] == "provisional-untuned"
    assert payload["families"][0]["family"] == "ahu"
