"""#151 (0.105): the mixed-air "between outdoor and return air" check feeds sensor trust on a unit
without airflow stations, fan-gated, and is not double-counted in triage.

Synthetic fixtures only.
"""

import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from camber.model.roles import Role  # noqa: E402
from camber.rules.triage import sensor_causes  # noqa: E402
from camber.sensorhealth import frame_sensor_health, mixing_consistency  # noqa: E402


def _ahu(days=30, seed=0, *, stations=False, fan=True):
    """An AHU on a 06-19 schedule: OAT 35-65 F, RAT ~72 F, MAT their 25 % OA blend."""
    idx = pd.date_range("2025-03-03", periods=days * 24, freq="h")
    n = len(idx)
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    on = (idx.hour >= 6) & (idx.hour < 19)
    oat = 50 + 15 * np.sin(2 * np.pi * t / 24) + rng.normal(0, 1.0, n)
    rat = 72 + 1.5 * np.sin(2 * np.pi * t / 24 + 1) + rng.normal(0, 0.3, n)
    sat = 56 + 1.0 * np.sin(2 * np.pi * t / 24 + 2) + rng.normal(0, 0.3, n)
    f = 0.25 + 0.05 * np.sin(2 * np.pi * t / 48)
    mat = f * oat + (1 - f) * rat + rng.normal(0, 0.3, n)
    cols = {
        Role.OAT: np.round(oat, 2),
        Role.RETURN_AIR_TEMP: np.round(rat, 2),
        Role.SUPPLY_AIR_TEMP: np.round(sat, 2),
        Role.MIXED_AIR_TEMP: np.round(mat, 2),
    }
    if fan:
        cols[Role.SUPPLY_FAN_STATUS] = on.astype(float)
    if stations:
        sa = 10000 + 1500 * np.sin(2 * np.pi * t / 24) + rng.normal(0, 100, n)
        cols[Role.AIRFLOW] = np.round(sa, 1)
        cols[Role.OA_AIRFLOW] = np.round(f * sa, 1)
    return pd.DataFrame(cols, index=idx)


def _running(fr):
    return fr[Role.SUPPLY_FAN_STATUS] > 0.5


def _checks(t, name):
    return [c for c in t.frame_checks if c.get("check") == name]


def test_a_mixed_air_outside_oat_rat_is_capped_and_its_set_flagged():
    fr = _ahu()
    base = frame_sensor_health(fr)
    assert not any("mixing_order" in t.flags for t in base.values())
    assert base[Role.MIXED_AIR_TEMP].trust > 0.75
    fr[Role.MIXED_AIR_TEMP] = fr[Role.RETURN_AIR_TEMP] + 10.0  # above both, always
    h = frame_sensor_health(fr)
    mat = h[Role.MIXED_AIR_TEMP]
    assert "mixing_order" in mat.flags and mat.trust <= 0.75 and mat.verdict == "suspect"
    (c,) = _checks(mat, "mixing_temperature_order")
    assert c["severity"] == "fault" and c["violation_frac"] == 1.0
    assert c["gate"] == "fan status" and c["n_checked"] == int(_running(fr).sum())
    for role in (Role.OAT, Role.RETURN_AIR_TEMP):
        assert "mixing_order" in h[role].flags
        assert h[role].trust == base[role].trust  # flagged, not lowered
    assert "mixing_order" not in h[Role.SUPPLY_AIR_TEMP].flags
    assert "mixing_balance" not in mat.flags


def test_a_warn_share_caps_too():
    fr = _ahu()
    run = np.flatnonzero(_running(fr).to_numpy())
    bad = run[: int(0.08 * len(run))]  # 8 % of the running samples: warn, not fault
    fr.iloc[bad, fr.columns.get_loc(Role.MIXED_AIR_TEMP)] = 95.0
    mat = frame_sensor_health(fr)[Role.MIXED_AIR_TEMP]
    assert "mixing_order" in mat.flags and mat.verdict == "suspect"
    assert _checks(mat, "mixing_temperature_order")[0]["severity"] == "warn"


def test_fan_off_samples_are_ignored():
    fr = _ahu()
    off = ~_running(fr)
    fr.loc[off, Role.MIXED_AIR_TEMP] = 95.0  # parked while the fan is off
    assert mixing_consistency(fr).severity == "fault"  # an ungated read would fault it
    for gate in (None, "fan", _running(fr)):
        h = frame_sensor_health(fr, gate=gate)
        assert not any("mixing_order" in t.flags for t in h.values()), gate


def test_a_caller_gate_is_used():
    fr = _ahu()
    off = ~_running(fr)
    fr.loc[off, Role.MIXED_AIR_TEMP] = 95.0
    everything = pd.Series(True, index=fr.index)  # a gate that says the fan always ran
    mat = frame_sensor_health(fr, gate=everything)[Role.MIXED_AIR_TEMP]
    assert "mixing_order" in mat.flags
    assert _checks(mat, "mixing_temperature_order")[0]["gate"] == "caller-supplied fan gate"


def test_no_fan_signal_skips_the_check():
    fr = _ahu(fan=False)
    fr[Role.MIXED_AIR_TEMP] = fr[Role.RETURN_AIR_TEMP] + 10.0
    h = frame_sensor_health(fr)
    assert not any("mixing_order" in t.flags for t in h.values())


def test_with_airflow_stations_the_balance_runs_and_the_order_does_not():
    fr = _ahu(stations=True)
    fr[Role.MIXED_AIR_TEMP] = fr[Role.MIXED_AIR_TEMP] + 5.0  # fails the balance, inside [OAT, RAT]
    h = frame_sensor_health(fr)
    assert "mixing_balance" in h[Role.MIXED_AIR_TEMP].flags
    fr[Role.MIXED_AIR_TEMP] = fr[Role.RETURN_AIR_TEMP] + 10.0  # out of order as well
    h = frame_sensor_health(fr)
    assert not any("mixing_order" in t.flags for t in h.values())
    # one station alone is not enough for the balance: the order check takes over
    h = frame_sensor_health(fr.drop(columns=[Role.OA_AIRFLOW]))
    assert "mixing_order" in h[Role.MIXED_AIR_TEMP].flags
    assert "mixing_balance" not in h[Role.MIXED_AIR_TEMP].flags


def test_triage_counts_the_unit_once():
    fr = _ahu()
    fr[Role.MIXED_AIR_TEMP] = fr[Role.RETURN_AIR_TEMP] + 10.0
    h = {k.value: v for k, v in frame_sensor_health(fr, gate="fan").items()}
    # the trust flag alone: one unit-local cause per flagged point
    causes = sensor_causes([], trust={"AHU-1": h})
    assert sorted(c.roles[0] for c in causes) == ["mixed_air_temp", "oat", "return_air_temp"]
    assert all(c.kind == "trust" and not c.shared for c in causes)
    mat = next(c for c in causes if c.roles == ("mixed_air_temp",))
    assert "outside [OAT, RAT] 100% of fan-on samples" in mat.detail
    # with the RCx mixing result firing too, the mixing cause stands for both
    mixing = {"AHU-1": mixing_consistency(fr[_running(fr).to_numpy()])}
    causes = sensor_causes([], trust={"AHU-1": h}, mixing=mixing)
    assert [c.kind for c in causes] == ["mixing"]
    assert causes[0].roles == ("mixed_air_temp", "oat", "return_air_temp")
    # an ok mixing result does not suppress the trust causes
    ok = {"AHU-1": mixing_consistency(_ahu())}
    assert ok["AHU-1"].severity == "ok"
    assert len(sensor_causes([], trust={"AHU-1": h}, mixing=ok)) == 3
