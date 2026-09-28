"""#16 (0.92): the copied-signal and mixed-air flow-balance checks feed sensor trust.

Synthetic fixtures only.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.base import Finding  # noqa: E402
from camber.rules.triage import link_findings, sensor_causes  # noqa: E402
from camber.sensorhealth import (  # noqa: E402
    frame_sensor_health,
    untrusted_roles,
)


def _idx(days=60):
    return pd.date_range("2025-03-03", periods=days * 24, freq="h")


def _ahu(days=60, seed=0):
    idx = _idx(days)
    n = len(idx)
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    oat = 50 + 15 * np.sin(2 * np.pi * t / 24) + rng.normal(0, 1.0, n)
    rat = 72 + 1.5 * np.sin(2 * np.pi * t / 24 + 1) + rng.normal(0, 0.3, n)
    sat = 56 + 1.0 * np.sin(2 * np.pi * t / 24 + 2) + rng.normal(0, 0.3, n)
    sa = 10000 + 1500 * np.sin(2 * np.pi * t / 24) + rng.normal(0, 100, n)
    f = 0.25 + 0.05 * np.sin(2 * np.pi * t / 48)
    oa = f * sa
    mat = f * oat + (1 - f) * rat + rng.normal(0, 0.3, n)
    return pd.DataFrame(
        {
            Role.OAT: np.round(oat, 2),
            Role.RETURN_AIR_TEMP: np.round(rat, 2),
            Role.SUPPLY_AIR_TEMP: np.round(sat, 2),
            Role.MIXED_AIR_TEMP: np.round(mat, 2),
            Role.AIRFLOW: np.round(sa, 1),
            Role.OA_AIRFLOW: np.round(oa, 1),
        },
        index=idx,
    )


def _checks(t, name):
    return [c for c in t.frame_checks if c.get("check") == name]


# --------------------------------------------------------------------------- copied signal


def test_a_return_air_that_copies_supply_air_is_blamed_and_scaled():
    fr = _ahu()
    k = len(fr) * 2 // 5  # the last 60 % of the window is a copy
    fr.iloc[k:, fr.columns.get_loc(Role.RETURN_AIR_TEMP)] = fr[Role.SUPPLY_AIR_TEMP].iloc[k:]
    h = frame_sensor_health(fr)
    rat, sat = h[Role.RETURN_AIR_TEMP], h[Role.SUPPLY_AIR_TEMP]
    assert "copied_signal" in rat.flags and "copied_signal" not in sat.flags
    (c,) = _checks(rat, "copied_signal")
    assert c["copy_of"] == "supply_air_temp" and c["blame"] == "level_shift"
    assert 0.55 <= c["share_of_samples"] <= 0.65
    assert rat.trust < 0.5 and rat.verdict == "untrusted"  # trust x (1 - share), capped
    # the runner's trust gate sees it too: RAT falls below the 0.5 bar, SAT does not
    assert untrusted_roles(fr, [Role.RETURN_AIR_TEMP, Role.SUPPLY_AIR_TEMP]) == [
        Role.RETURN_AIR_TEMP
    ]


def test_a_short_copy_only_caps_at_suspect_by_its_share():
    fr = _ahu()
    a, b = len(fr) // 2, len(fr) // 2 + 24 * 6  # six days identical
    fr.iloc[a:b, fr.columns.get_loc(Role.RETURN_AIR_TEMP)] = fr[Role.SUPPLY_AIR_TEMP].iloc[a:b]
    rat = frame_sensor_health(fr)[Role.RETURN_AIR_TEMP]
    assert "copied_signal" in rat.flags and rat.verdict == "suspect"
    assert _checks(rat, "copied_signal")[0]["blame"] == "level_shift"


def test_a_copy_spanning_the_window_blames_both_without_scaling():
    fr = _ahu()
    fr[Role.RETURN_AIR_TEMP] = fr[Role.SUPPLY_AIR_TEMP]
    h = frame_sensor_health(fr)
    for role in (Role.RETURN_AIR_TEMP, Role.SUPPLY_AIR_TEMP):
        t = h[role]
        assert "copied_signal" in t.flags
        assert _checks(t, "copied_signal")[0]["blame"] == "undetermined"
        assert 0.5 <= t.trust <= 0.75 and t.verdict == "suspect"


def test_no_copy_no_flag():
    h = frame_sensor_health(_ahu())
    assert not any("copied_signal" in t.flags for t in h.values())


# --------------------------------------------------------------------------- mixing balance


def test_a_biased_mixed_air_sensor_is_capped_and_its_set_flagged():
    fr = _ahu()
    base = frame_sensor_health(fr)
    assert not any("mixing_balance" in t.flags for t in base.values())
    fr[Role.MIXED_AIR_TEMP] = fr[Role.MIXED_AIR_TEMP] + 5.0
    h = frame_sensor_health(fr)
    mat = h[Role.MIXED_AIR_TEMP]
    assert "mixing_balance" in mat.flags and mat.trust <= 0.75 and mat.verdict == "suspect"
    (c,) = _checks(mat, "mixing_flow_balance")
    assert 4.0 < c["bias_f"] < 6.0
    for role in (Role.OAT, Role.RETURN_AIR_TEMP):
        assert "mixing_balance" in h[role].flags
        assert h[role].trust == base[role].trust  # flagged, not lowered
    assert "mixing_balance" not in h[Role.SUPPLY_AIR_TEMP].flags


def test_mixing_balance_needs_the_flows():
    fr = _ahu().drop(columns=[Role.OA_AIRFLOW])
    fr[Role.MIXED_AIR_TEMP] = fr[Role.MIXED_AIR_TEMP] + 5.0
    h = frame_sensor_health(fr)
    assert not any("mixing_balance" in t.flags for t in h.values())


def test_health_on_a_subset_marks_only_its_roles():
    fr = _ahu()
    fr[Role.MIXED_AIR_TEMP] = fr[Role.MIXED_AIR_TEMP] + 5.0
    # the runner's gate scores only a rule's required roles; checks still see the whole frame
    assert untrusted_roles(fr, [Role.MIXED_AIR_TEMP]) == []  # suspect, not untrusted


# --------------------------------------------------------------------------- triage


def test_flags_make_the_units_findings_conditional_on_that_unit_only():
    fr = _ahu()
    fr[Role.MIXED_AIR_TEMP] = fr[Role.MIXED_AIR_TEMP] + 5.0
    h1 = {k.value: v for k, v in frame_sensor_health(fr).items()}
    h2 = {k.value: v for k, v in frame_sensor_health(_ahu(seed=1)).items()}
    causes = sensor_causes([], trust={"AHU-1": h1, "AHU-2": h2})
    kinds = {(c.equip, c.roles[0]) for c in causes}
    assert ("AHU-1", "mixed_air_temp") in kinds and ("AHU-1", "oat") in kinds
    assert not any(c.equip == "AHU-2" for c in causes)
    assert not any(c.shared for c in causes)  # unit-local even on OAT
    detail = next(c.detail for c in causes if c.roles == ("mixed_air_temp",))
    assert "mixed-air flow balance fails" in detail and "MAT +" in detail

    class _Econ:
        name = "economizer_high_limit"
        roles_required = (Role.OAT, Role.MIXED_AIR_TEMP)
        roles_optional = ()

    fs = [
        Finding("economizer_high_limit", "AHU-1", "warn", {}, "AHU-1: econ"),
        Finding("economizer_high_limit", "AHU-2", "warn", {}, "AHU-2: econ"),
    ]
    issues = {
        i.equip: i
        for i in link_findings(
            fs, rules={"economizer_high_limit": _Econ()}, trust={"AHU-1": h1, "AHU-2": h2}
        )
    }
    assert issues["AHU-1"].conditional and not issues["AHU-2"].conditional


def test_copy_cause_names_the_copy():
    fr = _ahu()
    half = len(fr) // 2
    fr.iloc[half:, fr.columns.get_loc(Role.RETURN_AIR_TEMP)] = fr[Role.SUPPLY_AIR_TEMP].iloc[half:]
    h = {k.value: v for k, v in frame_sensor_health(fr).items()}
    causes = sensor_causes([], trust={"AHU-1": h})
    rat = [c for c in causes if c.roles == ("return_air_temp",)]
    assert len(rat) == 1  # untrusted: one cause, not two
    assert not any(c.roles == ("supply_air_temp",) for c in causes)
